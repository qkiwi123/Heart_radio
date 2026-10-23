import tempfile,time,unittest
from unittest.mock import Mock
from transport_access import TransportAccess
from interaction_sync import InteractionRegister

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.access=TransportAccess(self.tmp.name);self.access.managed=True
        self.access.configured_mode='auto_bluetooth';self.access.waiting_since=time.monotonic()-10
        self.access._candidate_states=Mock(return_value={'rfcomm':dict(connected=True,key=1),'wifi':dict(connected=True,key=('pc',1))})
        self.watch=InteractionRegister('watch')
        self.access._raw=Mock(side_effect=self.raw)
    def raw(self,route,op,params=None,request_id=None):
        if op=='sync_interaction':
            self.watch.merge(params['state'])
            return dict(status='ok',syncId=params['syncId'],interaction=dict(enabled=self.watch.enabled,register=self.watch.wire()))
        return dict(status='ok',selectionId=(params or {}).get('selectionId'))
    def enable(self):
        self.access.control(dict(action='agent_on'));self.access._sync_once()
    def select(self):self.access._route_once()
    def test_authenticated_selection_requires_watch_ack_not_just_socket(self):
        self.access._raw.return_value=dict(status='transport_not_allowed');self.access._raw.side_effect=None
        self.select();self.assertIsNone(self.access.selected)
        self.assertFalse(self.access.heart_snapshot(lambda:{})['valid'])
    def test_stable_selection_and_replacement_connection_gets_new_identity(self):
        self.select();identity=self.access.selection_id
        self.select();self.assertEqual(self.access._raw.call_count,1)
        self.access._candidate_states.return_value['rfcomm']['key']=2
        self.select();self.assertNotEqual(self.access.selection_id,identity)
        self.assertEqual(self.access.selected,'rfcomm')
    def test_inventory_and_sample_cannot_cross_route_or_replay(self):
        self.select();self.enable()
        self.access.request('inventory');self.access._drop_route()
        self.select();self.access._sync_once();self.access._raw.reset_mock()
        result=self.access.request('sample',dict(sensorId=1,seconds=4),'once')
        self.assertEqual(result['status'],'outcome_unknown_disconnected');self.access._raw.assert_not_called()
    def test_result_from_old_route_is_not_success_on_new_route(self):
        self.select();self.enable()
        def old_reply(*args):self.access._drop_route();return dict(status='data_received')
        self.access._raw.side_effect=old_reply
        self.assertEqual(self.access.request('sample',dict(sensorId=1,seconds=4),'once')['status'],'outcome_unknown_disconnected')
    def test_unselected_or_old_session_heart_is_not_added_to_cache(self):
        self.select();row=dict(event='heart',selectionId='old',heartAttemptId='attempt',valid=True,bpm=80,accuracy=3,sequence=1,watchReceiptMs=100)
        self.access._observe(row);self.assertEqual(self.access.cache.state.event_count,0)
        row['selectionId']=self.access.selection_id
        self.access._observe(row);self.assertEqual(self.access.cache.state.valid_count,1)
    def test_manual_mode_saves_without_starting_heart_or_enabling_interaction(self):
        self.access.stop=Mock();self.access.start=Mock()
        self.access.control(dict(action='set_mode',mode='wifi'))
        self.assertFalse(self.access.agent_enabled);self.access.start.assert_called_once()
        self.assertEqual(TransportAccess(self.tmp.name).configured_mode,'wifi')
    def test_manual_wifi_reuses_authenticated_wifi_socket_and_only_stops_bluetooth(self):
        self.select();old=self.access.selection_id;self.access.manager_thread=Mock();self.access.manager_thread.is_alive.return_value=True
        self.access.stop=Mock();self.access.start=Mock();self.access._stop_bluetooth_adapter=Mock();self.access._stop_wifi_adapter=Mock()
        self.access.control(dict(action='set_mode',mode='wifi'))
        self.assertEqual(self.access.selected,'wifi');self.assertNotEqual(self.access.selection_id,old)
        self.access.stop.assert_not_called();self.access.start.assert_not_called();self.access._stop_wifi_adapter.assert_not_called();self.access._stop_bluetooth_adapter.assert_called_once()
        self.assertFalse(self.access.agent_enabled)
    def test_preference_change_reselects_without_restarting_either_adapter(self):
        self.select();self.access.manager_thread=Mock();self.access.manager_thread.is_alive.return_value=True
        self.access.stop=Mock();self.access.start=Mock();self.access._stop_bluetooth_adapter=Mock();self.access._stop_wifi_adapter=Mock()
        self.access.control(dict(action='set_mode',mode='auto_wifi'))
        self.assertEqual(self.access.selected,'wifi');self.access.stop.assert_not_called();self.access.start.assert_not_called()
        self.access._stop_wifi_adapter.assert_not_called();self.access._stop_bluetooth_adapter.assert_not_called()
    def test_manual_bluetooth_keeps_ready_bluetooth_and_only_stops_wifi(self):
        self.access.configured_mode='auto_wifi';self.select();self.assertEqual(self.access.selected,'wifi')
        self.access.manager_thread=Mock();self.access.manager_thread.is_alive.return_value=True
        self.access.stop=Mock();self.access.start=Mock();self.access._stop_bluetooth_adapter=Mock();self.access._stop_wifi_adapter=Mock()
        self.access.control(dict(action='set_mode',mode='rfcomm'))
        self.assertEqual(self.access.selected,'rfcomm');self.access._stop_wifi_adapter.assert_called_once();self.access._stop_bluetooth_adapter.assert_not_called();self.access.stop.assert_not_called()
    def test_failed_mode_save_leaves_live_route_and_preferences_unchanged(self):
        self.select();identity=self.access.selection_id;self.access.manager_thread=Mock();self.access.manager_thread.is_alive.return_value=True
        self.access._preferences=Mock(side_effect=OSError('test disk full'))
        with self.assertRaises(OSError):self.access.control(dict(action='set_mode',mode='wifi'))
        self.assertEqual(self.access.selected,'rfcomm');self.assertEqual(self.access.selection_id,identity);self.assertEqual(self.access.configured_mode,'auto_bluetooth')
    def test_fresh_queries_blocked_but_cleanup_uses_original_session(self):
        self.select();self.assertEqual(self.access.request('status')['status'],'interaction_disabled')
        self.access.sample_routes['old']=('wifi',('pc',1),'old')
        self.access._raw.reset_mock()
        self.assertEqual(self.access.request('cancel',dict(targetId='old'))['status'],'outcome_unknown_disconnected')
        self.access._raw.assert_not_called()

    def test_wifi_reselection_never_reuses_an_inflight_packet_from_previous_session(self):
        self.access.selected='wifi';self.access.selected_ms=100;self.access.wifi_heart_session=22
        def snapshot(session):return dict(valid=True,bpm=88,eventReceivedMs=200,eventSession=session,connected=True)
        self.assertFalse(self.access.heart_snapshot(lambda:snapshot(21))['valid'])
        self.assertTrue(self.access.heart_snapshot(lambda:snapshot(22))['valid'])

    def test_duplicate_business_enable_does_not_extend_deadline(self):
        from control import save_settings
        from pathlib import Path
        save_settings(Path(self.tmp.name)/'runs/capability-duration-settings.json',dict(unlimited=False,amount=1,unit='minutes'))
        self.select();self.access.control(dict(action='agent_on'));ends=self.access.interaction.register.deadlines.copy()
        self.access.control(dict(action='agent_on'));self.assertEqual(self.access.interaction.register.deadlines,ends)

    def test_connection_close_preserves_business_preference(self):
        self.select();self.enable();self.access.stop=Mock()
        self.access.control(dict(action='stop'))
        self.assertTrue(self.access.agent_enabled);self.assertFalse(self.access.enabled_on_start)

    def test_receive_stop_cannot_write_an_unrelated_stop_file(self):
        from pathlib import Path
        import json
        folder=Path(self.tmp.name)/'runs';folder.mkdir(exist_ok=True)
        (folder/'active.json').write_text(json.dumps(dict(stopFile=str(Path(self.tmp.name)/'unrelated.request'))),encoding='utf-8')
        with self.assertRaises(ValueError):self.access.control(dict(action='heart_receive_off'))
        self.assertFalse((Path(self.tmp.name)/'unrelated.request').exists())

    def test_view_only_restart_cannot_use_connection_listener_as_business_permission(self):
        self.access.managed=False;self.access.mode='wifi'
        self.access.wifi_state=Mock(return_value=dict(connected=True,connectionOnly=True))
        self.access.wifi_request=Mock()
        self.assertEqual(self.access.request('status')['status'],'interaction_disabled')
        self.access.wifi_request.assert_not_called()

    def test_enable_pending_ack_blocks_fresh_query_then_confirms(self):
        self.select();self.access.control(dict(action='agent_on'))
        self.assertEqual(self.access.request('status')['status'],'interaction_sync_pending')
        self.access._sync_once();self.assertEqual(self.access.request('status')['status'],'ok')

    def test_watch_close_updates_pc_and_cancels_bounded_work(self):
        import threading
        self.select();self.enable();cancelled=threading.Event();self.access.on_gate_closed=cancelled.set
        self.watch.edit(False);self.access._sync_once()
        self.assertFalse(self.access.agent_enabled);self.assertTrue(cancelled.wait(1))
        self.assertEqual(self.access.request('status')['status'],'interaction_disabled')

    def test_failed_or_stale_ack_is_not_reported_as_synced(self):
        self.select();self.enable();self.access._raw.side_effect=lambda *args:dict(status='outcome_unknown_timeout')
        self.access._sync_once();self.assertFalse(self.access.state()['agentInteraction']['peerConfirmed'])
        self.assertEqual(self.access.request('status')['status'],'interaction_sync_pending')

    def test_lost_local_history_from_peer_closes_instead_of_restoring_old_open(self):
        self.select();self.watch.events['pc']=dict(seq=10,seen=0,id='c'*32,enabled=True,ttlMs=None)
        self.access._sync_once();self.assertFalse(self.access.agent_enabled)
        self.assertFalse(self.access.state()['agentInteraction']['peerConfirmed'])
        self.access._sync_once();self.assertFalse(self.watch.enabled);self.assertTrue(self.access._sync_confirmed())

    def test_offline_edit_does_not_start_connection(self):
        self.access.managed=False;self.access.mode='wifi';self.access.start=Mock()
        result=self.access.control(dict(action='agent_on'))
        self.assertTrue(result['agentInteraction']['pending']);self.access.start.assert_not_called()

    def test_malformed_confirmation_immediately_invalidates_previous_ack(self):
        self.select();self.enable()
        self.access._raw.side_effect=lambda route,op,params:dict(status='ok',syncId=params['syncId'],interaction={})
        self.access._sync_once();self.assertFalse(self.access._sync_confirmed())
        self.assertEqual(self.access.request('status')['status'],'interaction_sync_pending')

    def test_watch_finite_lease_is_reported_even_with_pc_unlimited_preference(self):
        self.select();self.watch.edit(True,30000);self.access._sync_once()
        value=self.access.state()['agentInteraction']
        self.assertTrue(value['enabled']);self.assertEqual(value['activeDurationMs'],30000)
        self.assertLessEqual(value['remainingMs'],30000)

    def test_receipt_from_previous_bluetooth_selection_cannot_update_current_cache(self):
        self.select();observer=Mock();self.access.on_reminder_receipt=observer
        self.access._observe(dict(event='reminder_receipt',reminderId='one',selectionId='old'))
        observer.assert_not_called()
        self.access._observe(dict(event='reminder_receipt',reminderId='one',selectionId=self.access.selection_id))
        observer.assert_called_once()

if __name__=='__main__':unittest.main()
