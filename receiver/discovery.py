"""LAN discovery authenticated with the existing pairing secret. No secret on UDP."""
import asyncio
import hashlib
import hmac
import re
import time
import socket

GROUP = '239.255.72.82'


def discovery_socket(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        socket.inet_aton(GROUP) + socket.inet_aton(host))
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def signature(token, message):
    return hmac.new(token, message.encode('ascii'), hashlib.sha256).hexdigest()


def discovery_reply(data, token, pin, port, request_prefix='HRDISC1', reply_prefix='HRHERE1'):
    try:
        message = data.decode('ascii')
        prefix, nonce, identity, mac = message.split('|')
        if prefix != request_prefix or not re.fullmatch('[0-9a-f]{32}', nonce) or identity != pin:
            return None
        body = '|'.join((prefix, nonce, identity))
        if not hmac.compare_digest(mac, signature(token, body)):
            return None
        reply = f'{reply_prefix}|{nonce}|{pin}|{port}'
        return (reply + '|' + signature(token, reply)).encode('ascii')
    except (UnicodeError, ValueError):
        return None


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, token, pin, port, request_prefix='HRDISC1', reply_prefix='HRHERE1'):
        self.token, self.pin, self.port = token, pin, port
        self.request_prefix, self.reply_prefix = request_prefix, reply_prefix
        self.last_reply = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) > 256 or time.monotonic() - self.last_reply < .2:
            return
        reply = discovery_reply(data, self.token, self.pin, self.port, self.request_prefix, self.reply_prefix)
        if reply is not None:
            self.last_reply = time.monotonic()
            self.transport.sendto(reply, addr)
