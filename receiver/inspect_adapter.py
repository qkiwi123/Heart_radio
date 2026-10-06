"""Read-only capability check of the local Windows Bluetooth adapter."""
import asyncio
import json
from winrt.windows.devices.bluetooth import BluetoothAdapter


async def main():
    adapter = await BluetoothAdapter.get_default_async()
    print(json.dumps(dict(peripheralSupported=adapter.is_peripheral_role_supported,
                          centralSupported=adapter.is_central_role_supported,
                          address=":".join(f"{adapter.bluetooth_address:012X}"[i:i+2] for i in range(0, 12, 2)))))


asyncio.run(main())
