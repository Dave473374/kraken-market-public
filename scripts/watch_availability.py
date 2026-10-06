"""Fresh requested-pair eligibility result, never a price or whole-asset verdict."""
from urllib.parse import urlsplit, parse_qs
from evidence_guard import obj, normalize, fresh


def unavailable_pair_evidence(result, pair, schema, aliases=None, now=None):
    """Recognize only exact Kraken Unknown asset pair for the requested SI filter.

    A partial price/candle request, network failure, stale error, other country,
    or different pair remains UNKNOWN. This does not rule out an alternative
    quote or authorize a trade, nor does it mask missing owned-risk evidence.
    """
    result = obj(result)
    body = obj(result.get('body'))
    source = obj(body.get('source'))
    try:
        url = urlsplit(source.get('source_url', ''))
        query = parse_qs(url.query, keep_blank_values=True)
        pair_values = query.get('pair', [])
        exact_source = (url.scheme == 'https' and url.netloc == 'api.kraken.com'
            and url.path == '/0/public/AssetPairs' and not url.fragment
            and len(pair_values) == 1 and bool(pair)
            and normalize(pair_values[0], aliases) == normalize(pair, aliases)
            and query.get('country_code') == ['SI']
            and query.get('aclass_base') == ['currency']
            and query.get('execution_venue') == ['international'])
    except (ValueError, TypeError, AttributeError):
        exact_source = False
    verified = bool(result.get('ok') is True and body.get('schema') == schema
        and body.get('data_health') == 'DATA_UNAVAILABLE'
        and body.get('stage') == 'AssetPairs'
        and source.get('method') == 'GET' and source.get('http_status') == 200
        and source.get('ok') is False and source.get('status') == 'KRAKEN_API_ERROR'
        and source.get('errors') == ['EQuery:Unknown asset pair']
        and exact_source and fresh(body.get('generated_at'), now)
        and fresh(source.get('retrieved_at'), now))
    return {'quality': 'VERIFIED' if verified else 'UNKNOWN',
        'status': 'PAIR_UNAVAILABLE_FOR_REQUESTED_FILTERS' if verified else 'UNKNOWN',
        'requested_pair': pair, 'source_url': source.get('source_url') if verified else None,
        'source_retrieved_at': source.get('retrieved_at') if verified else None,
        'fresh_quote_evidence': False, 'whole_asset_availability': 'NOT_DETERMINED',
        'account_specific_eligibility': 'NOT_CHECKED', 'trade_signal': None}


def watchlist_status(pairs, statuses, availability):
    """Completed eligibility checks are not outages, but absent pairs lack prices."""
    unavailable = []
    for pair in pairs:
        state = statuses.get(pair, {})
        if state.get('fresh_quote_evidence') is True and state.get('closed_4h_current') is True:
            continue
        evidence = availability.get(pair, {})
        if evidence.get('quality') == 'VERIFIED' and evidence.get('status') == 'PAIR_UNAVAILABLE_FOR_REQUESTED_FILTERS':
            unavailable.append(pair)
        else:
            return 'PARTIAL_DATA'
    return 'CHECKED_WITH_UNAVAILABLE_PAIRS' if unavailable else 'DATA_OK'
