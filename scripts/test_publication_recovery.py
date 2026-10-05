"""Real local Git publication tests. A local bare repository; no external network."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import run_public_collector as runner


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.bare=base/'origin.git'; self.root=base/'work'
        subprocess.run(['git','init','--bare','-q',str(self.bare)],check=True,capture_output=True)
        self.root.mkdir()
        self.run_git('init','-q','-b','main')
        self.run_git('config','user.name','Offline test')
        self.run_git('config','user.email','test@example.invalid')
        self.run_git('remote','add','origin',str(self.bare))
        (self.root/'research').mkdir()
        (self.root/'research/protected.txt').write_text('Other projects remain unchanged')
        (self.root/'README.md').write_text('original main')
        self.run_git('add','.')
        self.run_git('commit','-qm','Synthetic initial commit')
        self.main=self.run_git('rev-parse','HEAD')
        self.run_git('push','-q','origin','HEAD:main','HEAD:core-live')
        self.files={'manifest.json':b'{"schema":"SYNTHETIC_TEST_ONLY","generated_at":"old-time"}',
                    'collection-state.json':b'{"contains_account_data":false}',
                    'candidates/TESTEUR.json':b'{"data_health":"PARTIAL_DATA"}'}
    def run_git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL).decode().strip()
    def test_orphan_publish_preserves_main_and_restores_worktree(self):
        with patch.object(runner,'ROOT',self.root), patch.dict(os.environ,{'GITHUB_RUN_ID':'999'}):
            new=runner.publish_core(self.files,self.main)
        self.assertNotEqual(new,self.main)
        self.assertEqual(self.run_git('rev-parse','HEAD'),self.main)
        self.assertEqual(self.run_git('show','main:research/protected.txt'),'Other projects remain unchanged')
        self.assertEqual(self.run_git('ls-remote','origin','refs/heads/main').split()[0],self.main)
        self.assertEqual(self.run_git('ls-remote','origin','refs/heads/core-live').split()[0],new)
        self.assertEqual(self.run_git('rev-list','--count',new),'1')
        self.assertEqual((self.root/'data/core/manifest.json').read_bytes(),self.files['manifest.json'])
    def test_lease_conflict_does_not_overwrite_newer_snapshot(self):
        with patch.object(runner,'ROOT',self.root), patch.dict(os.environ,{'GITHUB_RUN_ID':'999'}):
            new=runner.publish_core(self.files,self.main)
            with self.assertRaises(subprocess.CalledProcessError):
                runner.publish_core({**self.files,'extra.json':b'{}'},self.main)
        self.assertEqual(self.run_git('ls-remote','origin','refs/heads/core-live').split()[0],new)
        self.assertEqual(self.run_git('rev-parse','HEAD'),self.main)
    def test_snapshot_read_pins_one_generation(self):
        with patch.object(runner,'ROOT',self.root), patch.dict(os.environ,{'GITHUB_RUN_ID':'999'}):
            new=runner.publish_core(self.files,self.main)
            sha,files=runner.snapshot_from_git()
        self.assertEqual(sha,new);self.assertEqual(files,self.files)

    def test_crash_cannot_republish_previous_cycle_or_start_successor(self):
        old = {'manifest.json': json.dumps({'operational_revision':'1.4-single-owner',
                'run_id':'999', 'status':'OK'}).encode()}
        with patch.object(runner,'ROOT',self.root), patch.dict(os.environ,{
                'GITHUB_RUN_ID':'999','GITHUB_REPOSITORY':runner.REPO,
                'GITHUB_REF':'refs/heads/main'}), \
             patch.object(runner,'snapshot_from_git',return_value=(self.main,old)), \
             patch.object(runner,'git',return_value=self.main), \
             patch.object(runner.subprocess,'run') as process, \
             patch.object(runner,'publish_core') as publish, \
             patch.object(runner,'handoff') as handoff:
            process.return_value.returncode=1
            with self.assertRaises(KeyError):
                runner.collect_session(10,7)
        publish.assert_not_called(); handoff.assert_not_called()
        self.assertFalse((self.root/'data/core/manifest.json').exists())


if __name__=='__main__': unittest.main()
