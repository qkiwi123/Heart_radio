import gzip
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,Mock
from diagnostic_log import DiagnosticLog,diagnostic_lines,diagnostic_tail,maintain,managed_files,DEFAULTS,_compress


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'runs/session/bluetooth.jsonl'

    def test_rotation_preserves_every_unicode_record_and_compression_order(self):
        log=DiagnosticLog(self.path,max_bytes=180)
        expected=[json.dumps(dict(i=i,text='中文'*10),ensure_ascii=False)+'\n' for i in range(20)]
        for line in expected:log.write(line)
        log.close()
        self.assertEqual(list(diagnostic_lines(self.path)),expected)
        self.assertEqual(diagnostic_tail(self.path,5),expected[-5:])
        self.assertGreater(len(managed_files(self.root)),1)
        for _ in range(20):
            report=maintain(self.root,now=time.time()+3*86400)
            self.assertLessEqual(len(report['actions']),2)
            if not report['actions']:break
        self.assertEqual(list(diagnostic_lines(self.path)),expected)
        self.assertFalse(self.path.exists())
        self.assertEqual(diagnostic_tail(self.path,5),expected[-5:])

    def test_legacy_business_files_and_active_base_are_never_cleaned(self):
        log=DiagnosticLog(self.path,max_bytes=100);log.write('new diagnostic\n')
        old=self.root/'runs/old/bluetooth.jsonl';old.parent.mkdir();old.write_text('old raw\n')
        business=self.root/'runs/session/pc.jsonl';business.write_text('business history\n')
        ledger=self.root/'runs/reminders/ledger.json';ledger.parent.mkdir();ledger.write_text('{"keep":true}')
        try:
            report=maintain(self.root,now=time.time()+30*86400)
            self.assertEqual(report['actions'],[])
            self.assertEqual(self.path.read_text(),'new diagnostic\n')
            self.assertEqual(old.read_text(),'old raw\n');self.assertEqual(business.read_text(),'business history\n')
            self.assertEqual(ledger.read_text(),'{"keep":true}')
        finally:log.close()

    def test_only_closed_managed_files_expire_with_bounded_cleanup(self):
        log=DiagnosticLog(self.path,max_bytes=32)
        for _ in range(8):log.write('x'*30+'\n')
        log.close();before=len(managed_files(self.root))
        report=maintain(self.root,now=time.time()+15*86400)
        self.assertEqual(len(report['actions']),2);self.assertEqual(len(managed_files(self.root)),before-2)
        self.assertTrue(all(r['action']=='delete' for r in report['actions']))
        self.assertTrue(report['moreWork'])
        for _ in range(10):
            report=maintain(self.root,now=time.time()+15*86400)
            if not report['moreWork']:break
        self.assertEqual(managed_files(self.root),[])
        self.assertFalse(self.path.with_name(self.path.name+'.index.json').exists())
        self.assertFalse(log.folder.exists())

    def test_capacity_deletes_oldest_closed_diagnostic_before_retention_age(self):
        (self.root/'runs').mkdir()
        (self.root/'runs/logging-settings.json').write_text(json.dumps(dict(DEFAULTS,maxTotalMiB=10)))
        paths=[]
        for i in range(3):
            path=self.root/f'runs/session{i}/bluetooth.jsonl';paths.append(path)
            log=DiagnosticLog(path);log.write('x'*(4*1024**2));log.close()
        report=maintain(self.root)
        self.assertEqual(len(report['actions']),1)
        self.assertEqual(report['actions'][0]['path'],str(paths[0].resolve()))
        self.assertFalse(paths[0].exists());self.assertTrue(paths[1].exists() and paths[2].exists())
        self.assertLessEqual(report['eligibleBytes'],report['limitBytes'])
        self.assertFalse(report['moreWork'])

    def test_debug_keep_all_disables_expiry_and_capacity_deletion(self):
        log=DiagnosticLog(self.path);log.write('retained\n');log.close()
        (self.root/'runs/logging-settings.json').write_text(json.dumps(dict(DEFAULTS,keepAllDiagnostics=True)))
        report=maintain(self.root,now=time.time()+40*86400)
        self.assertFalse(any(row['action']=='delete' for row in report['actions']))
        self.assertEqual(''.join(diagnostic_lines(self.path)),'retained\n')

    def test_bad_policy_disables_cleanup_without_blocking_new_diagnostics(self):
        (self.root/'runs').mkdir();(self.root/'runs/logging-settings.json').write_text('{bad')
        log=DiagnosticLog(self.path);log.write('still writes\n');log.close()
        self.assertTrue(log.error)
        with self.assertRaises(ValueError):maintain(self.root,now=time.time()+40*86400)
        self.assertEqual(self.path.read_text(),'still writes\n')

    def test_compression_verification_failure_keeps_original(self):
        log=DiagnosticLog(self.path);log.write('must survive\n');log.close()
        row=managed_files(self.root)[0];original=gzip.open
        def corrupt(path,mode,*args,**kwargs):
            return io.BytesIO(b'wrong archive') if mode=='rb' else original(path,mode,*args,**kwargs)
        with patch('diagnostic_log.gzip.open',side_effect=corrupt):
            with self.assertRaises(OSError):_compress(row)
        self.assertEqual(self.path.read_text(),'must survive\n')
        self.assertFalse(list(row['folder'].glob('*.gz')))

    def test_logging_write_failure_is_reported_without_breaking_business_caller(self):
        log=DiagnosticLog(self.path);log.stream.close()
        log.stream=Mock();log.stream.write.side_effect=OSError('disk full')
        self.assertEqual(log.write('event\n'),0);self.assertTrue(log.error)
        self.assertEqual(log.write('another\n'),0);log.close()

    def test_creation_flush_and_close_errors_do_not_break_business_caller(self):
        with patch.object(Path,'open',side_effect=PermissionError('read only')):
            log=DiagnosticLog(self.path)
        self.assertTrue(log.error);self.assertEqual(log.write('event\n'),0);log.close()
        log=DiagnosticLog(self.path);log.stream.close();log.stream=Mock()
        log.stream.flush.side_effect=OSError('disk full');log.stream.close.side_effect=OSError('disk full')
        log.flush();log.close();self.assertTrue(log.error);self.assertTrue(log.closed)

    def test_manifest_cannot_adopt_an_outside_file(self):
        log=DiagnosticLog(self.path);log.close();meta=log.metadata
        meta['source']='../outside/bluetooth.jsonl'
        (log.folder/'stream.json').write_text(json.dumps(meta))
        self.assertEqual(managed_files(self.root),[])

    def test_existing_file_is_never_overwritten_or_adopted(self):
        self.path.parent.mkdir(parents=True);self.path.write_text('legacy\n')
        with self.assertRaises(FileExistsError):DiagnosticLog(self.path)
        self.assertEqual(self.path.read_text(),'legacy\n')
