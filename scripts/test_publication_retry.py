"""Offline fault injection against a real local bare Git repo; no market calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import run_public_collector as runner


class VerifiedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.remote = base/'work', base/'origin.git'
        self.root.mkdir()
        subprocess.run(['git','init','--bare','-q',str(self.remote)], check=True, capture_output=True)
        self.git('init','-q','-b','main')
        self.git('config','user.name','Offline test')
        self.git('config','user.email','test@example.invalid')
        self.git('remote','add','origin',str(self.remote))
        (self.root/'README.md').write_text('Main must stay unchanged')
        self.git('add','.')
        self.git('commit','-qm','Synthetic baseline')
        self.old = self.git('rev-parse','HEAD')
        self.git('push','-q','origin','HEAD:main','HEAD:core-live')
        (self.root/'untracked-cache.txt').write_text('Keep this too')
        self.files = {'manifest.json':b'{"snapshot_id":"synthetic","generated_at":"2026-10-01T00:00:00Z"}',
            'candidates/TESTEUR.json':b'{"data_health":"PARTIAL_DATA"}',
            'sampling-history.json':b'{"old_failures":["preserve"]}'}
        self.addCleanup(patch.stopall)
        patch.object(runner,'ROOT',self.root).start()
        patch.object(runner,'RETRY_DELAYS',(0,0,0,0)).start()
        self.real_git = runner.git

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL).decode().strip()

    def remote_sha(self):
        return self.git('ls-remote','origin','refs/heads/core-live').split()[0]

    def test_one_500_recovers_with_identical_commit_and_lease(self):
        pushes=[]
        def flaky(*args,**kw):
            if args[0]=='push':
                pushes.append(args)
                if len(pushes)==1:
                    raise subprocess.CalledProcessError(1,['git',*args],output=b'remote: Internal Server Error')
            return self.real_git(*args,**kw)
        with patch.object(runner,'git',side_effect=flaky):
            new=runner.publish_core(self.files,self.old)
        self.assertEqual(len(pushes),2)
        self.assertEqual(pushes[0],pushes[1])
        self.assertEqual(self.remote_sha(),new)
        self.assertEqual(self.git('rev-parse','HEAD'),self.old)
        self.assertEqual((self.root/'untracked-cache.txt').read_text(),'Keep this too')
        self.assertEqual(self.git('show',new+':data/core/manifest.json'),self.files['manifest.json'].decode())

    def test_success_lost_ack_is_verified_without_duplicate_push(self):
        pushes=[]
        def lost(*args,**kw):
            out=self.real_git(*args,**kw)
            if args[0]=='push':
                pushes.append(args)
                raise subprocess.TimeoutExpired(['git',*args],45)
            return out
        with patch.object(runner,'git',side_effect=lost):
            new=runner.publish_core(self.files,self.old)
        self.assertEqual(self.remote_sha(),new)
        self.assertEqual(len(pushes),1)

    def test_remote_probe_outage_after_success_does_not_overwrite(self):
        reads=[0]
        def flaky(*args,**kw):
            if args[0]=='ls-remote':
                reads[0]+=1
                if reads[0]==1:
                    raise subprocess.CalledProcessError(1,['git'],output=b'error: 503')
            return self.real_git(*args,**kw)
        with patch.object(runner,'git',side_effect=flaky):
            new=runner.publish_core(self.files,self.old)
        self.assertEqual(self.remote_sha(),new)
        self.assertEqual(reads[0],2)

    def test_real_concurrent_writer_is_never_overwritten(self):
        new=runner.publish_core(self.files,self.old)
        with self.assertRaises(subprocess.CalledProcessError):
            runner.publish_core({**self.files,'extra.json':b'{}'},self.old)
        self.assertEqual(self.remote_sha(),new)
        self.assertEqual(self.git('rev-parse','HEAD'),self.old)

    def test_permanent_permission_failure_not_retried(self):
        pushes=[]
        def denied(*args,**kw):
            if args[0]=='push':
                pushes.append(args)
                raise subprocess.CalledProcessError(128,['git'],output=b'Permission denied (403)')
            return self.real_git(*args,**kw)
        with patch.object(runner,'git',side_effect=denied):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                runner.publish_core(self.files,self.old)
        self.assertNotIsInstance(caught.exception,runner.PublicationUnavailable)
        self.assertEqual(len(pushes),1)
        self.assertEqual(self.remote_sha(),self.old)

    def test_exhaustion_preserves_pending_and_does_not_report_success(self):
        pushes=[]
        def failed(*args,**kw):
            if args[0]=='push':
                pushes.append(args)
                raise subprocess.CalledProcessError(1,['git'],output=b'remote: Internal Server Error')
            return self.real_git(*args,**kw)
        with patch.object(runner,'git',side_effect=failed):
            with self.assertRaises(runner.PublicationUnavailable) as caught:
                runner.publish_core(self.files,self.old)
        self.assertEqual(len(pushes),4)
        self.assertEqual(len(set(pushes)),1)
        self.assertEqual(self.remote_sha(),self.old)
        self.assertEqual((self.root/'data/core/sampling-history.json').read_bytes(),self.files['sampling-history.json'])
        pending=caught.exception
        self.assertEqual(self.git('show',pending.sha+':data/core/manifest.json'),self.files['manifest.json'].decode())
        self.assertIn('PUBLICATION_UNCONFIRMED',(self.root/'.caw-publication/attempts.jsonl').read_text())
        self.assertNotIn('REMOTE_REF_VERIFIED',(self.root/'.caw-publication/attempts.jsonl').read_text())

    def test_retry_recovery_uses_pending_commit_without_new_collection(self):
        pending=runner.PublicationUnavailable('a'*40,self.old)
        with patch.object(runner,'publish_core',side_effect=pending) as build, \
             patch.object(runner,'push_verified',return_value='a'*40) as push, \
             patch.object(runner.time,'monotonic',return_value=0), \
             patch.object(runner.time,'sleep') as sleep:
            self.assertEqual(runner.publish_with_recovery(self.files,self.old,120),'a'*40)
        build.assert_called_once()
        push.assert_called_once_with('a'*40,self.old)
        sleep.assert_called_once_with(60)

    def test_recovery_stops_at_bounded_session_deadline(self):
        pending=runner.PublicationUnavailable('a'*40,self.old)
        with patch.object(runner,'publish_core',side_effect=pending), \
             patch.object(runner,'push_verified') as push, \
             patch.object(runner.time,'monotonic',return_value=61):
            with self.assertRaises(runner.PublicationUnavailable):
                runner.publish_with_recovery(self.files,self.old,120)
        push.assert_not_called()

    def test_startup_fetch_retries_transient_only(self):
        with patch.object(runner,'git',side_effect=[subprocess.CalledProcessError(128,['git'],output=b'error: 503'),'ok']) as git:
            self.assertEqual(runner.fetch_core_ref(),'ok')
        self.assertEqual(git.call_count,2)
        with patch.object(runner,'git',side_effect=subprocess.CalledProcessError(128,['git'],output=b'Permission denied')) as git:
            with self.assertRaises(subprocess.CalledProcessError): runner.fetch_core_ref()
        self.assertEqual(git.call_count,1)

    def test_source_times_partial_flags_and_history_unchanged(self):
        new=runner.publish_core(self.files,self.old)
        for name,content in self.files.items():
            self.assertEqual(self.git('show',new+':data/core/'+name),content.decode())
        self.assertEqual(self.git('rev-list','--count',new),'1')
        self.assertEqual(self.git('ls-remote','origin','refs/heads/main').split()[0],self.old)

    def test_path_traversal_and_missing_manifest_rejected(self):
        for files in ({'extra.json':b'{}'},{**self.files,'../bad.json':b'{}'}, {**self.files,'/bad.json':b'{}'}):
            with self.assertRaises(ValueError): runner.publish_core(files,self.old)
        self.assertFalse((self.root/'data/bad.json').exists())
        self.assertEqual(self.remote_sha(),self.old)

    def test_retry_pacing_is_bounded_and_nonzero_in_production(self):
        source=Path(runner.__file__).read_text()
        self.assertIn('RETRY_DELAYS = (0, 15, 45, 90)',source)
        self.assertNotIn("git('clean'",source)
        self.assertNotIn("git('checkout'",source)


if __name__=='__main__': unittest.main()
