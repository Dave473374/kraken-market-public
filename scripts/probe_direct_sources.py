#!/usr/bin/env python3
"""PR-only isolated cold/warm public evidence test. Never publish or trade."""
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
            diagnostics={}
            for path in (core.CORE/'candidates').glob('*.json'):
                body=json.loads(path.read_text());block=body.get('closed_4h',{})
                diagnostics[path.stem]={'data_health':body.get('data_health'),
                    'blocking_data_reasons':body.get('blocking_data_reasons'),
                    'candle_quality':block.get('quality'),'candle_end':block.get('latest_closed_bar_end'),
                    'candle_source':block.get('source'),
                    'independent_comparison':body.get('independent_comparison')}
            references={a:{k:r.get(k) for k in ('quality','provider','asset_id','asset_symbol','price','quote','observed_unix')}
                        for a,r in core.CLIENT.independent.references.items()}
            print(json.dumps({'diagnostic_only':True,'cycle':cycle+1,'collector_exit':code,
                'manifest_status':manifest['status'],'modules':manifest['modules'],
                'requests_made':record['requests_made'],'rate_errors':record['http_429_count'],
                'candidate_ok':manifest['candidate_pairs_ok'],
                'diagnostics':diagnostics,'independent_references':references,
                'contains_account_data':False,'production_ready':False}),flush=True)
            if not manifest['summary']['owned_priority_complete'] or not manifest['summary']['owned_history_complete']:
                raise RuntimeError('Direct source test missing required owned evidence; not ready to deploy')
            if cycle==0:first_count=record['requests_made']
            elif record['requests_made']>=first_count:
                raise RuntimeError('Warm cache did not reduce requests; investigate before deployment')
            night=core.CLIENT.independent.get('NIGHT')
            if night.get('quality')=='VERIFIED_PROVIDER_REFERENCE' and night.get('asset_id')!='midnight-3':
                raise RuntimeError('Wrong NIGHT identity; must not deploy')

if __name__=='__main__':main()
