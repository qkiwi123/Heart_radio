import tempfile
import unittest
from unittest.mock import Mock
from reminders import Reminders
import reminder_tools


class ReminderToolTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.wire=Mock(side_effect=lambda op,p:dict(status='issued',reminderId=p['reminderId'],vibrationIssued=True))
        self.reminders=Reminders(self.folder.name,self.wire)
        self.args=dict(reminder_id='unique',title='提醒',body='你好')

    def test_maps_units_and_custom_fields_to_shared_journal(self):
        row=reminder_tools.call(self.reminders,'send_reminder',dict(self.args,ttl_seconds=120,screen_seconds=30,
            vibration=dict(pulse_ms=300,gap_ms=500,count=3,amplitude=255)))
        self.assertTrue(row['ok']);self.assertFalse(row['automaticallyRetried'])
        op,p=self.wire.call_args.args
        self.assertEqual(op,'remind');self.assertEqual(p['ttlMs'],120000)
        self.assertEqual(p['vibration'],dict(pulseMs=300,gapMs=500,count=3,amplitude=255))
        self.assertEqual(p['screenSeconds'],30)

    def test_sound_is_explicit_and_cannot_bypass_user_permission(self):
        from reminder_policy import DEFAULTS
        blocked=Reminders(self.folder.name,self.wire,policy=lambda:DEFAULTS)
        row=reminder_tools.call(blocked,'send_reminder',dict(self.args,sound=True,sound_source='system'))
        self.assertEqual(row['policyReason'],'sound_disabled_by_user');self.wire.assert_not_called()
        allowed=Reminders(self.folder.name+'/allowed',self.wire,policy=lambda:dict(DEFAULTS,soundEnabled=True))
        reminder_tools.call(allowed,'send_reminder',dict(self.args,sound=True,sound_source='system',vibrate=False,screen=False))
        params=self.wire.call_args.args[1]
        self.assertTrue(params['sound']);self.assertEqual(params['soundSource'],'system')
        self.wire.reset_mock()
        with self.assertRaises(ValueError):reminder_tools.call(allowed,'send_reminder',dict(self.args,sound_source='custom'))
        self.wire.assert_not_called()

    def test_mcp_and_web_share_dedup_across_restore(self):
        first=reminder_tools.call(self.reminders,'send_reminder',self.args)
        again=self.reminders.send(dict(reminderId='unique',title='提醒',body='你好',ttlMs=300000))
        self.assertTrue(again['duplicate']);self.assertEqual(first['pcExpiresAtMs'],again['pcExpiresAtMs'])
        restored=Reminders(self.folder.name,self.wire)
        self.assertTrue(reminder_tools.call(restored,'send_reminder',self.args)['duplicate'])
        self.wire.assert_called_once()

    def test_invalid_nested_fields_required_values_and_total_are_rejected_before_wire(self):
        for change in [dict(reminder_id=''),dict(ttl_seconds=True),dict(ttl_seconds=301),dict(title=' '),
                       dict(vibration={'pulseMs':300}),dict(vibration={'pulse_ms':1000,'gap_ms':2000,'count':8}),
                       dict(screen='true'),dict(agent_on=True)]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                reminder_tools.call(self.reminders,'send_reminder',dict(self.args,**change))
        with self.assertRaises(ValueError):reminder_tools.call(self.reminders,'send_reminder',dict(title='x',body='y'))
        self.wire.assert_not_called()

    def test_saved_failure_is_queryable_offline_but_fresh_remains_gated(self):
        self.wire.side_effect=None;self.wire.return_value=dict(status='interaction_disabled')
        sent=reminder_tools.call(self.reminders,'send_reminder',self.args)
        self.assertFalse(sent['ok']);self.wire.reset_mock()
        local=reminder_tools.call(self.reminders,'get_reminder_status',dict(reminder_id='unique'))
        self.assertTrue(local['ok']);self.assertEqual(local['source'],'pc_reminder_journal');self.wire.assert_not_called()
        fresh=reminder_tools.call(self.reminders,'get_reminder_status',dict(reminder_id='unique',fresh=True))
        self.assertFalse(fresh['ok']);self.assertEqual(fresh['status'],'interaction_disabled')

    def test_timeout_is_not_retried_or_mistaken_for_delivery(self):
        self.wire.side_effect=None;self.wire.return_value=dict(status='outcome_unknown_timeout')
        value=reminder_tools.call(self.reminders,'send_reminder',self.args)
        self.assertFalse(value['ok'])
        duplicate=reminder_tools.call(self.reminders,'send_reminder',self.args)
        self.assertTrue(duplicate['duplicate']);self.wire.assert_called_once()

    def test_acknowledgement_is_additive_and_query_does_not_send(self):
        reminder_tools.call(self.reminders,'send_reminder',self.args)
        self.reminders.observe(dict(reminderId='unique',acknowledgedAtMs=123))
        self.wire.reset_mock()
        row=reminder_tools.call(self.reminders,'get_reminder_status',dict(reminder_id='unique'))
        self.assertTrue(row['ok']);self.assertEqual(row['status'],'acknowledged');self.wire.assert_not_called()

    def test_missing_receipt_and_invalid_refresh_are_explicit(self):
        value=reminder_tools.call(self.reminders,'get_reminder_status',dict(reminder_id='absent'))
        self.assertFalse(value['ok']);self.assertEqual(value['status'],'not_found')
        with self.assertRaises(ValueError):reminder_tools.call(self.reminders,'get_reminder_status',dict(reminder_id='absent',fresh=1))
        self.wire.assert_not_called()


if __name__=='__main__':unittest.main()
