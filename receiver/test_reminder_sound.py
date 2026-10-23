import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from reminder_protocol import normalize,fingerprint
from reminder_policy import DEFAULTS,PolicyRegister,PolicyStore,validate_settings,violation


class ReminderSoundTests(unittest.TestCase):
    def request(self,**kw):return dict(reminderId='sound',title='t',body='b',ttlMs=60000,**kw)
    def test_legacy_payload_fingerprint_is_unchanged_and_sound_requires_new_id(self):
        raw=self.request();old=['t','b',True,True,255,360,220,2,15]
        old_hash=hashlib.sha256(json.dumps(old,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(fingerprint(raw),old_hash)
        self.assertEqual(fingerprint(raw),fingerprint(dict(raw,sound=False,soundSource='system')))
        self.assertNotEqual(fingerprint(raw),fingerprint(dict(raw,sound=True)))
    def test_legacy_saved_policy_migrates_to_sound_off_without_identity_conflict(self):
        register=PolicyRegister('pc');register.edit(DEFAULTS);wire=register.wire()
        del wire['pc']['value']['soundEnabled']
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'policy.json';path.write_text(json.dumps(dict(state=wire)),encoding='utf-8')
            store=PolicyStore(path);self.assertIsNone(store.error);self.assertFalse(store.register.effective()['soundEnabled'])
            before=store.register.fingerprint();store.register.merge(wire)
            self.assertEqual(before,store.register.fingerprint())
            store.register.merge(register.wire())
            self.assertEqual(before,store.register.fingerprint())
    def test_sound_permission_and_concurrent_disable_are_independent_of_other_effects(self):
        params=normalize(self.request(sound=True,vibrate=False,screen=False))
        self.assertEqual(violation(params,DEFAULTS)['reason'],'sound_disabled_by_user')
        self.assertIsNone(violation(params,dict(DEFAULTS,soundEnabled=True,vibrationEnabled=False,wakeEnabled=False)))
        p,w=PolicyRegister('pc'),PolicyRegister('watch')
        p.edit(dict(DEFAULTS,soundEnabled=True));w.edit(DEFAULTS);p.merge(w.wire());w.merge(p.wire())
        self.assertFalse(p.effective()['soundEnabled']);self.assertEqual(p.effective(),w.effective())
    def test_sources_are_explicit_and_reserved_custom_is_rejected(self):
        self.assertEqual(normalize(self.request())['soundSource'],'system')
        for values in [dict(sound=1),dict(sound='true'),dict(soundSource='custom'),dict(soundSource='file:///sdcard/a.ogg'),dict(soundSource=None)]:
            with self.assertRaises(ValueError):normalize(self.request(**values))
        with self.assertRaises(ValueError):validate_settings(dict(DEFAULTS,soundEnabled='true'))


if __name__=='__main__':unittest.main()
