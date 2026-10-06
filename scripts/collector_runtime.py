"""Bounded operational state and recovery. No account or advisory state belongs here."""
import math
import time
from datetime import datetime, timezone
from public_transport import PublicGetter
from evidence_guard import obj, normalize


def iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class CycleTransport(PublicGetter):
    """Reuse server cooldown across collector processes. At most one retry per path.

    Single FAST CORE upstream owner; M1 archives that owner instead of fetching
    Kraken again. This cannot coordinate unrelated projects/Worker isolates.
    """
    def __init__(self, base, previous=None, timeout=30):
        super().__init__(base, timeout=timeout, min_interval=3.0)
        previous = obj(previous)
        until = previous.get('cooldown_until_epoch', 0)
        if not isinstance(until, (int, float)) or isinstance(until, bool) or not math.isfinite(until) or until < 0:
            raise ValueError('Invalid persisted cooldown; do not silently reset it')
        self.cooldown_until = time.monotonic() + max(0, until - time.time())
        self.blocked_by_url = previous.get('blocked_by_url')
        self.blocked_at = previous.get('blocked_at')
        self.deadline = time.monotonic() + 480
        self.wait_budget = 120.0
        self.requests_made = 0
        self.recovery_wait_seconds = 0.0

    def get_bounded(self, path):
        attempts, result = 0, None
        for _ in range(2):
            # Bound total rejections across paths; a short local hint must not
            # create a rapid loop across the entire configured universe.
            if self.http_429_count >= 6:
                break
            remaining = max(0.0, self.cooldown_until - time.monotonic())
            if remaining:
                wait = remaining + 0.05
                if wait > self.wait_budget or time.monotonic() + wait + self.timeout >= self.deadline:
                    break
                time.sleep(wait)
                self.wait_budget -= wait
                self.recovery_wait_seconds += wait
            if time.monotonic() + self.timeout >= self.deadline:
                break
            result = super().get(path)
            if result.get('http_request_made', True):
                attempts += 1
                self.requests_made += 1
            if result.get('error') not in ('HTTP_429', 'LOCAL_RATE_LIMIT_COOLDOWN'):
                return result, attempts
        if result is not None:
            return result, attempts
        return {'ok': False, 'url': self.base + path, 'retrieved_at': iso(),
                'error': 'COLLECTION_BUDGET_OR_COOLDOWN', 'http_request_made': False,
                'detail': 'Deferred, not a strategy rejection; next cycle prioritizes missing owned evidence'}, attempts

    def state(self):
        return {'cooldown_until_epoch': time.time() + max(0, self.cooldown_until - time.monotonic()),
                'blocked_by_url': self.blocked_by_url, 'blocked_at': self.blocked_at}


def retained_seeds(previous, current, now, limit=20):
    """Public candidate history only: retain up to 48h, current seeds first."""
    kept = {}
    for item in previous if isinstance(previous, list) else []:
        item = obj(item)
        try:
            age = (now - datetime.fromisoformat(item['last_seen_at'].replace('Z', '+00:00'))).total_seconds()
            if 0 <= age <= 48 * 3600 and item.get('pair'):
                kept[normalize(item['pair'])] = dict(item)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    order = []
    for item in current:
        if not isinstance(item, dict):
            continue
        pair = normalize(item.get('pair_key') or item.get('altname') or item.get('pair'))
        if not pair or not pair.isalnum() or len(pair) > 24:
            continue
        if pair not in order:
            order.append(pair)
        row = kept.setdefault(pair, {'pair': pair, 'first_seen_at': now.isoformat(), 'last_reviewed_at': None})
        row['last_seen_at'] = now.isoformat()
        row['asset'] = item.get('asset') or item.get('base_code') or pair
    order.extend(p for p in kept if p not in order)
    result, seen = [], set()
    for pair in order:
        row = kept[pair]
        asset = row.get('asset') or pair
        if asset in seen:
            continue
        seen.add(asset)
        result.append(row)
        if len(result) >= limit:
            break
    return result


def review_seed_pairs(rows, excluded, limit=3):
    candidates = [r for r in rows if r['pair'] not in excluded]
    candidates.sort(key=lambda r: (r.get('last_reviewed_at') or '', r.get('first_seen_at') or '', r['pair']))
    return [r['pair'] for r in candidates[:min(3, max(0, limit))]]


def sampling_report(history):
    """Summarize OBSERVED collection samples, never invent continuous uptime."""
    if not history:
        return {'status': 'NO_OBSERVATIONS'}
    stamps = [datetime.fromisoformat(r['generated_at'].replace('Z', '+00:00')) for r in history]
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    run_ids = list(dict.fromkeys(r.get('run_id') for r in history if r.get('run_id')))
    complete = sum(r.get('owned_complete') is True for r in history)
    span = (stamps[-1] - stamps[0]).total_seconds()
    return {'status': 'NOT_YET_24H' if span < 86400 else 'READY_FOR_HUMAN_REVIEW',
            'first_observation_at': history[0]['generated_at'],
            'last_observation_at': history[-1]['generated_at'], 'observed_span_seconds': span,
            'observation_count': len(history), 'owned_complete_sample_count': complete,
            'owned_complete_sample_pct': 100 * complete / len(history),
            'max_publication_gap_seconds': max(gaps, default=None),
            'gaps_over_600s': sum(g > 600 for g in gaps), 'distinct_run_ids': run_ids,
            'continuous_uptime_proven': False, 'trade_logic_validated': False,
            'notification_delivery_validated': False, 'production_ready': False}
