import tempfile
import unittest
from unittest.mock import Mock

from interaction_sync import InteractionRegister
from transport_access import TransportAccess
from capability_bridge import validate_request


class BroadcastTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.access=TransportAccess(self.tmp.name);self.access.managed=True
        self.access.selected='rfcomm';self.access.selected_key=1;self.access.selection_id='a'*32
        self.access.candidates={'rfcomm':dict(connected=True),'wifi':dict(connected=False)}
        self.watch=InteractionRegister('watch');self.running=False;self.pending=False
        self.access._raw=Mock(side_effect=self.reply)
    def reply(self,route,op,params=None,request_id=None):
        if op!='sync_broadcast':return dict(status='unsupported')
        self.watch.merge(params['state'])
        if not self.pending:self.running=self.watch.enabled
        return dict(status='ok',syncId=params['syncId'],broadcast=dict(register=self.watch.wire(),running=self.running,registered=self.running,effectError=''))
    def sync(self):
        for _ in range(2):self.access._sync_broadcast(self.access._token())
    def test_pc_can_start_and_stop_broadcast_with_agent_permission_closed(self):
        self.access._edit_broadcast(True);self.sync()
        self.assertTrue(self.running);self.assertTrue(self.access._broadcast_state()['peerConfirmed'])
        self.assertFalse(self.access.agent_enabled)
        self.access._edit_broadcast(False);self.sync()
        self.assertFalse(self.running);self.assertFalse(self.access.cache.receiving)
    def test_watch_changes_follow_on_pc_and_prepare_receiving(self):
        self.access.cache.set_receiving(False)
        self.watch.edit(True);self.sync()
        self.assertTrue(self.access._broadcast_state()['enabled']);self.assertTrue(self.access.cache.receiving)
        self.watch.edit(False);self.sync()
        self.assertFalse(self.access._broadcast_state()['enabled']);self.assertFalse(self.access.cache.receiving)
    def test_receiving_pause_is_not_undone_by_duplicate_sync(self):
        self.watch.edit(True);self.sync();self.access.cache.set_receiving(False)
        self.sync();self.assertFalse(self.access.cache.receiving);self.assertTrue(self.running)
        self.watch.edit(False);self.sync();self.watch.edit(True);self.sync()
        self.assertTrue(self.access.cache.receiving)
    def test_conflicting_offline_edits_close_and_new_human_enable_can_reopen(self):
        self.access._edit_broadcast(True);self.sync()
        self.access._edit_broadcast(False);self.access._edit_broadcast(True);self.watch.edit(False)
        self.sync();self.assertFalse(self.access._broadcast_state()['enabled']);self.assertFalse(self.running)
        self.access._edit_broadcast(True);self.sync();self.assertTrue(self.running)
    def test_pending_physical_start_is_not_reported_as_applied(self):
        self.pending=True;self.access._edit_broadcast(True);self.sync()
        self.assertTrue(self.access._broadcast_state()['pending']);self.assertFalse(self.access._broadcast_state()['peerConfirmed'])
        self.running=True;self.sync();self.assertTrue(self.access._broadcast_state()['peerConfirmed'])
    def test_offline_human_edit_is_saved_without_starting_connection(self):
        self.access._drop_route();self.access.start=Mock()
        state=self.access.control(dict(action='heart_broadcast_on'))
        self.assertTrue(state['heartBroadcast']['pending']);self.assertFalse(state['connectionRunning'])
        self.access.start.assert_not_called();self.access._raw.assert_not_called()
        restored=TransportAccess(self.tmp.name);restored._ensure_broadcast();self.assertTrue(restored.broadcast.enabled)
    def test_daily_receive_prepare_respects_explicitly_disabled_connection(self):
        self.access.managed=False;self.access.mode='wifi';self.access.enabled_on_start=False
        self.access._drop_route();self.access.start=Mock()
        self.access.control(dict(action='start_if_enabled'))
        state=self.access.control(dict(action='heart_receive_on'))
        self.assertFalse(state['connectionRunning']);self.assertTrue(state['heartReceiveEnabled'])
        self.assertFalse(state['heartBroadcast']['enabled']);self.access.start.assert_not_called()
    def test_old_connection_reply_cannot_confirm_or_override_new_selection(self):
        self.watch.edit(True)
        original=self.reply
        def replaced(*args,**kwargs):
            result=original(*args,**kwargs);self.access._drop_route();return result
        self.access._raw.side_effect=replaced;self.sync()
        self.assertFalse(self.access._broadcast_state()['enabled']);self.assertFalse(self.access._broadcast_state()['peerConfirmed'])
    def test_invalid_lease_is_rejected_before_wifi_wire_request(self):
        self.watch.edit(True,1000)
        with self.assertRaises(ValueError):validate_request(dict(op='sync_broadcast',params=dict(selectionId='a'*32,syncId='b'*32,state=self.watch.wire())))
        self.watch.edit(False)
        self.assertEqual(validate_request(dict(op='sync_broadcast',params=dict(selectionId='a'*32,syncId='b'*32,state=self.watch.wire())))['op'],'sync_broadcast')
    def test_broadcast_storage_failure_never_reports_peer_confirmation(self):
        self.access._ensure_broadcast();self.access.broadcast.save=Mock(side_effect=OSError('disk full'))
        with self.assertRaises(OSError):self.access._edit_broadcast(True)
        self.sync();self.assertFalse(self.access._broadcast_state()['peerConfirmed']);self.assertIsNotNone(self.access._broadcast_state()['storageError'])


if __name__=='__main__':unittest.main()
