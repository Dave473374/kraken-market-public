#!/usr/bin/env python3
"""Use the existing collector/publication contract with the direct public adapter.

Only the transport factory is replaced. Existing pair coverage, evidence guards,
private-state exclusions, sampling history and candidate discovery remain intact.
"""
import hashlib
import json
from functools import partial
import collect_core as core
from direct_kraken import DirectKrakenTransport, PRODUCER


def main():
    core.CycleTransport = partial(DirectKrakenTransport, cache_path=core.CORE / 'direct-cache.json')
    code=core.main()
    core.CLIENT.save()
    manifest=json.loads((core.CORE/'manifest.json').read_text())
    manifest.update(transport_revision='1.5-direct-kraken', transport_mode='DIRECT_KRAKEN_NO_WORKER',
        producer=PRODUCER, expected_evidence_schema=core.EXPECTED,
        source_contract_note='Legacy wire schema only; this snapshot is not produced by the Worker.')
    history=json.loads((core.CORE/'sampling-history.json').read_text())
    history[-1]['transport_revision']='1.5-direct-kraken'
    history[-1]['independent_reference_count']=sum(v.get('quality')=='VERIFIED_PROVIDER_REFERENCE' for v in core.CLIENT.independent.references.values())
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
