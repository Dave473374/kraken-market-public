"""Read-only quote integrity checks. Never signals or executable prices."""
import math
from datetime import datetime, timezone


def coverage(result, expected_pairs, schema, aliases=None, now=None):
    aliases = aliases or {}
    now = now or datetime.now(timezone.utc)
    def normal(value):
        value = str(value or '').strip().upper().replace('/', '')
        return aliases.get(value, value)
    def fresh(value):
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= 600
        except (TypeError, ValueError, AttributeError, OverflowError):
            return False
    body = result.get('body')
    body = body if isinstance(body, dict) else {}
    rows = body.get('rows')
    rows = rows if isinstance(rows, list) else []
    expected = [normal(p) for p in expected_pairs]
    counts, invalid = {}, []
    for row in rows:
        if not isinstance(row, dict):
            invalid.append('MALFORMED_ROW')
            continue
        pair = normal(row.get('altname') or row.get('pair_key'))
        counts[pair] = counts.get(pair, 0) + 1
        ticker = row.get('ticker')
        ticker = ticker if isinstance(ticker, dict) else {}
        source = ticker.get('source')
        source = source if isinstance(source, dict) else {}
        try:
            bid, ask = ticker['bid'], ticker['ask']
            valid = (not isinstance(bid, bool) and not isinstance(ask, bool)
                     and math.isfinite(float(bid)) and math.isfinite(float(ask))
                     and 0 < float(bid) <= float(ask)
                     and source.get('ok') is True and fresh(source.get('retrieved_at')))
        except (KeyError, TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            invalid.append(pair)
    missing = [p for p in expected if counts.get(p, 0) == 0]
    duplicates = [p for p, n in counts.items() if n > 1]
    unexpected = [p for p in counts if p not in expected]
    ok = (result.get('ok') is True and body.get('schema') == schema
          and bool(expected) and len(set(expected)) == len(expected)
          and not body.get('unresolved') and not (missing or duplicates or unexpected or invalid))
    return {'research_usable': bool(ok), 'expected_count': len(expected),
            'received_count': len(rows), 'missing_pairs': missing,
            'duplicate_pairs': duplicates, 'unexpected_pairs': unexpected,
            'invalid_or_stale_pairs': invalid, 'executable_evidence': False}
