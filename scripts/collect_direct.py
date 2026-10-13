#!/usr/bin/env python3
"""Use the existing collector/publication contract with the direct public adapter.

The separate price inventory completes public valuation coverage only. It never
contains private balances and does not change strategy, cadence or risk limits.
"""
import hashlib
import json
from functools import partial
import collect_core as core
from direct_kraken import DirectKrakenTransport, PRODUCER
from public_valuation import build_report, REVISION as VALUATION_REVISION


def main():
    core.CycleTransport = partial(DirectKrakenTransport, cache_path=core.CORE / 'direct-cache.json')
    code=core.main()
    # Reuse this cycle's 17 books; five extra EUR books share the existing client,
    # request budget, cooldown and deadline. A valuation failure is a separate
    # scope, not a reason to hide otherwise valid owned SELL evidence.
    try:
        valuation=build_report(core.CLIENT, core.CORE, core.configured_pairs()[0])
    except Exception as error:
        valuation={'schema':'CAW_PUBLIC_VALUATION_PRICES_V1', 'revision':VALUATION_REVISION,
            'generated_at':core.now_iso(), 'status':'ERROR', 'error_type':type(error).__name__,
            'rows':[], 'received_count':0, 'contains_account_data':False,
            'automatic_trading':False, 'private_exchange_api':False, 'trade_signal':None}
    core.atomic_write(core.CORE/'valuation-quotes.json',valuation)
    core.CLIENT.save()
    manifest=json.loads((core.CORE/'manifest.json').read_text())
    manifest.update(transport_revision='1.5-direct-kraken', transport_mode='DIRECT_KRAKEN_NO_WORKER',
        producer=PRODUCER, expected_evidence_schema=core.EXPECTED,
        source_contract_note='Legacy wire schema only; this snapshot is not produced by the Worker.')
    manifest['valuation_revision']=VALUATION_REVISION
    manifest['modules']['valuation_prices']='DATA_OK' if valuation['status']=='COMPLETE' else 'PARTIAL_DATA'
    manifest['files']['valuation-quotes.json']={'status':valuation['status'],
        'received_count':valuation['received_count'], 'contains_account_data':False,
        'generated_at':valuation['generated_at'], 'trade_signal':None}
    history=json.loads((core.CORE/'sampling-history.json').read_text())
    history[-1]['transport_revision']='1.5-direct-kraken'
    history[-1]['independent_reference_count']=sum(v.get('quality')=='VERIFIED_PROVIDER_REFERENCE' for v in core.CLIENT.independent.references.values())
    history[-1]['valuation_prices_status']=valuation['status']
    core.atomic_write(core.CORE/'sampling-history.json',history)
    report=core.sampling_report(history)
    versions={r.get('transport_revision','legacy-unspecified') for r in history}
    report['by_transport_revision']={v:core.sampling_report([r for r in history if r.get('transport_revision','legacy-unspecified')==v]) for v in sorted(versions)}
    report['current_transport_revision']='1.5-direct-kraken'
    core.atomic_write(core.CORE/'sampling-report.json',report)
    manifest['file_sha256']={p.relative_to(core.CORE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(core.CORE.rglob('*.json')) if p.name!='manifest.json'}
    core.atomic_write(core.CORE/'manifest.json',manifest)
    return code

if __name__=='__main__':
    raise SystemExit(main())
