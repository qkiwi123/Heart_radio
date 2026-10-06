"""Read-only pairing/connection status for an explicitly supplied BLE target."""
import asyncio
import json
import sys
from winrt.windows.devices.bluetooth import BluetoothLEDevice


async def main():
    device = await asyncio.wait_for(BluetoothLEDevice.from_bluetooth_address_async(int(sys.argv[1].replace(":", ""), 16)), 15)
    if device is None:
        raise RuntimeError("target unavailable")
    try:
        print(json.dumps(dict(address=sys.argv[1], paired=device.device_information.pairing.is_paired,
                              canPair=device.device_information.pairing.can_pair,
                              connectionStatus=int(device.connection_status))))
    finally:
        device.close()


asyncio.run(main())
