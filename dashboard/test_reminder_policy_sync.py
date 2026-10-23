import tempfile
import unittest
from unittest.mock import Mock
from transport_access import TransportAccess
from reminder_policy import DEFAULTS,PolicyRegister
from reminder_protocol import normalize


class PolicySyncTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.t=TransportAccess(self.folder.name);self.t.managed=True;self.t.selected='rfcomm';self.t.selection_id='a'*32;self.t.selected_key=1
        self.peer=PolicyRegister('watch');self.peer.edit(dict(DEFAULTS,maxVibrationMs=3000))
        def wire(route,op,params=None,request_id=None):
            self.assertEqual(op,'sync_reminder_policy');self.peer.merge(params['state'])
            return dict(status='ok',syncId=params['syncId'],policy=dict(state=self.peer.wire()))
        self.t._raw=Mock(side_effect=wire)
    def test_watch_edit_arrives_and_later_pc_edit_is_confirmed(self):
        self.t._sync_reminder_policy(self.t._token());state=self.t.reminder_policy_state()
        self.assertTrue(state['peerConfirmed']);self.assertEqual(state['limits']['maxVibrationMs'],3000)
        self.t.edit_reminder_policy(dict(DEFAULTS,maxVibrationMs=8000));self.assertFalse(self.t.reminder_policy_state()['peerConfirmed'])
        self.t._sync_reminder_policy(self.t._token());self.assertTrue(self.t.reminder_policy_state()['peerConfirmed']);self.assertEqual(self.peer.effective()['maxVibrationMs'],8000)
    def test_offline_edit_does_not_start_connection_or_interaction(self):
        self.t.selected=None;state=self.t.edit_reminder_policy(dict(DEFAULTS,wakeEnabled=False))
        self.assertFalse(state['peerConfirmed']);self.assertFalse(self.t.agent_enabled);self.assertIsNone(self.t.manager_thread);self.t._raw.assert_not_called()
    def test_stale_selection_cannot_acknowledge_or_apply_reply(self):
        original=self.t._raw.side_effect
        def race(*args,**kwargs):result=original(*args,**kwargs);self.t.selection_id='b'*32;return result
        self.t._raw.side_effect=race;self.t._sync_reminder_policy(self.t._token())
        self.assertFalse(self.t.reminder_policy_state()['peerConfirmed']);self.assertEqual(self.t.reminder_limits()['maxVibrationMs'],6000)
    def test_new_reminder_is_blocked_until_policy_confirmation(self):
        self.t._limits=lambda:None;self.t._sync_confirmed=lambda:True;self.t.agent_enabled=True
        p=normalize(dict(reminderId='one',title='t',body='b',ttlMs=60000))
        self.assertEqual(self.t.request('remind',p)['status'],'reminder_policy_sync_pending');self.t._raw.assert_not_called()


if __name__=='__main__':unittest.main()
