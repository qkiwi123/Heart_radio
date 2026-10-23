import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from widget_layout import LayoutStore


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LayoutStore(self.temp.name)

    def test_stale_drag_cannot_undo_a_dashboard_lock(self):
        self.store.patch('current',dict(left=10,width=360),'a'*32,1)
        self.store.set_locked('current',True)
        value=self.store.patch('current',dict(left=99,width=400),'a'*32,2)
        self.assertEqual(value['status'],'locked')
        self.assertEqual(value['layout']['current']['left'],10)
        self.assertTrue(value['layout']['current']['locked'])
        with self.assertRaises(ValueError):self.store.patch('current',dict(locked=False),'a'*32,3)

    def test_out_of_order_retries_and_two_windows_do_not_overwrite_each_other(self):
        self.store.patch('current',dict(left=40),'b'*32,2)
        self.store.patch('trend',dict(width=500),'b'*32,3)
        old=self.store.patch('current',dict(left=1),'b'*32,1)
        self.assertEqual(old['status'],'already_applied')
        self.assertEqual(old['layout']['current']['left'],40)
        self.assertEqual(old['layout']['trend']['width'],500)

    def test_interleaved_field_updates_are_serialized(self):
        start=threading.Barrier(3)
        def move():
            start.wait()
            for seq in range(1,41):self.store.patch('current',dict(left=seq),'c'*32,seq)
        def resize():
            start.wait()
            for seq in range(1,41):self.store.patch('trend',dict(width=400+seq),'d'*32,seq)
        def lock():
            start.wait();self.store.set_locked('current',True)
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for result in [pool.submit(move),pool.submit(resize),pool.submit(lock)]:result.result()
        value=self.store.snapshot()['layout']
        self.assertTrue(value['current']['locked']);self.assertEqual(value['trend']['width'],440)
        self.assertEqual(json.loads(self.store.path.read_text())['trend']['width'],440)

    def test_failed_publish_keeps_old_layout_and_same_sequence_can_retry(self):
        self.store.patch('current',dict(left=1),'e'*32,1)
        before=self.store.path.read_bytes();atomic=self.store.atomic
        def fail(path,value):
            if path==self.store.path:raise OSError('disk full')
            return atomic(path,value)
        with patch.object(self.store,'atomic',side_effect=fail):
            with self.assertRaises(OSError):self.store.patch('current',dict(left=2),'e'*32,2)
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(self.store.patch('current',dict(left=2),'e'*32,2)['status'],'saved')

    def test_invalid_primary_uses_last_good_without_destroying_evidence(self):
        self.store.patch('current',dict(left=12),'f'*32,1)
        self.store.set_locked('trend',True)
        self.store.path.write_text('{broken',encoding='utf-8')
        result=self.store.snapshot()
        self.assertEqual(result['layout']['current']['left'],12);self.assertTrue(result['storageError'])
        self.assertEqual(self.store.path.read_text(),'{broken')

    def test_unknown_fields_and_nonfinite_geometry_never_reach_disk(self):
        for changes in [dict(path='other'),dict(left=float('nan')),dict(width=True),dict(width=1)]:
            with self.assertRaises(ValueError):self.store.patch('current',changes,'a'*32,1)
        self.assertFalse(self.store.path.exists())
