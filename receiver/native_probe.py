"""Bounded WinRT comparison for the explicitly confirmed watch; no Bleak code."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from uuid import UUID
from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothCacheMode
from protocol import SERVICE, DATA


async def run(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8", buffering=1) as file:
        def log(kind, **fields):
            row = dict(kind=kind, wallMs=time.time_ns() // 1_000_000, monotonicNs=time.monotonic_ns(), **fields)
            line = json.dumps(row)
            file.write(line + "\n")
            print(line, flush=True)
        device = None
        services = []
        stage = "device"
        try:
            device = await asyncio.wait_for(BluetoothLEDevice.from_bluetooth_address_async(int(args.address.replace(":", ""), 16)), 15)
            if device is None:
                raise RuntimeError("target unavailable")
            log("device", address=args.address, paired=device.device_information.pairing.is_paired,
                connectionStatus=int(device.connection_status))
            stage = "services"
            log("request", stage=stage, mode=args.mode)
            operation = (device.get_gatt_services_for_uuid_with_cache_mode_async(UUID(SERVICE), BluetoothCacheMode.UNCACHED)
                         if args.mode == "specific" else device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED))
            result = await asyncio.wait_for(operation, 25)
            services = list(result.services)
            matches = [s for s in services if str(s.uuid).lower() == SERVICE]
            log("services", status=int(result.status), protocolError=result.protocol_error, totalCount=len(services), projectCount=len(matches))
            for service in matches:
                stage = "characteristics"
                chars = await asyncio.wait_for(service.get_characteristics_for_uuid_with_cache_mode_async(UUID(DATA), BluetoothCacheMode.UNCACHED), 15)
                log("characteristics", status=int(chars.status), count=len(chars.characteristics), properties=[int(c.characteristic_properties) for c in chars.characteristics])
                for characteristic in chars.characteristics:
                    if not args.read:
                        continue
                    stage = "read"
                    read = await asyncio.wait_for(characteristic.read_value_with_cache_mode_async(BluetoothCacheMode.UNCACHED), 15)
                    log("read", status=int(read.status), protocolError=read.protocol_error,
                        hex=bytes(memoryview(read.value)).hex() if read.value is not None else None)
        except Exception as exc:
            log("error", stage=stage, error=type(exc).__name__, message=str(exc))
            return 1
        finally:
            for service in services:
                service.close()
            if device is not None:
                device.close()
    return 0


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--address", required=True)
parser.add_argument("--mode", choices=("specific", "all"), default="specific")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--read", action="store_true", help="only for a readable diagnostic characteristic")
raise SystemExit(asyncio.run(run(parser.parse_args())))
