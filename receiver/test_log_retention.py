import gzip
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from diagnostic_log import DiagnosticLog,diagnostic_lines,managed_files,atomic_json
from log_retention import DEFAULTS,adopt,candidates,closed_file,console_rows,kind,maintain_project,maintenance_lock,settings


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.now=time.time()

    def file(self,name,text='diagnostic\n',days=30):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        os.utime(path,(self.now-days*86400,self.now-days*86400));return path

    def test_only_known_legacy_paths_are_candidates_and_dry_run_never_changes_files(self):
        good=self.file('runs/20260901-010203-004/bluetooth.jsonl')
        for name in ('runs/20260901-010203-004/pc.jsonl','runs/reminders/ledger.json','runs/data-access/latest.json',
                     'runs/test/bluetooth.jsonl','evidence/20260901-010203-004/bluetooth.jsonl','runs/recovery/app.apk'):
            self.file(name)
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report=maintain_project(self.root,self.now,dry_run=True)
        self.assertEqual(report['candidateFiles'],1);self.assertEqual(report['candidates'][0]['path'],str(good))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_old_diagnostic_is_verified_compressed_then_protected_for_seven_days(self):
        path=self.file('runs/20260901-010203-004/bluetooth.jsonl','中文\n'*20)
        business=self.file('runs/20260901-010203-004/pc.jsonl','business\n')
        report=maintain_project(self.root,self.now)
        self.assertEqual([r['action'] for r in report['actions']],['adopt','compress'])
        self.assertEqual(''.join(diagnostic_lines(path)),'中文\n'*20)
        self.assertFalse(path.exists());self.assertEqual(business.read_text(),'business\n')
        maintain_project(self.root,self.now+6*86400)
        self.assertTrue(managed_files(self.root))
        maintain_project(self.root,self.now+8*86400)
        self.assertEqual(managed_files(self.root),[]);self.assertFalse(path.with_name(path.name+'.index.json').exists())

    def test_new_writer_cannot_orphan_an_archived_stream_by_reusing_its_absent_base(self):
        path=self.file('runs/20260901-010203-004/bluetooth.jsonl','preserved\n')
        maintain_project(self.root,self.now);self.assertFalse(path.exists())
        index=path.with_name(path.name+'.index.json');before=index.read_bytes()
        with self.assertRaises(FileExistsError):DiagnosticLog(path)
        self.assertEqual(index.read_bytes(),before);self.assertEqual(''.join(diagnostic_lines(path)),'preserved\n')

    @unittest.skipUnless(os.name=='nt','Windows exclusion-handle behavior')
    def test_open_file_is_not_adopted_or_removed(self):
        path=self.file('runs/20260901-010203-004/bluetooth.jsonl')
        with path.open('ab'):
            with closed_file(path) as stream:self.assertIsNone(stream)
            report=maintain_project(self.root,self.now)
        self.assertEqual(report['actions'],[]);self.assertEqual(path.read_text(),'diagnostic\n')

    def test_pin_protects_existing_new_and_legacy_logs_from_capacity_and_expiry(self):
        old=self.file('runs/20260901-010203-004/bluetooth.jsonl')
        path=self.root/'runs/new/bluetooth.jsonl';log=DiagnosticLog(path);log.write('new\n');log.close()
        self.file('runs/.keep-logs','')
        report=maintain_project(self.root,self.now+40*86400)
        self.assertEqual(report['actions'],[]);self.assertTrue(old.exists() and path.exists())

    def test_console_recreated_at_fixed_path_does_not_overwrite_earlier_archive(self):
        path=self.file('runs/dashboard.stdout.txt','first\n')
        maintain_project(self.root,self.now)
        path.write_text('second\n');maintain_project(self.root,self.now+1)
        path.write_text('third\n');maintain_project(self.root,self.now+2)
        self.assertEqual(''.join(diagnostic_lines(path)),'first\nsecond\nthird\n')
        self.assertEqual(len(console_rows(self.root)),3)
        for _ in range(4):maintain_project(self.root,self.now+10*86400)
        self.assertEqual(console_rows(self.root),[])
        self.assertFalse(path.with_name(path.name+'.index.json').exists())

    def test_keep_all_prevents_old_console_and_diagnostic_deletion(self):
        self.file('runs/dashboard.stderr.txt','error\n')
        self.file('runs/20260901-010203-004/bluetooth.jsonl')
        from diagnostic_log import DEFAULTS as LIMITS
        atomic_json(self.root/'runs/logging-settings.json',dict(LIMITS,keepAllDiagnostics=True))
        for _ in range(4):maintain_project(self.root,self.now)
        maintain_project(self.root,self.now+60*86400)
        self.assertTrue(console_rows(self.root));self.assertTrue(managed_files(self.root))

    def test_console_capacity_removes_oldest_archives_after_migration_grace(self):
        atomic_json(self.root/'runs/log-cleanup-settings.json',dict(DEFAULTS,legacyGraceDays=1,consoleMaxTotalMiB=1))
        for i,name in enumerate(('runs/dashboard.stdout.txt','runs/dashboard.stderr.txt','runs/20260901-010203-004/receiver.stderr.txt')):
            path=self.file(name,days=2);path.write_bytes(os.urandom(600_000))
            stamp=self.now-2*86400+i;os.utime(path,(stamp,stamp))
        for _ in range(5):maintain_project(self.root,self.now)
        self.assertEqual(len(console_rows(self.root)),3)
        result=maintain_project(self.root,self.now+2*86400)
        self.assertEqual(len(result['actions']),2);self.assertTrue(all(a['action']=='delete' for a in result['actions']))
        self.assertEqual(len(console_rows(self.root)),1);self.assertLessEqual(result['consoleBytes'],1024**2)

    def test_manage_console_false_preserves_already_registered_archives(self):
        self.file('runs/dashboard.stdout.txt');maintain_project(self.root,self.now)
        atomic_json(self.root/'runs/log-cleanup-settings.json',dict(DEFAULTS,manageConsole=False))
        report=maintain_project(self.root,self.now+100*86400)
        self.assertEqual(report['actions'],[]);self.assertTrue(console_rows(self.root))

    def test_owner_alive_or_unknown_is_not_reclaimed_and_dead_owner_is_closed(self):
        path=self.root/'runs/new/bluetooth.jsonl';log=DiagnosticLog(path,keep_open=False);log.write('event\n')
        owner=log.metadata['ownerStarted']
        for identity in (owner,None):
            with patch('diagnostic_log.process_start',return_value=identity):
                self.assertEqual(maintain_project(self.root,self.now)['actions'],[])
        with patch('diagnostic_log.process_start',return_value=0):
            report=maintain_project(self.root,self.now)
        self.assertEqual(report['actions'][0]['action'],'close_orphan')
        self.assertTrue(json.loads((log.folder/'stream.json').read_text())['closed'])
        self.assertEqual(path.read_text(),'event\n')

    def test_bad_extended_policy_disables_all_cleanup(self):
        path=self.file('runs/dashboard.stdout.txt')
        atomic_json(self.root/'runs/log-cleanup-settings.json',dict(DEFAULTS,consoleRetentionDays=0))
        with self.assertRaises(ValueError):maintain_project(self.root,self.now)
        self.assertTrue(path.exists());self.assertFalse((self.root/'runs/diagnostics').exists())

    def test_console_manifest_cannot_adopt_history_or_outside_file(self):
        path=self.file('runs/dashboard.stdout.txt');maintain_project(self.root,self.now)
        row=console_rows(self.root)[0];meta=json.loads((row['folder']/'stream.json').read_text())
        meta['source']='runs/20260901-010203-004/pc.jsonl';atomic_json(row['folder']/'stream.json',meta)
        self.assertEqual(console_rows(self.root),[])

    def test_compression_detects_source_changes_and_preserves_original(self):
        path=self.file('runs/20260901-010203-004/bluetooth.jsonl')
        row=candidates(self.root,self.now,settings(self.root))[0];adopt(self.root,row,self.now,DEFAULTS)
        original=gzip.open
        def altered(file,mode,*args,**kwargs):
            if mode=='rb':path.write_text('changed\n')
            return original(file,mode,*args,**kwargs)
        with patch('diagnostic_log.gzip.open',side_effect=altered):
            with self.assertRaises(OSError):maintain_project(self.root,self.now)
        self.assertEqual(path.read_text(),'changed\n')

    def test_total_actions_remain_bounded_across_all_categories(self):
        for i in range(4):self.file(f'runs/20260901-010203-{i:03}/bluetooth.jsonl')
        self.file('runs/dashboard.stdout.txt')
        for _ in range(8):self.assertLessEqual(len(maintain_project(self.root,self.now)['actions']),2)

    def test_exclusion_lock_skips_another_run(self):
        with maintenance_lock(self.root):
            # Windows mutex is recursive on the same thread; use another thread to model another caller.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(1) as pool:report=pool.submit(maintain_project,self.root,self.now).result()
            self.assertEqual(report['actions'],[]);self.assertIn('skipped',report)

    def test_cleanup_history_is_bounded_and_does_not_store_record_contents(self):
        from log_retention import record_report
        for i in range(105):record_report(self.root,dict(actions=[dict(action='compress',path=f'log-{i}')]))
        rows=json.loads((self.root/'runs/log-cleanup-history.json').read_text())
        self.assertEqual(len(rows),100);self.assertEqual(rows[0]['examples'][0]['path'],'log-5')
        self.assertEqual(rows[-1]['counts'],dict(compress=1))
