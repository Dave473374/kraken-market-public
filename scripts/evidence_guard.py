"""Public-evidence validation only. Never authorize a trade or retail Convert quote."""
import math
from datetime import datetime, timezone


def obj(value):
    return value if isinstance(value, dict) else {}


def fresh(value, now=None, max_age=600):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        return stamp.tzinfo is not None and 0 <= age <= max_age
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def book(bid, ask):
    try:
        return (not isinstance(bid, bool) and not isinstance(ask, bool)
                and math.isfinite(float(bid)) and math.isfinite(float(ask))
                and 0 < float(bid) <= float(ask))
    except (TypeError, ValueError, OverflowError):
        return False


def normalize(pair, aliases=None):
    p = str(pair or '').strip().upper().replace('/', '')
    return (aliases or {}).get(p, p)


def candidate_status(result, pair, schema, aliases=None, now=None):
    """Check provenance freshness and identity; 2% is a universal conflict ceiling.

    Liquid actions still require the separate 1% action check, independent provider,
    actual tier/notional/fees and all strategy/risk gates. DATA_OK is not BUY.
    """
    result = obj(result)
    body = obj(result.get('body'))
    meta = obj(body.get('pair_metadata'))
    spread, depth = obj(body.get('timestamped_spread')), obj(body.get('depth'))
    ss, ds = obj(spread.get('source')), obj(depth.get('source'))
    reasons = []
    pair_ok = bool(pair) and normalize(meta.get('altname') or meta.get('pair_key'), aliases) == normalize(pair, aliases)
    if result.get('ok') is not True:
        reasons.append(result.get('error') or 'SOURCE_FETCH_FAILED')
    if body.get('schema') != schema:
        reasons.append('SCHEMA_MISSING_OR_CONFLICT')
    if body.get('data_health') != 'DATA_OK' or body.get('pair_error'):
        reasons.append('SOURCE_PARTIAL_OR_PAIR_ERROR')
    if not pair_ok:
        reasons.append('PAIR_IDENTITY_MISSING_OR_CONFLICT')
    for label, value in [('generated_at', body.get('generated_at')),
                         ('spread_observed_at', spread.get('observed_at')),
                         ('spread_retrieved_at', ss.get('retrieved_at')),
                         ('depth_retrieved_at', ds.get('retrieved_at'))]:
        if not fresh(value, now):
            reasons.append('STALE_OR_MISSING_' + label.upper())
    valid_books = (book(spread.get('bid'), spread.get('ask'))
                   and book(depth.get('best_bid'), depth.get('best_ask')))
    if not valid_books or spread.get('quality') != 'VERIFIED' or depth.get('quality') != 'VERIFIED' or ss.get('ok') is not True or ds.get('ok') is not True:
        reasons.append('BOOK_EVIDENCE_MISSING_OR_INVALID')
    mismatch = None
    if valid_books:
        mids = [(float(spread['bid']) + float(spread['ask'])) / 2,
                (float(depth['best_bid']) + float(depth['best_ask'])) / 2]
        mismatch = 100 * abs(mids[0] - mids[1]) / min(mids)
        if mismatch > 2:
            reasons.append('SAME_PROVIDER_QUOTE_CONFLICT_GT_2PCT')
    good = not reasons
    return {'data_health': 'DATA_OK' if good else 'PARTIAL_DATA',
            'source_data_health': body.get('data_health'), 'pair_matches': pair_ok,
            'fresh_quote_evidence': good, 'blocking_data_reasons': reasons,
            'spread_observed_at': spread.get('observed_at'),
            'spread_source_retrieved_at': ss.get('retrieved_at'),
            'depth_source_retrieved_at': ds.get('retrieved_at'),
            'best_bid': depth.get('best_bid'), 'best_ask': depth.get('best_ask'),
            'same_provider_mismatch_pct': mismatch,
            'liquid_1pct_check_passed': mismatch is not None and mismatch <= 1,
            'error': result.get('error'), 'pair_error': body.get('pair_error'),
            'trade_signal': None, 'independent_crosscheck': 'NOT_PROVIDED',
            'retail_convert_executable': 'NOT_VERIFIED'}


def universe_status(result, schema, now=None):
    """PARTIAL ticker universe is discovery support, never executable evidence."""
    result, reasons = obj(result), []
    body = obj(result.get('body'))
    if result.get('ok') is not True or body.get('schema') != schema:
        reasons.append('UNIVERSE_TRANSPORT_OR_SCHEMA')
    if not fresh(body.get('generated_at'), now):
        reasons.append('UNIVERSE_GENERATED_AT_STALE_OR_MISSING')
    for name in ('metadata', 'ticker'):
        source = obj(obj(body.get('source_times')).get(name))
        if source.get('ok') is not True or not fresh(source.get('retrieved_at'), now):
            reasons.append('UNIVERSE_' + name.upper() + '_STALE_OR_MISSING')
    if not isinstance(body.get('candidates'), list):
        reasons.append('UNIVERSE_CANDIDATES_MISSING')
    return {'research_usable': not reasons, 'blocking_data_reasons': reasons,
            'executable_evidence': False}
