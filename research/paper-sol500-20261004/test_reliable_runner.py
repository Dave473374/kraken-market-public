"""Offline regression tests. Fake clock/market/GitHub; real temporary local Git.
These test records never enter the real paper ledger.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import engine as e
import runner as c
import reliable_runner as r
from test_engine import snap


class Clock:
    def __init__(self, now=e.START+7205): self.now=now
    def time(self): return self.now
    def sleep(self, seconds): self.now+=max(seconds,0)


def report(s):
    p={k:s.get(k) for k in ('id','protocol_hash','last_observed_at','cash_eur','equity_eur','fees_eur','final')}
    p.update(paper_only=True,real_orders=False,fill_count=len(s['fills']))
    return p


def state(now):
    s=e.fresh_state()
    s.update(last_observed=now,last_observed_at=e.iso(now),last_attempt_at=e.iso(now),
             success_count=1,data_health='DATA_OK',engine_sha256=c.ENGINE_SHA,
             last_snapshot='observations/'+e.datetime.fromtimestamp(now,e.timezone.utc).strftime('%Y%m%dT%H%M%SZ.json'))
    return s


class TransportTests(unittest.TestCase):
    def setUp(self): r.TRANSPORT.clear()
    def test_transient_push_succeeds_beyond_old_three_attempt_limit(self):
        clock=Clock()
        results=[Mock(returncode=1,stdout='remote: Internal Server Error') for _ in range(4)]+[Mock(returncode=0,stdout='ok')]
        with patch.object(c,'time',clock),patch.object(c,'command',side_effect=results) as cmd:
            result=r.network(['git','push'],Path('.'))
        self.assertEqual(result.returncode,0)
        self.assertEqual(cmd.call_count,5)
        self.assertEqual(r.TRANSPORT[-1]['kind'],'TRANSPORT_RECOVERED')
    def test_timeouts_retry_without_logging_environment(self):
        clock=Clock()
        with patch.object(c,'time',clock),patch.object(c,'command',side_effect=[subprocess.TimeoutExpired('git',45),Mock(returncode=0,stdout='')]):
            r.network(['git','fetch'],Path('.'))
        self.assertEqual(r.TRANSPORT[0]['detail'],'TimeoutExpired')
    def test_persistent_network_failure_is_bounded(self):
        clock=Clock(); start=clock.now
        with patch.object(c,'time',clock),patch.object(c,'command',return_value=Mock(returncode=1,stdout='Internal Server Error')):
            with self.assertRaises(RuntimeError): r.network(['git','push'],Path('.'))
        self.assertLessEqual(clock.now-start,600)
    def test_non_fast_forward_never_force_pushes(self):
        with patch.object(c,'command',return_value=Mock(returncode=1,stdout='non-fast-forward')) as cmd:
            with self.assertRaises(ValueError): r.network(['git','push'],Path('.'))
        self.assertEqual(cmd.call_count,1)
        self.assertNotIn('--force',str(cmd.call_args))
    def test_auth_failure_fails_closed(self):
        with patch.object(c,'command',return_value=Mock(returncode=1,stdout='Authentication failed')):
            with self.assertRaises(ValueError): r.network(['gh','api'],Path('.'))
    def test_cancelled_successor_is_not_a_live_successor(self):
        obj={'id':2,'head_branch':'main','status':'completed','conclusion':'cancelled','path':'.github/workflows/'+c.WORKFLOW}
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':c.REPO,'GITHUB_RUN_ID':'1'}),patch.object(r,'github_json',return_value={'workflow_runs':[obj]}),patch.object(r,'network',return_value=Mock(returncode=0,stdout='')) as net:
            out=r.ensure_successor(e.START,False)
        self.assertEqual(out['verification'],'DISPATCH_ACCEPTED_NOT_START_CONFIRMED')
        self.assertIn('POST',net.call_args.args[0])
    def test_pending_successor_is_verified_without_duplicate(self):
        obj={'id':2,'head_branch':'main','status':'pending','path':'.github/workflows/'+c.WORKFLOW}
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':c.REPO,'GITHUB_RUN_ID':'1'}),patch.object(r,'github_json',return_value={'workflow_runs':[obj]}),patch.object(r,'network') as net:
            out=r.ensure_successor(e.START,False)
        net.assert_not_called(); self.assertEqual(out['run_id'],2)
    def test_other_workflow_does_not_count_as_successor(self):
        obj={'id':2,'head_branch':'main','status':'in_progress','path':'.github/workflows/unrelated.yml'}
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':c.REPO,'GITHUB_RUN_ID':'1'}),patch.object(r,'github_json',return_value={'workflow_runs':[obj]}),patch.object(r,'network',return_value=Mock(returncode=0,stdout='')) as net:
            r.ensure_successor(e.START,False)
        self.assertEqual(net.call_count,1)
    def test_current_worker_does_not_count_as_successor(self):
        obj={'id':1,'head_branch':'main','status':'in_progress','path':'.github/workflows/'+c.WORKFLOW}
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':c.REPO,'GITHUB_RUN_ID':'1'}),patch.object(r,'github_json',return_value={'workflow_runs':[obj]}),patch.object(r,'network',return_value=Mock(returncode=0,stdout='')) as net:
            r.ensure_successor(e.START,False)
        self.assertEqual(net.call_count,1)
    def test_final_and_cutoff_do_not_restart(self):
        with patch.object(r,'github_json') as get:
            self.assertEqual(r.ensure_successor(e.END,True)['status'],'NOT_NEEDED')
            self.assertEqual(r.ensure_successor(c.HARD_STOP,False)['status'],'NOT_NEEDED')
            get.assert_not_called()
    def test_wrong_repo_rejected(self):
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':'other/repo'}):
            with self.assertRaises(ValueError): r.ensure_successor(e.START,False)
    def test_unsafe_snapshot_path_rejected(self):
        with self.assertRaises(ValueError): r.safe_snapshot_path(Path('/tmp'), '../state.json')
    def test_frozen_engine_and_end_remain_exact(self):
        self.assertEqual(hashlib.sha256(Path(e.__file__).read_bytes()).hexdigest(),c.ENGINE_SHA)
        self.assertEqual(e.PHASH,c.PHASH); self.assertEqual(e.END,c.END)


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.ledger=self.base/'ledger';self.ledger.mkdir()
        self.remote=self.base/'remote.git'; self.root=self.ledger/c.REL;self.root.mkdir(parents=True)
        self.real=c.command
        self.git('init','--bare',str(self.remote));self.git('init','-b',c.BRANCH)
        self.git('config','user.name','test');self.git('config','user.email','test@example.invalid')
        self.git('remote','add','origin',str(self.remote))
        self.old=state(e.START+60);self.write(self.root,self.old,True)
        self.git('add','.');self.git('commit','-m','seed');self.git('push','-u','origin',c.BRANCH)
    def git(self,*args): return self.real(['git',*args],self.ledger)
    def write(self,root,s,with_snapshot=False):
        root.mkdir(exist_ok=True,parents=True)
        (root/'state.json').write_text(json.dumps(s))
        (root/'report.json').write_text(json.dumps(report(s)))
        if with_snapshot:
            p=root/s['last_snapshot'];p.parent.mkdir(exist_ok=True)
            p.write_text(json.dumps({'id':c.ID,'observed_unix':s['last_observed'],'observed_at':s['last_observed_at']}))
    def candidate(self,with_snapshot):
        root=self.base/'artifact';new=copy.deepcopy(self.old)
        new.update(last_observed=e.START+360,last_observed_at=e.iso(e.START+360),last_attempt_at=e.iso(e.START+360),success_count=2,
                   last_snapshot='observations/'+e.datetime.fromtimestamp(e.START+360,e.timezone.utc).strftime('%Y%m%dT%H%M%SZ.json'))
        self.write(root,new,with_snapshot);return root,new


class DurableLedgerTests(GitFixture):
    def test_retry_keeps_one_commit_and_identical_state(self):
        (self.root/'extra.json').write_text('{}')
        old_head=self.git('rev-parse','HEAD').stdout.strip(); calls=[]
        def cmd(args,cwd,**kwargs):
            if args[:2]==['git','push']:
                calls.append(1)
                if len(calls)<5:return subprocess.CompletedProcess(args,1,'Internal Server Error')
            return self.real(args,cwd,**kwargs)
        with patch.object(c,'command',cmd),patch.object(c,'time',Clock()):r.persist(self.ledger)
        self.assertEqual(len(calls),5)
        self.assertEqual(self.git('rev-list','--count',old_head+'..HEAD').stdout.strip(),'1')
        self.assertEqual(c.load_ledger(self.root),self.old)
    def test_already_committed_but_unpublished_head_is_flushed(self):
        (self.root/'extra.json').write_text('{}');self.git('add','.');self.git('commit','-m','pending')
        local=self.git('rev-parse','HEAD').stdout.strip()
        r.persist(self.ledger)
        self.assertEqual(self.git('ls-remote','origin','refs/heads/'+c.BRANCH).stdout.split()[0],local)
    def test_sync_flushes_clean_ahead_commit(self):
        (self.root/'extra.json').write_text('{}');self.git('add','.');self.git('commit','-m','pending')
        local=self.git('rev-parse','HEAD').stdout.strip();r.sync(self.ledger)
        self.assertEqual(self.git('ls-remote','origin','refs/heads/'+c.BRANCH).stdout.split()[0],local)
    def test_no_unrelated_staged_write(self):
        (self.ledger/'not-the-trial.txt').write_text('untouched');self.git('add','not-the-trial.txt')
        with self.assertRaises(ValueError):r.persist(self.ledger)
    def test_unpublished_worktree_blocks_next_evaluation(self):
        (self.root/'state.json').write_text('invalid')
        with self.assertRaises(RuntimeError):r.sync(self.ledger)
    def test_full_recovery_restores_original_bytes(self):
        root,new=self.candidate(True); raw=(root/'state.json').read_bytes()
        out=r.restore_candidate(self.ledger,root,100,1000)
        self.assertEqual(out,'ORIGINAL_UNPUBLISHED_RECORD_RESTORED')
        self.assertEqual((self.root/'state.json').read_bytes(),raw)
        self.assertEqual(c.load_ledger(self.root),new)
        self.assertTrue((self.root/new['last_snapshot']).is_file())
    def test_legacy_no_snapshot_no_trade_change_kept_only_as_evidence(self):
        root,new=self.candidate(False);raw=(self.root/'state.json').read_bytes()
        out=r.restore_candidate(self.ledger,root,100,1000)
        self.assertEqual(out,'INCOMPLETE_LEGACY_NO_TRADE_CHANGE_EVIDENCE_ONLY')
        self.assertEqual((self.root/'state.json').read_bytes(),raw)
        self.assertFalse((self.root/new['last_snapshot']).exists())
        self.assertTrue((self.root/'recovery/100/state.json').is_file())
    def test_missing_snapshot_financial_change_blocks(self):
        root,new=self.candidate(False);new['cash_eur']=499;self.write(root,new)
        with self.assertRaises(ValueError):r.restore_candidate(self.ledger,root,100,1000)
        self.assertEqual(c.load_ledger(self.root),self.old)
    def test_recovery_protocol_mismatch_blocks(self):
        root,new=self.candidate(True);new['protocol_hash']='wrong';self.write(root,new)
        with self.assertRaises(ValueError):r.restore_candidate(self.ledger,root,100,1000)
    def test_recovery_engine_checksum_mismatch_blocks(self):
        root,new=self.candidate(True);new['engine_sha256']='wrong';self.write(root,new)
        with self.assertRaises(ValueError):r.restore_candidate(self.ledger,root,100,1000)
    def test_recovery_observation_time_mismatch_blocks(self):
        root,new=self.candidate(True);p=root/new['last_snapshot'];s=json.loads(p.read_text());s['observed_at']=e.iso(e.START);p.write_text(json.dumps(s))
        with self.assertRaises(ValueError):r.restore_candidate(self.ledger,root,100,1000)
    def test_existing_observation_cannot_be_overwritten(self):
        root,new=self.candidate(True);p=root/self.old['last_snapshot'];p.write_text('{"different":true}')
        with self.assertRaises(ValueError):r.restore_candidate(self.ledger,root,100,1000)
    def test_existing_trade_history_cannot_be_rewritten(self):
        root,new=self.candidate(True);old=copy.deepcopy(self.old);old['gaps']=[{'original':'keep'}];self.write(self.root,old)
        with self.assertRaises(ValueError):r.restore_candidate(self.ledger,root,100,1000)
    def test_older_recovery_is_not_a_reset(self):
        root,new=self.candidate(True);self.write(self.root,new,True)
        self.write(root,self.old,True)
        self.assertEqual(r.restore_candidate(self.ledger,root,100,1000),'ALREADY_COVERED')
        self.assertEqual(c.load_ledger(self.root),new)



class WholeWorkerTests(GitFixture):
    # Reuses the local Git fixture. No API credentials and no real market traffic.
    def run_worker(self, lose_queue=False, fail_push=False):
        clock=Clock();tick_times=[];posts=[];queued=[]
        original_command=self.real
        def cmd(args,cwd,**kwargs):
            if args[0]==sys.executable:
                before=c.load_ledger(self.root)
                tick_times.append(clock.now)
                new=e.evaluate(before,snap(clock.now))
                new['last_attempt_at']=e.iso(clock.now);new['engine_sha256']=c.ENGINE_SHA
                new['last_snapshot']='observations/'+e.datetime.fromtimestamp(clock.now,e.timezone.utc).strftime('%Y%m%dT%H%M%SZ.json')
                self.write(self.root,new,True)
                return subprocess.CompletedProcess(args,0,'OFFLINE TEST')
            if args[0]=='gh':
                if 'POST' in args:
                    posts.append(clock.now)
                    queued[:]=[{'id':900,'head_branch':'main','status':'pending','path':'.github/workflows/'+c.WORKFLOW}]
                    return subprocess.CompletedProcess(args,0,'')
                if 'status=failure' in args[-1]:return subprocess.CompletedProcess(args,0,'{"workflow_runs":[]}')
                return subprocess.CompletedProcess(args,0,json.dumps({'workflow_runs':queued}))
            if args[:2]==['git','push'] and fail_push and tick_times:
                return subprocess.CompletedProcess(args,1,'Internal Server Error')
            return original_command(args,cwd,**kwargs)
        original_main=c.main
        def main_core():
            original_main()
            if lose_queue:queued[:]=[]
        with patch.object(sys,'argv',['reliable_runner','--ledger',str(self.ledger),'--minutes','6']),patch.object(c,'time',clock),patch.object(c,'command',cmd),patch.object(c,'main',main_core),patch.dict(os.environ,{'GITHUB_REPOSITORY':c.REPO,'GITHUB_RUN_ID':'800'}):
            if fail_push:
                with self.assertRaises(RuntimeError):r.main()
            else:r.main()
        return tick_times,posts
    def test_full_worker_checks_queue_without_duplicate(self):
        ticks,posts=self.run_worker()
        self.assertEqual(ticks,[e.START+7205,e.START+7505]);self.assertEqual(len(posts),1)
        self.assertEqual(c.load_ledger(self.root)['last_observed'],ticks[-1])
        self.assertEqual(json.loads((self.root/'operations.json').read_text())['supervisor_revision'],r.REV)
    def test_cancelled_queue_at_handoff_rearmed(self):
        ticks,posts=self.run_worker(lose_queue=True)
        self.assertEqual(len(ticks),2);self.assertEqual(len(posts),2)
    def test_failed_push_no_second_tick_and_rearms_after_failure(self):
        ticks,posts=self.run_worker(fail_push=True)
        self.assertEqual(len(ticks),1);self.assertEqual(len(posts),1)
        self.assertTrue((self.root/'reliability.json').exists())
    def test_module_hooks_restored_after_worker_failure(self):
        before=(c.sync,c.persist,c.successor,c.REV)
        self.run_worker(fail_push=True)
        self.assertEqual((c.sync,c.persist,c.successor,c.REV),before)


if __name__=='__main__':unittest.main(verbosity=2)
