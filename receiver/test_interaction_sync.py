import copy
from pathlib import Path
import tempfile
import unittest
from interaction_sync import InteractionRegister,InteractionStore,validate_wire


class InteractionSyncTests(unittest.TestCase):
    def setUp(self):
        self.now=1000
        self.pc=InteractionRegister('pc',lambda:self.now)
        self.watch=InteractionRegister('watch',lambda:self.now+900000)  # Different device epochs.

    def settle(self):
        for _ in range(3):
            self.watch.merge(self.pc.wire());self.pc.merge(self.watch.wire())
        self.assertEqual(self.pc.fingerprint(),self.watch.fingerprint())
        self.assertEqual(self.pc.enabled,self.watch.enabled)

    def test_both_ends_can_open_close_then_reopen_without_double_operation(self):
        self.pc.edit(True,60000);self.settle();self.assertTrue(self.watch.enabled)
        self.watch.edit(False);self.settle();self.assertFalse(self.pc.enabled)
        self.watch.edit(True);self.settle();self.assertTrue(self.pc.enabled)
        self.pc.edit(False);self.settle();self.assertFalse(self.watch.enabled)

    def test_concurrent_offline_close_wins_then_explicit_open_can_override(self):
        self.pc.edit(True);self.settle()
        self.pc.edit(False);self.pc.edit(True)
        self.watch.edit(False)
        self.settle();self.assertFalse(self.pc.enabled)
        self.pc.edit(True);self.settle();self.assertTrue(self.watch.enabled)

    def test_concurrent_opens_keep_earliest_finite_lease(self):
        self.pc.edit(True,10000);self.watch.edit(True,5000)
        self.settle();self.assertEqual(self.pc.remaining(),5000)
        self.now+=5001;self.watch.expire();self.settle();self.assertFalse(self.pc.enabled)

    def test_delayed_duplicate_and_reconnect_do_not_extend_deadline(self):
        self.pc.edit(True,10000);old=self.pc.wire();self.settle()
        end=self.watch.deadlines.copy();self.now+=3000
        self.watch.merge(old);self.watch.merge(old);self.pc.edit(True,50000);self.settle()
        self.assertEqual(self.watch.deadlines,end);self.assertEqual(self.pc.remaining(),7000)

    def test_expiry_is_a_causal_close_and_old_open_cannot_restore_it(self):
        self.pc.edit(True,2000);old=self.pc.wire();self.settle();self.now+=2001
        self.pc.expire();self.settle();self.watch.merge(old)
        self.assertFalse(self.watch.enabled)

    def test_explicit_duration_edit_propagates_once(self):
        self.watch.edit(True,10000);self.settle();self.now+=1000
        self.pc.edit(True,3000,force=True);self.settle();self.now+=1000;self.settle()
        self.assertEqual(self.watch.remaining(),2000)

    def test_duplicate_version_with_different_identity_rejected_atomically(self):
        self.pc.edit(True);self.settle();old=self.watch.wire()
        bad=copy.deepcopy(old);bad['pc']['id']='f'*32
        with self.assertRaises(ValueError):self.watch.merge(bad)
        self.assertEqual(self.watch.wire(),old)

    def test_malformed_leases_cycles_missing_history_rejected(self):
        self.pc.edit(True,1000);self.watch.edit(False)
        wire=dict(pc=self.pc.wire()['pc'],watch=self.watch.wire()['watch'])
        for mutate in [lambda s:s['pc'].update(seq=True),lambda s:s['pc'].update(remainingMs=1001),
                       lambda s:s['watch'].update(ttlMs=100,remainingMs=10),lambda s:s['pc'].update(seen=5),
                       lambda s:(s['pc'].update(seen=1),s['watch'].update(seen=1))]:
            bad=copy.deepcopy(wire);mutate(bad)
            with self.assertRaises(ValueError):validate_wire(bad)
        self.assertEqual(validate_wire(dict(reversed(list(wire.items())))),wire)

    def test_stale_message_order_converges_without_reopening(self):
        self.pc.edit(True);stale=self.pc.wire();self.settle()
        self.watch.edit(False);close=self.watch.wire();self.pc.merge(close)
        for snapshot in [stale,close,stale,close]:self.watch.merge(snapshot)
        self.settle();self.assertFalse(self.pc.enabled)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'gate.json';self.now=1000

    def store(self,boot='boot-a',**kwargs):
        return InteractionStore(self.path,now=lambda:self.now,boot=boot,**kwargs)

    def test_process_restart_preserves_deadline_without_renewal(self):
        first=self.store();first.transact(lambda r:r.edit(True,10000));self.now+=3500
        second=self.store();self.assertTrue(second.enabled);self.assertEqual(second.register.remaining(),6500)
        self.now+=6501;third=self.store();self.assertFalse(third.enabled)

    def test_new_boot_closes_even_unlimited_and_persists_close(self):
        first=self.store();first.transact(lambda r:r.edit(True));second=self.store('boot-b')
        self.assertFalse(second.enabled);self.assertFalse(self.store('boot-b').enabled)

    def test_failed_save_does_not_commit_or_ack_open(self):
        first=self.store();first.transact(lambda r:r.edit(False));old=self.path.read_bytes()
        def failed(*args):raise OSError('test disk failure')
        first.save=failed
        with self.assertRaises(OSError):first.transact(lambda r:r.edit(True,1000))
        self.assertFalse(first.enabled);self.assertFalse(first.register.enabled);self.assertEqual(self.path.read_bytes(),old)

    def test_corrupt_file_fails_closed_and_is_preserved(self):
        self.path.write_text('{bad',encoding='utf-8');store=self.store()
        self.assertFalse(store.enabled);self.assertTrue(store.error);self.assertEqual(self.path.read_text(),'{bad')


if __name__=='__main__':unittest.main()
