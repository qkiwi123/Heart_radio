import unittest
from coexistence_check import summarize


def packet(seconds, kind='sensor_event', valid=True, sequence_step=1):
    return dict(kind='packet', pcMonotonicNs=int(seconds * 1e9), sequenceStep=sequence_step,
                packet=dict(kind=kind, valid=valid, bpm=75 if valid else None, session=1, sequence=int(seconds),
                            watchReceiptMs=0))


class CoexistenceSummaryTests(unittest.TestCase):
    def test_heartbeats_do_not_make_old_heart_rate_fresh(self):
        result = summarize([packet(0), packet(35, 'heartbeat', False), packet(55, 'heartbeat', False)],
                           int(10e9), int(60e9))
        self.assertEqual(result['validHeartCallbacks'], 0)
        self.assertEqual(result['heartbeatPackets'], 2)
        self.assertEqual(result['staleSeconds'], 30)
        self.assertEqual(result['finalValidAgeSeconds'], 60)

    def test_overlapping_fresh_windows_and_phase_boundaries(self):
        result = summarize([packet(5), packet(20), packet(40), packet(90)], int(10e9), int(100e9))
        self.assertEqual(result['staleSeconds'], 20)
        self.assertEqual(result['maxBetweenValidCallbacksSeconds'], 50)
        self.assertEqual(result['validHeartCallbacks'], 3)
        self.assertEqual(result['initialValidAgeSeconds'], 5)
        self.assertEqual(result['finalValidAgeSeconds'], 10)

    def test_no_prior_callback_and_invalid_measurements(self):
        result = summarize([packet(12, valid=False), packet(40, sequence_step=2)], int(10e9), int(50e9))
        self.assertEqual(result['staleSeconds'], 30)
        self.assertEqual(result['invalidHeartCallbacks'], 1)
        self.assertIsNone(result['initialValidAgeSeconds'])
        self.assertIsNone(result['maxBetweenValidCallbacksSeconds'])
        self.assertEqual(len(result['sequenceAnomalies']), 1)
        self.assertFalse(result['sourceAgeVerified'])


if __name__ == '__main__':
    unittest.main()
