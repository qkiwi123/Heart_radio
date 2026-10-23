import copy
import unittest
from reminder_protocol import normalize,fingerprint,validate_status
from capability_bridge import validate_request


class ReminderProtocolTests(unittest.TestCase):
    def request(self,**kw):return dict(reminderId='test-1',title='喝水提醒',body='请稍作休息。\n点击知道了。',ttlMs=60000,**kw)
    def test_strong_default_and_silent_notification_templates(self):
        p=normalize(self.request());self.assertEqual(p['vibration'],dict(amplitude=255,pulseMs=360,gapMs=220,count=2))
        for template,flags in [('silent',(False,True)),('notification_only',(False,False)),('light',(True,True))]:
            value=normalize(self.request(template=template));self.assertEqual((value['vibrate'],value['screen']),flags)
    def test_custom_overrides_template_and_total_is_bounded(self):
        value=normalize(self.request(template='light',vibration=dict(count=3,pulseMs=300,gapMs=500,amplitude=255)))
        self.assertEqual(value['vibration'],dict(count=3,pulseMs=300,gapMs=500,amplitude=255))
        with self.assertRaises(ValueError):normalize(self.request(vibration=dict(count=8,pulseMs=1000,gapMs=2000)))
    def test_rejects_invalid_types_ids_text_expiry_and_unknown_fields(self):
        bads=[dict(vibrate=1),dict(screen='true'),dict(vibration=dict(count=True)),dict(vibration=dict(pulseMs=300.0)),
              dict(vibration=dict(amplitude=256)),dict(vibration=dict(gapMs=-1)),dict(vibration=dict(loop=True)),dict(screenSeconds=31),
              dict(extra=True),dict(template='alarm'),dict(strength='maximum')]
        for bad in bads:
            with self.assertRaises(ValueError,msg=str(bad)):normalize(dict(self.request(),**bad))
        for key,value in [('reminderId','../old'),('title',' '),('body','字'*501),('body','x\0y'),('body','\ud800'),('ttlMs',0),('ttlMs',300001)]:
            with self.assertRaises(ValueError):normalize(dict(self.request(),**{key:value}))
        p=self.request();p['expiresAtMs']=123
        with self.assertRaises(ValueError):normalize(p)
    def test_full_unicode_characters_and_legacy_absolute_deadline(self):
        p=self.request();p.update(title='😀'*60);self.assertEqual(len(normalize(p)['title']),60)
        p.pop('ttlMs');p['expiresAtMs']=10000;self.assertEqual(normalize(p)['expiresAtMs'],10000)
    def test_same_id_deadline_changes_do_not_change_content_fingerprint(self):
        a=self.request();b=dict(a,ttlMs=200000)
        self.assertEqual(fingerprint(a),fingerprint(b));self.assertNotEqual(fingerprint(a),fingerprint(dict(a,body='不同内容')))
    def test_wifi_adapter_accepts_same_normalized_request_and_status_bounds(self):
        p=self.request(vibration=dict(count=3,pulseMs=300,gapMs=500))
        self.assertEqual(validate_request(dict(op='remind',params=p))['params'],normalize(p))
        self.assertEqual(validate_request(dict(op='reminder_status',params={'reminderId':'test-1'}))['op'],'reminder_status')
        with self.assertRaises(ValueError):validate_status(dict(reminderId='test-1',fresh=True))


if __name__=='__main__':unittest.main()
