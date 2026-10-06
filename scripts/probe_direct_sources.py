#!/usr/bin/env python3
"""PR-only isolated two-cycle public evidence test. Never publish or trade."""
import json
import tempfile
from pathlib import Path
import collect_core as core
import collect_direct


def main():
    with tempfile.TemporaryDirectory(prefix='caw-direct-probe-') as tmp:
        core.CORE=Path(tmp)/'core';core.CORE_CANDIDATES=core.CORE/'candidates'
        for cycle in range(2):
            code=collect_direct.main()
            manifest=json.loads((core.CORE/'manifest.json').read_text())
            record=json.loads((core.CORE/'sampling-history.json').read_text())[-1]
            print(json.dumps({'diagnostic_only':True,'cycle':cycle+1,'collector_exit':code,
                'manifest_status':manifest['status'],'modules':manifest['modules'],
                'requests_made':record['requests_made'],'rate_errors':record['http_429_count'],
                'candidate_ok':manifest['candidate_pairs_ok'],
                'missing':manifest['candidate_pairs_partial_or_failed'],
                'independent':core.CLIENT.independent.report(),'contains_account_data':False,
                'production_ready':False}),flush=True)
            if not manifest['summary']['owned_priority_complete'] or not manifest['summary']['owned_history_complete']:
                raise RuntimeError('Direct source test missing required owned evidence; not ready to deploy')
            if cycle==0:first_count=record['requests_made']
            elif record['requests_made']>=first_count:
                raise RuntimeError('Warm cache did not reduce requests; investigate before deployment')

if __name__=='__main__':main()
