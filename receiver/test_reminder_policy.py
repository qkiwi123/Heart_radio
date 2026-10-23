import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from reminder_policy import DEFAULTS,PolicyRegister,PolicyStore,validate_wire,validate_settings,violation,stricter
from reminder_protocol import normalize


class ReminderPolicyTests(unittest.TestCase):
    def test_defaults_are_six_seconds_and_existing_flags(self):
        self.assertEqual(PolicyRegister('pc').effective(),DEFAULTS);self.assertEqual(DEFAULTS['maxVibrationMs'],6000)
    def test_concurrent_stricter_converges_then_explicit_edit_can_raise(self):
        p,w=PolicyRegister('pc'),PolicyRegister('watch')
        p.edit(dict(DEFAULTS,maxVibrationMs=9000));w.edit(dict(DEFAULTS,maxVibrationMs=3000,vibrationEnabled=False,maxScreenSeconds=10))
        p.merge(w.wire());w.merge(p.wire());self.assertEqual(p.effective(),w.effective());self.assertEqual(p.effective()['maxVibrationMs'],3000);self.assertFalse(p.effective()['vibrationEnabled'])
        p.edit(dict(DEFAULTS,maxVibrationMs=8000));w.merge(p.wire());self.assertEqual(w.effective(),p.effective());self.assertEqual(w.effective()['maxVibrationMs'],8000)
    def test_merge_is_idempotent_without_inventing_human_edits(self):
        p,w=PolicyRegister('pc'),PolicyRegister('watch');p.edit(DEFAULTS);w.merge(p.wire());before=w.fingerprint();w.merge(p.wire());self.assertEqual(before,w.fingerprint());self.assertIsNone(w.events['watch'])
    def test_gaps_count_and_user_flags_are_enforced(self):
        p=normalize(dict(reminderId='one',title='t',body='b',ttlMs=60000,vibration=dict(pulseMs=1000,gapMs=1500,count=3)))
        self.assertIsNone(violation(p,DEFAULTS))
        p['vibration']['gapMs']=1501;self.assertEqual(violation(p,DEFAULTS)['reason'],'vibration_exceeds_user_limit')
        self.assertEqual(violation(p,dict(DEFAULTS,vibrationEnabled=False))['status'],'reminder_policy_denied')
        p['vibrate']=False;self.assertEqual(violation(p,dict(DEFAULTS,wakeEnabled=False))['reason'],'wake_disabled_by_user')
    def test_unknown_types_ranges_and_missing_history_rejected(self):
        for v in [dict(DEFAULTS,maxVibrationMs=True),dict(DEFAULTS,maxScreenSeconds=31),dict(DEFAULTS,extra=1)]:
            with self.assertRaises(ValueError):validate_settings(v)
        p=PolicyRegister('pc');p.edit(DEFAULTS);wire=p.wire();wire['pc']['seen']=1
        with self.assertRaises(ValueError):validate_wire(wire)
    def test_conflicting_equal_version_does_not_overwrite_register(self):
        p=PolicyRegister('pc');p.edit(DEFAULTS);wire=p.wire();wire['pc']['value']['maxVibrationMs']=3000
        with self.assertRaises(ValueError):p.merge(wire)
        self.assertEqual(p.effective()['maxVibrationMs'],6000)
    def test_atomic_failure_retains_previous_version_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';s=PolicyStore(path);s.transact(lambda r:r.edit(DEFAULTS));before=path.read_bytes();version=s.register.fingerprint()
            s.save=Mock(side_effect=OSError('test disk failure'))
            with self.assertRaises(OSError):s.transact(lambda r:r.edit(dict(DEFAULTS,maxVibrationMs=2000)))
            self.assertEqual(path.read_bytes(),before);self.assertEqual(s.register.fingerprint(),version);self.assertTrue(s.error)
    def test_restart_retains_settings_and_identical_merge_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';s=PolicyStore(path);s.transact(lambda r:r.edit(dict(DEFAULTS,maxVibrationMs=2000)))
            save=Mock();r=PolicyStore(path,save=save);r.transact(lambda x:x.merge(s.register.wire()));save.assert_not_called();self.assertEqual(r.register.fingerprint(),s.register.fingerprint())
    def test_corrupt_state_is_preserved_and_not_implicitly_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';path.write_text('{bad');s=PolicyStore(path)
            with self.assertRaises(OSError):s.transact(lambda r:r.edit(DEFAULTS))
            self.assertEqual(path.read_text(),'{bad')
    def test_recovery_can_only_tighten_unknown_peer_history(self):
        self.assertEqual(stricter(DEFAULTS,dict(DEFAULTS,maxVibrationMs=10000,wakeEnabled=False)),dict(DEFAULTS,wakeEnabled=False))


if __name__=='__main__':unittest.main()
