"""Local service calls must stay on loopback even when a process has HTTP_PROXY set."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch
import urllib.request
import tempfile

from mcp_server import Server
from transport_access import TransportAccess
from capability_service import CapabilityService
import transport_access
import capability_service


class LoopbackProxyTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        seen = self.seen
        class Handler(BaseHTTPRequestHandler):
            def reply(self):
                size = int(self.headers.get('Content-Length', 0))
                if size: self.rfile.read(size)
                seen.append(self.server.server_port)
                data = json.dumps(dict(ok=True, status='ok', connected=False)).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = reply
            def log_message(self, *args): pass
        self.target = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        for http in (self.target, self.proxy):
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join)
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)

    def test_mcp_http_ignores_ambient_proxy(self):
        with patch.dict(os.environ, {'http_proxy': f'http://127.0.0.1:{self.proxy.server_port}',
                                     'HTTP_PROXY': f'http://127.0.0.1:{self.proxy.server_port}',
                                     'no_proxy': '', 'NO_PROXY': ''}), patch.object(urllib.request, '_opener', None):
            value = Server(self.target.server_port).invoke('get_capabilities', {})
        self.assertTrue(value['ok'])
        self.assertEqual(self.seen, [self.target.server_port], 'Local requests must never be sent to the proxy')

    def test_pc_bridge_status_and_requests_ignore_ambient_proxy(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'http_proxy': f'http://127.0.0.1:{self.proxy.server_port}',
            'HTTP_PROXY': f'http://127.0.0.1:{self.proxy.server_port}', 'no_proxy': '', 'NO_PROXY': ''}), \
                patch.object(transport_access, 'BRIDGE_URL', f'http://127.0.0.1:{self.target.server_port}'), \
                patch.object(capability_service, 'BRIDGE_URL', f'http://127.0.0.1:{self.target.server_port}'), \
                patch.object(urllib.request, '_opener', None):
            self.assertEqual(TransportAccess.wifi_state()['status'], 'ok')
            self.assertEqual(TransportAccess.wifi_request('status')['status'], 'ok')
            self.assertIsNotNone(CapabilityService(folder).bridge_state())
        self.assertEqual(self.seen, [self.target.server_port] * 3)


if __name__ == '__main__':
    unittest.main()
