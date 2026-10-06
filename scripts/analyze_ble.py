"""Summarize only the explicitly supplied project logs. No packets != success."""
import argparse
from collections import Counter
import json
import statistics
from pathlib import Path


def read(paths):
    return [json.loads(line) for path in paths for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--watch", type=Path, nargs="+", required=True)
parser.add_argument("--pc", type=Path, nargs="+", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
watch = read(args.watch)
pc = read(args.pc)
packets = [row for row in pc if row["kind"] == "packet"]
watch_hex = {row["hex"] for row in watch if row["kind"] == "ble_packet"}
intervals = [(b["pcMonotonicNs"] - a["pcMonotonicNs"]) / 1e9 for a, b in zip(packets, packets[1:])]
pc_start = next((r for r in pc if r["kind"] == "server_start"), None)
pc_stop = next((r for r in reversed(pc) if r["kind"] == "server_stop"), None)
summary = {
    "watchFiles": [str(p) for p in args.watch], "pcFiles": [str(p) for p in args.pc],
    "watchKinds": dict(Counter(row["kind"] for row in watch)),
    "pcKinds": dict(Counter(row["kind"] for row in pc)),
    "capabilities": [r for r in watch if r["kind"] == "ble_capability"],
    "advertisingSuccessCount": sum(r.get("advertiseSuccess") is True or
                                   (r["kind"] == "ble_discovery_advertising" and r.get("success") is True)
                                   for r in watch),
    "watchValidEvents": sum(r["kind"] == "event" and r.get("valid") is True for r in watch),
    "receivedPackets": len(packets),
    "pcWindowSeconds": (pc_stop["pcMonotonicNs"] - pc_start["pcMonotonicNs"]) / 1e9 if pc_start and pc_stop else None,
    "pcTrailingSilenceSeconds": (pc_stop["pcMonotonicNs"] - packets[-1]["pcMonotonicNs"]) / 1e9 if pc_stop and packets else None,
    "pcPacketSpanSeconds": (packets[-1]["pcMonotonicNs"] - packets[0]["pcMonotonicNs"]) / 1e9 if len(packets) > 1 else None,
    "pcIntervalMedianSeconds": statistics.median(intervals) if intervals else None,
    "pcIntervalMaxSeconds": max(intervals) if intervals else None,
    "sequenceGaps": [r["sequenceStep"] for r in packets if r.get("sequenceStep") not in (None, 1)],
    "receivedValidSensorEvents": sum(r["packet"]["kind"] == "sensor_event" and r["packet"]["valid"] for r in packets),
    "allReceivedPacketsMatchWatch": all(r["hex"] in watch_hex for r in packets) if packets else None,
    "mtuValues": sorted({r["mtu"] for r in watch if r["kind"] == "ble_mtu"}),
    "stopReasons": [r.get("reason") for r in watch if r["kind"] == "session_stop"],
    "limitations": "Only transport bench attempts; no user-confirmed wear test. Zero received packets does not establish any heart-rate transport success.",
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
