"""HeartRadio BLE v1: no synthetic samples, heartbeat != sensor event."""
import struct

SERVICE = "749b1000-6f67-4d32-96dd-4d778c27a101"
DATA = "749b1001-6f67-4d32-96dd-4d778c27a101"
PACKET = struct.Struct("<BBIHQHbB")


def decode(data: bytes) -> dict:
    if len(data) != PACKET.size:
        raise ValueError(f"expected 20 bytes, got {len(data)}")
    version, kind, session, sequence, receipt_ms, tenths, accuracy, flags = PACKET.unpack(data)
    if version != 1 or kind not in (1, 2, 3) or flags & ~7:
        raise ValueError("unknown version, kind or flags")
    valid = bool(flags & 1)
    if valid and (not tenths or accuracy not in (1, 2, 3) or flags & 6 != 6 or receipt_ms == 0):
        raise ValueError("inconsistent valid reading")
    return dict(version=version, kind={1: "sensor_event", 2: "heartbeat", 3: "accuracy"}[kind],
                session=session, sequence=sequence, watchReceiptMs=receipt_ms or None,
                timeBasis="watch_receipt_approximation", bpm=tenths / 10 if valid else None,
                accuracy=accuracy, valid=valid, running=bool(flags & 2), registered=bool(flags & 4))
