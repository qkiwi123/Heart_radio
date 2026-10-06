"""Track one selected Windows LAN adapter, never listen on a wildcard address."""
import ctypes as c
import ipaddress
import json
from control import atomic_json

PRIVATE = tuple(ipaddress.ip_network(v) for v in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def private_ipv4(address):
    try:
        return any(ipaddress.ip_address(address) in network for network in PRIVATE)
    except ValueError:
        return False


class Address(c.Structure):
    pass


Address._fields_ = [('next', c.POINTER(Address)), ('ip', c.c_char * 16),
                   ('mask', c.c_char * 16), ('context', c.c_uint32)]


class Adapter(c.Structure):
    pass


Adapter._fields_ = [('next', c.POINTER(Adapter)), ('combo', c.c_uint32),
                    ('name', c.c_char * 260), ('description', c.c_char * 132),
                    ('mac_length', c.c_uint32), ('mac', c.c_ubyte * 8),
                    ('index', c.c_uint32), ('type', c.c_uint32), ('dhcp', c.c_uint32),
                    ('current', c.POINTER(Address)), ('addresses', Address), ('gateways', Address),
                    ('dhcp_server', Address), ('wins', c.c_int32), ('primary', Address),
                    ('secondary', Address), ('lease_start', c.c_int64), ('lease_end', c.c_int64)]


def adapters():
    api = c.WinDLL('iphlpapi').GetAdaptersInfo
    api.argtypes = [c.POINTER(Adapter), c.POINTER(c.c_uint32)]
    api.restype = c.c_uint32
    size = c.c_uint32()
    result = api(None, c.byref(size))
    if result == 232:
        return []
    if result != 111:
        raise OSError(result, 'GetAdaptersInfo size failed')
    buffer = c.create_string_buffer(size.value)
    first = c.cast(buffer, c.POINTER(Adapter))
    result = api(first, c.byref(size))
    if result:
        raise OSError(result, 'GetAdaptersInfo failed')
    found = []
    item = first
    while item:
        value = item.contents
        ips = []
        address = c.pointer(value.addresses)
        while address:
            host = address.contents.ip.decode('ascii')
            if private_ipv4(host):
                ips.append(host)
            address = address.contents.next
        found.append(dict(name=value.name.decode('ascii'), type=value.type, ips=ips,
                          gateway=value.gateways.ip.decode('ascii')))
        item = value.next
    return found


class LanAdapter:
    def __init__(self, directory, old_host):
        self.path = directory / 'network.json'
        available = adapters()
        if self.path.exists():
            self.name = json.loads(self.path.read_text(encoding='utf-8'))['adapter']
        else:
            candidates = [a for a in available if old_host in a['ips'] and a['type'] in (6, 71)]
            if not candidates:
                candidates = [a for a in available if a['type'] in (6, 71) and a['ips'] and a['gateway'] != '0.0.0.0']
            if len(candidates) != 1:
                raise ValueError('无法唯一选择局域网网卡；请核对 config.json 的电脑地址')
            self.name = candidates[0]['name']
            atomic_json(self.path, dict(adapter=self.name))

    def address(self):
        candidates = [a for a in adapters() if a['name'] == self.name and a['type'] in (6, 71)]
        return candidates[0]['ips'][0] if candidates and candidates[0]['ips'] else None
