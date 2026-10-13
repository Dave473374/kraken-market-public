"""Public price inventory only. No holdings, cash, orders or private API calls.

Reuse the main candidate books and collect only five extra EUR books with the
same budgeted Kraken client. A missing price is explicit, never silently zero.
"""
import json
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

REVISION = '1.7-complete-valuation-evidence'
EXTRA_PAIRS = ('ZECEUR', 'BCHEUR', 'ADAEUR', 'XRPEUR', 'USDCEUR')
ALIASES = {'XBT': 'BTC', 'XDG': 'DOGE'}


def asset_of(pair):
    if not isinstance(pair, str) or not pair.endswith('EUR') or not pair[:-3].isalnum():
        raise ValueError('Expected an explicit EUR pair')
    return ALIASES.get(pair[:-3], pair[:-3])


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a price')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid price')
    return value


def source_ok(source, at):
    try:
        parsed = datetime.fromisoformat(source['retrieved_at'].replace('Z', '+00:00'))
        url = urlsplit(source['source_url'])
        headers = source.get('http_metadata', {})
        date = parsedate_to_datetime(headers['Date'])
        age = float(headers.get('Age', 0))
        return (source.get('ok') is True and source.get('method') == 'GET'
                and source.get('http_status') == 200 and parsed.tzinfo is not None
                and date.tzinfo is not None and math.isfinite(age) and 0 <= age <= 600
                and 0 <= at - parsed.timestamp() <= 600
                and -2 <= at - date.timestamp() <= 600
                and url.scheme == 'https' and url.hostname == 'api.kraken.com'
                and url.path in ('/0/public/AssetPairs', '/0/public/Depth'))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def price_row(pair, metadata, metadata_source, depth, at):
    base = asset_of(pair)
    missing = {'pair': pair, 'asset': base, 'quote': 'EUR', 'quality': 'MISSING',
               'reason': 'IDENTITY_OR_FRESH_L2_EVIDENCE_MISSING', 'trade_signal': None}
    try:
        wsbase, wsquote = metadata['wsname'].split('/')
        if (ALIASES.get(wsbase, wsbase) != base or wsquote != 'EUR'
                or metadata.get('status') != 'online'
                or metadata.get('aclass_base') != 'currency'
                or metadata.get('execution_venue') != 'international'
                or metadata.get('country_filter') != 'SI'
                or not source_ok(metadata_source, at)
                or depth.get('quality') != 'VERIFIED'
                or not source_ok(depth['source'], at)
                or depth['source']['source_url'].split('?')[0] != 'https://api.kraken.com/0/public/Depth'):
            return missing
        bid, ask = number(depth['best_bid']), number(depth['best_ask'])
        if bid > ask:
            return missing
        return {'pair': pair, 'asset': base, 'quote': 'EUR',
                'quality': 'VERIFIED_PUBLIC_L2_PRICE', 'bid': bid, 'ask': ask,
                'mid': (bid + ask) / 2, 'observed_at': depth['source']['retrieved_at'],
                'source': depth['source'], 'pair_metadata': metadata,
                'metadata_source': metadata_source, 'trade_signal': None,
                'use': 'MARK_TO_MARKET_ONLY_NOT_NET_LIQUIDATION_OR_CONVERT_QUOTE'}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return missing


def build_report(client, core_path, logical_pairs, at=None, depth_parser=None):
    if depth_parser is None:
        from direct_kraken import depth_evidence
        depth_parser = depth_evidence
    required = list(dict.fromkeys(list(logical_pairs) + list(EXTRA_PAIRS)))
    if len(required) > 35:
        raise ValueError('Unexpected valuation request count')
    for pair in required:
        asset_of(pair)
    evidence = {}
    for pair in required:
        path = Path(core_path) / 'candidates' / (pair + '.json')
        if pair in logical_pairs:
            # Use only files from this cycle, never fetch a different generation.
            evidence[pair] = json.loads(path.read_text()) if path.exists() else {}
        else:
            key, raw_meta = client._resolve(pair)
            if key is None or not isinstance(raw_meta, dict):
                evidence[pair] = {}
                continue
            raw = client._api('/0/public/Depth', {'pair': raw_meta['altname'], 'count': 100})
            evidence[pair] = {'pair_metadata': client._pairmeta(key, raw_meta),
                              'metadata_source': client._meta()['source'],
                              'depth': depth_parser(raw, key)}
    checked = time.time() if at is None else at
    rows = [price_row(p, evidence[p].get('pair_metadata', {}),
                      evidence[p].get('metadata_source', {}),
                      evidence[p].get('depth', {}), checked) for p in required]
    missing = [r['pair'] for r in rows if r['quality'] != 'VERIFIED_PUBLIC_L2_PRICE']
    return {'schema': 'CAW_PUBLIC_VALUATION_PRICES_V1', 'revision': REVISION,
            'generated_at': datetime.fromtimestamp(checked, timezone.utc).isoformat(),
            'status': 'PARTIAL' if missing else 'COMPLETE', 'required_pairs': required,
            'received_count': len(rows) - len(missing), 'missing_pairs': missing,
            'rows': rows, 'source_max_age_seconds': 600,
            'contains_account_data': False, 'automatic_trading': False,
            'private_exchange_api': False, 'trade_signal': None,
            'note': 'Prices only. Join ALL latest user-confirmed quantities privately. '
                    'Recompute source ages; no missing value may be treated as zero. '
                    'A complete price inventory is not a trade approval or proof of wallet balances.'}
