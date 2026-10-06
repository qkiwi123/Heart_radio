"""Local BLE verification receiver. No web server or cloud. Ctrl+C disconnects."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import time
import traceback
from bleak import BleakClient, BleakScanner
from protocol import SERVICE, DATA, decode


async def run(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8", buffering=1) as stream:
        def log(kind, **fields):
            row = dict(kind=kind, pcReceiptMs=time.time_ns() // 1_000_000,
                       pcMonotonicNs=time.monotonic_ns(), **fields)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)

        target = args.address
        previous = None
        for cycle in range(args.cycles):
            log("scan_start", cycle=cycle + 1, service=SERVICE)
            devices = await BleakScanner.discover(timeout=args.scan_seconds, service_uuids=[SERVICE], return_adv=True)
            # Filter again: do not persist unrelated devices even if an OS ignores the UUID filter.
            candidates = [d for d, adv in devices.values() if SERVICE in [s.lower() for s in adv.service_uuids]
                          and (target is None or d.address.lower() == target.lower())]
            if args.scan_only:
                log("scan_result", count=len(candidates), candidates=[d.address for d in candidates])
                return
            if len(candidates) != 1:
                log("scan_result", count=len(candidates), candidates=[d.address for d in candidates])
                raise RuntimeError("need exactly one project device; specify --address if multiple")
            device = candidates[0]
            target = device.address  # Never silently switch devices during reconnect testing.
            log("target", address=target)
            disconnected = asyncio.Event()

            def notified(_, data):
                nonlocal previous
                try:
                    packet = decode(bytes(data))
                    current = (packet["session"], packet["sequence"])
                    gap = None if previous is None or previous[0] != current[0] else (current[1] - previous[1]) & 0xffff
                    previous = current
                    log("packet", packet=packet, hex=bytes(data).hex(), sequenceStep=gap)
                except ValueError as exc:
                    log("invalid_packet", reason=str(exc), hex=bytes(data).hex())

            log("connect_start", pairing=True)
            async with BleakClient(device, pair=True, timeout=45, services=[SERVICE],
                                   winrt={"use_cached_services": args.cached_services},
                                   disconnected_callback=lambda _: disconnected.set()) as client:
                log("connected")
                await client.start_notify(DATA, notified)
                log("subscribed")
                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=args.seconds)
                    log("unexpected_disconnect")
                except asyncio.TimeoutError:
                    log("capture_window_complete")
                if client.is_connected:
                    await client.stop_notify(DATA)
            log("disconnected", cycle=cycle + 1)
            if cycle + 1 < args.cycles:
                await asyncio.sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="explicit target if multiple project devices are advertised")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--scan-seconds", type=float, default=12)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--cycles", type=int, default=1, help="bounded disconnect/reconnect cycles")
    parser.add_argument("--output", type=Path, default=Path("evidence/ble/pc-receive.jsonl"))
    parser.add_argument("--debug", action="store_true", help="log only the selected target's WinRT client")
    parser.add_argument("--cached-services", action="store_true", help="diagnostic comparison using Windows GATT cache")
    args = parser.parse_args()
    if args.seconds <= 0 or args.cycles < 1 or args.scan_seconds <= 0:
        parser.error("durations and cycles must be positive")
    if args.debug:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.output.with_suffix(".debug.log"), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger("bleak.backends.winrt.client")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped; BLE connection closed.")
    except Exception as exc:
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(kind="receiver_error", pcReceiptMs=time.time_ns() // 1_000_000,
                                         error=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())) + "\n")
        print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
