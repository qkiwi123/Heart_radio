"""Windows BLE receiver/server: encrypted writes from one explicitly allowed watch."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import time
from uuid import UUID
from winrt.windows.devices.bluetooth import BluetoothAdapter, BluetoothError, BluetoothLEDevice
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattServiceProvider, GattLocalCharacteristicParameters, GattCharacteristicProperties,
    GattProtectionLevel, GattServiceProviderAdvertisingParameters, GattSession,
)
from protocol import decode

SERVICE = UUID("749b2000-6f67-4d32-96dd-4d778c27a101")
DATA = UUID("749b2001-6f67-4d32-96dd-4d778c27a101")


async def run(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    tasks = set()
    sessions = {}
    remote = None
    radio_session = None
    fatal = asyncio.Event()
    with args.output.open("a", encoding="utf-8", buffering=1) as stream:
        def log(kind, **fields):
            line = json.dumps(dict(kind=kind, pcReceiptMs=time.time_ns() // 1_000_000,
                                   pcMonotonicNs=time.monotonic_ns(), **fields), ensure_ascii=False)
            stream.write(line + "\n")
            print(line, flush=True)

        adapter = await BluetoothAdapter.get_default_async()
        if adapter is None or not adapter.is_peripheral_role_supported:
            raise RuntimeError("PC adapter does not support BLE peripheral role")
        address = ":".join(f"{adapter.bluetooth_address:012X}"[i:i+2] for i in range(0, 12, 2))
        log("adapter", address=address, peripheralSupported=True)
        result = await GattServiceProvider.create_async(SERVICE)
        if result.error != BluetoothError.SUCCESS:
            raise RuntimeError(f"service creation failed: {result.error}")
        provider = result.service_provider
        params = GattLocalCharacteristicParameters()
        params.characteristic_properties = GattCharacteristicProperties.WRITE
        params.write_protection_level = GattProtectionLevel.ENCRYPTION_REQUIRED
        created = await provider.service.create_characteristic_async(DATA, params)
        if created.error != BluetoothError.SUCCESS:
            raise RuntimeError(f"characteristic creation failed: {created.error}")
        characteristic = created.characteristic
        expected = args.allow_watch.lower()
        previous = None

        async def handle_write(event, deferral):
            nonlocal previous
            request = None
            try:
                request = await event.get_request_async()
                if request is None:
                    log("expired_write")
                    return
                # WinRT IDs identify local + remote addresses. Do not log any unapproved peer.
                identifier = event.session.device_id.id.lower()
                if not identifier.endswith("-" + expected):
                    request.respond_with_protocol_error(0x08)
                    log("rejected_peer")
                    return
                if identifier not in sessions:
                    # Keep the approved connection's WinRT session alive for this bounded test.
                    session = event.session
                    if session.can_maintain_connection:
                        session.maintain_connection = True
                    sessions[identifier] = session
                    log("session_retained", target=args.allow_watch)
                if request.offset != 0:
                    request.respond_with_protocol_error(0x07)
                    return
                payload = bytes(memoryview(request.value))
                packet = decode(payload)
                current = packet["session"], packet["sequence"]
                gap = None if previous is None or previous[0] != current[0] else (current[1] - previous[1]) & 0xffff
                previous = current
                log("packet", packet=packet, hex=payload.hex(), sequenceStep=gap)
                request.respond()
            except ValueError as exc:
                if request is not None:
                    request.respond_with_protocol_error(0x0D)
                log("invalid_packet", reason=str(exc))
            except Exception as exc:
                log("write_error", error=type(exc).__name__, message=str(exc))
                fatal.set()
            finally:
                deferral.complete()

        def write_requested(_, event):
            deferral = event.get_deferral()
            def schedule():
                task = asyncio.create_task(handle_write(event, deferral))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            loop.call_soon_threadsafe(schedule)

        def advertising_changed(_, event):
            loop.call_soon_threadsafe(log_advertising, int(event.status), int(event.error))

        def log_advertising(status, error):
            log("advertising_status", status=status, error=error)
            if error:
                fatal.set()

        write_token = characteristic.add_write_requested(write_requested)
        advert_token = provider.add_advertisement_status_changed(advertising_changed)
        advertising = GattServiceProviderAdvertisingParameters()
        advertising.is_connectable = True
        advertising.is_discoverable = True
        try:
            provider.start_advertising_with_parameters(advertising)
            log("server_start", service=str(SERVICE), allowWatch=args.allow_watch, encrypted=True, seconds=args.seconds)
            remote = await BluetoothLEDevice.from_bluetooth_address_async(int(args.allow_watch.replace(":", ""), 16))
            if remote is None or not remote.device_information.pairing.is_paired:
                raise RuntimeError("expected watch is unavailable or not paired")
            radio_session = await GattSession.from_device_id_async(remote.bluetooth_device_id)
            if not radio_session.can_maintain_connection:
                raise RuntimeError("cannot maintain target radio link")
            radio_session.maintain_connection = True
            log("radio_link_requested", target=args.allow_watch)
            try:
                await asyncio.wait_for(fatal.wait(), args.seconds)
                raise RuntimeError("receiver stopped after BLE error")
            except asyncio.TimeoutError:
                log("capture_window_complete")
        finally:
            provider.stop_advertising()
            characteristic.remove_write_requested(write_token)
            provider.remove_advertisement_status_changed(advert_token)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for session in sessions.values():
                if session.can_maintain_connection:
                    session.maintain_connection = False
            if radio_session is not None:
                radio_session.maintain_connection = False
                radio_session.close()
            if remote is not None:
                remote.close()
            # Flush callbacks already queued from WinRT before closing the log.
            await asyncio.sleep(0)
            log("server_stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-watch", required=True, help="confirmed paired watch Bluetooth address")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--output", type=Path, default=Path("evidence/ble-fix/pc-server.jsonl"))
    args = parser.parse_args()
    if args.seconds <= 0 or not re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", args.allow_watch):
        parser.error("provide a valid address and positive duration")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
