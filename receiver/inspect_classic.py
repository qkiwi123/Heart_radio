"""Read local Bluetooth radio state; never changes it or enumerates nearby devices."""
import asyncio
import json
from winrt.windows.devices.bluetooth import BluetoothAdapter


async def main():
    adapter = await asyncio.wait_for(BluetoothAdapter.get_default_async(), 8)
    if adapter is None:
        print(json.dumps(dict(available=False)))
        return
    radio = await asyncio.wait_for(adapter.get_radio_async(), 8)
    print(json.dumps(dict(available=True, radioState=int(radio.state), radioName=radio.name,
                         centralSupported=adapter.is_central_role_supported,
                         peripheralSupported=adapter.is_peripheral_role_supported)))


if __name__ == '__main__':
    asyncio.run(main())
