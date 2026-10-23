"""Windows RFCOMM client using documented Winsock AF_BTH addressing.

Only connects to the supplied paired address and custom service UUID; no scan,
pairing, adapter changes or raw port guessing.
"""
import ctypes
import select
import socket
import sys
import time
import uuid


class SockaddrBth(ctypes.Structure):
    _pack_ = 1  # ws2bth.h is enclosed in pshpack1.h.
    _fields_ = [('family', ctypes.c_uint16), ('address', ctypes.c_uint64),
                ('service', ctypes.c_ubyte * 16), ('port', ctypes.c_uint32)]


def make_address(address, service):
    parts = address.split(':')
    if len(parts) != 6 or any(len(part) != 2 or any(c not in '0123456789abcdefABCDEF' for c in part) for part in parts):
        raise ValueError('Invalid Bluetooth address')
    result = SockaddrBth()
    result.family = 32
    result.address = int(''.join(parts), 16)
    result.service[:] = uuid.UUID(str(service)).bytes_le
    return result


def connect(address, service, timeout=20, cancel=None):
    if sys.platform != 'win32':
        raise OSError('This experimental RFCOMM adapter requires Windows')
    winsock = ctypes.WinDLL('Ws2_32.dll')
    winsock.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    winsock.connect.restype = ctypes.c_int
    winsock.WSAGetLastError.restype = ctypes.c_int
    target = make_address(address, service)
    client = socket.socket(32, socket.SOCK_STREAM, 3)
    try:
        # Require the same authenticated, encrypted link as Android's secure listener.
        client.setsockopt(3, ctypes.c_int32(0x80000001).value, 1)
        client.setsockopt(3, 0x00000002, 1)
        client.setblocking(False)
        result = winsock.connect(client.fileno(), ctypes.byref(target), ctypes.sizeof(target))
        if result:
            error = winsock.WSAGetLastError()
            if error not in (10035, 10036, 10037):
                raise OSError(error, f'RFCOMM connect failed: {error}')
            until=time.monotonic()+timeout
            while True:
                if cancel is not None and cancel.is_set():raise ConnectionAbortedError('Connection stopped')
                left=until-time.monotonic()
                if left<=0:raise TimeoutError('RFCOMM connect timed out')
                _, writable, exceptional = select.select([], [client], [client], min(.25,left))
                if writable or exceptional:break
            error = client.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if error:
                raise OSError(error, f'RFCOMM connect failed: {error}')
        client.settimeout(20)
        return client
    except BaseException:
        client.close()
        raise
