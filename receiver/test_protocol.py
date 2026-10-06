import unittest
from protocol import PACKET, decode


class ProtocolTests(unittest.TestCase):
    def test_android_golden_packet(self):
        data = bytes.fromhex("010104030201ffff0068e5cf8b010000d5020307")
        row = decode(data)
        self.assertEqual(row["session"], 0x01020304)
        self.assertEqual(row["sequence"], 65535)
        self.assertEqual(row["watchReceiptMs"], 1700000000000)
        self.assertEqual(row["bpm"], 72.5)

    def test_contact_loss_never_reuses_number(self):
        row = decode(PACKET.pack(1, 1, 1, 2, 1700000000000, 725, -1, 6))
        self.assertIsNone(row["bpm"])

    def test_heartbeat_is_not_measurement(self):
        row = decode(PACKET.pack(1, 2, 1, 3, 1700000000000, 725, 3, 7))
        self.assertEqual(row["kind"], "heartbeat")

    def test_bad_payloads_rejected(self):
        for data in (b"", bytes(20), PACKET.pack(1, 1, 1, 0, 0, 725, -1, 7)):
            with self.assertRaises(ValueError):
                decode(data)


if __name__ == "__main__":
    unittest.main()
