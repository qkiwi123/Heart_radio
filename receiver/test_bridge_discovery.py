"""Authenticated discovery isolation; fixtures are not actual watch data."""
import unittest
from discovery import discovery_reply, signature


class BridgeDiscoveryTests(unittest.TestCase):
    token = bytes(range(32))
    pin = 'a'*64
    nonce = 'b'*32

    def request(self, prefix='HRBRIDGEDISC1'):
        body = f'{prefix}|{self.nonce}|{self.pin}'
        return (body+'|'+signature(self.token, body)).encode('ascii')

    def test_bridge_reply_is_paired_nonce_bound_and_separate_from_heart(self):
        reply = discovery_reply(self.request(), self.token, self.pin, 8767, 'HRBRIDGEDISC1', 'HRBRIDGEHERE1')
        body = f'HRBRIDGEHERE1|{self.nonce}|{self.pin}|8767'
        self.assertEqual(reply, (body+'|'+signature(self.token, body)).encode('ascii'))
        self.assertIsNone(discovery_reply(self.request(), self.token, self.pin, 8765))
        self.assertIsNone(discovery_reply(self.request('HRDISC1'), self.token, self.pin, 8767, 'HRBRIDGEDISC1', 'HRBRIDGEHERE1'))
        self.assertNotIn(self.token.hex().encode(), reply)

    def test_wrong_pairing_identity_or_signature_cannot_discover(self):
        for data in (self.request()[:-1]+b'z', b'HRBRIDGEDISC1|bad|'+self.pin.encode()+b'|invalid'):
            self.assertIsNone(discovery_reply(data, self.token, self.pin, 8767, 'HRBRIDGEDISC1', 'HRBRIDGEHERE1'))
        self.assertIsNone(discovery_reply(self.request(), b'x'*32, self.pin, 8767, 'HRBRIDGEDISC1', 'HRBRIDGEHERE1'))
        self.assertIsNone(discovery_reply(self.request(), self.token, 'c'*64, 8767, 'HRBRIDGEDISC1', 'HRBRIDGEHERE1'))


if __name__ == '__main__': unittest.main()
