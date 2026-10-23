import tempfile
import json
import http.client
from pathlib import Path
from http.server import ThreadingHTTPServer
import threading
import unittest
from unittest.mock import Mock, patch
from widgets import Widgets
import server


class WidgetControlTest(unittest.TestCase):
    def test_http_lock_actions_are_independent_and_reject_bad_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server,'widgets',Widgets(directory)):
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
            def send(payload):
                connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port)
                connection.request('POST','/api/widgets',json.dumps(payload),{
                    'Content-Type':'application/json','X-HeartRadio-Action':'widgets'})
                response=connection.getresponse();result=response.status,json.loads(response.read())
                connection.close();return result
            try:
                code,state=send({'widget':'current','locked':True})
                self.assertEqual(code,200)
                self.assertEqual(state['locked'],{'current':True,'trend':False})
                code,state=send({'widget':'trend','locked':True})
                self.assertEqual(code,200)
                self.assertEqual(state['locked'],{'current':True,'trend':True})
                self.assertEqual(send({'widget':'trend','locked':1})[0],400)
                self.assertEqual(send({'widget':'trend','visible':False,'locked':False})[0],400)
                self.assertEqual(server.widgets.status()['locked'],{'current':True,'trend':True})
                payload={'widget':'trend','layout':{'left':200},'client_id':'a'*32,'sequence':1}
                code,state=send(payload)
                self.assertEqual(code,200);self.assertEqual(state['status'],'locked')
                self.assertNotIn('left',state['layout']['trend'])
                send({'widget':'trend','locked':False})
                payload['sequence']=2
                code,state=send(payload)
                self.assertEqual(code,200);self.assertEqual(state['layout']['trend']['left'],200)
                payload['layout']['locked']=True
                self.assertEqual(send(payload)[0],400)
            finally:
                httpd.shutdown();httpd.server_close();worker.join()
    def test_dashboard_locks_each_window_without_changing_visibility_or_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            layout=Path(directory)/'runs'/'widget-layout.json'
            layout.parent.mkdir(parents=True)
            layout.write_text(json.dumps({'current':{'left':42,'locked':False},'trend':{'locked':True}}),encoding='utf-8')
            widget=Widgets(directory)
            widget.process=Mock();widget.process.poll.return_value=None
            widget.desired={'current':True,'trend':True}
            state=widget.set_locked('current',True)
            self.assertEqual(state['locked'],{'current':True,'trend':True})
            self.assertTrue(state['current'] and state['trend'])
            self.assertEqual(json.loads(layout.read_text(encoding='utf-8'))['current']['left'],42)
            state=widget.set_locked('trend',False)
            self.assertEqual(state['locked'],{'current':True,'trend':False})
            self.assertEqual(widget.status()['locked'],state['locked'])

    def test_lock_control_rejects_invalid_target_and_value(self):
        with tempfile.TemporaryDirectory() as directory:
            widget=Widgets(directory)
            for target,locked in [('other',True),('current','true'),('trend',1)]:
                with self.assertRaises(ValueError): widget.set_locked(target,locked)

    def test_dashboard_can_hide_and_show_locked_window_without_unlocking(self):
        for key in ('locked','movementLocked'):
            with self.subTest(saved_key=key), tempfile.TemporaryDirectory() as directory:
                layout=Path(directory)/'runs'/'widget-layout.json'
                layout.parent.mkdir(parents=True)
                layout.write_text(json.dumps({'current':{key:True},'trend':{key:False}}),encoding='utf-8')
                widget=Widgets(directory);widget.process=Mock();widget.process.poll.return_value=None
                widget.desired={'current':True,'trend':True}
                self.assertTrue(widget.status()['locked']['current'])
                self.assertFalse(widget.set_visible('current',False)['current'])
                self.assertTrue(widget.set_visible('current',True)['current'])
                self.assertTrue(widget.status()['locked']['current'])
                self.assertFalse(widget.set_visible('trend',False)['trend'])
                layout.write_text(json.dumps({'current':{'locked':False}}),encoding='utf-8')
                self.assertFalse(widget.set_visible('current',False)['current'])

    def test_reject_arbitrary_target_and_non_boolean_before_starting_process(self):
        with tempfile.TemporaryDirectory() as directory, patch('widgets.subprocess.Popen') as spawn:
            widget=Widgets(directory)
            for target,visible in [('receiver',True),('current','true'),('trend',1)]:
                with self.assertRaises(ValueError): widget.set_visible(target,visible)
            spawn.assert_not_called()
    def test_hiding_one_window_keeps_other_and_reuses_process(self):
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'desktop-widget'/'build'/'HeartRadioWidgets.exe'
            executable.parent.mkdir(parents=True);executable.touch()
            process=Mock();process.poll.return_value=None
            with patch('widgets.subprocess.Popen',return_value=process) as spawn:
                widget=Widgets(directory)
                widget.set_visible('current',True);widget.set_visible('trend',True)
                state=widget.set_visible('current',False)
                self.assertFalse(state['current']);self.assertTrue(state['trend']);spawn.assert_called_once()
                process.poll.return_value=1
                self.assertFalse(widget.status()['running']);self.assertFalse(widget.status()['trend'])
