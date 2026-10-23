import unittest
from capability_bridge import validate_request
from motion_protocol import validate_motion_window

class MotionProtocolTests(unittest.TestCase):
    def test_fixed_window_is_accepted_and_not_an_arbitrary_long_sensor_request(self):
        value=dict(op='motion_window',params=dict(sensorId=4,seconds=30))
        self.assertEqual(validate_request(value),value)
        for p in [dict(sensorId=4,seconds=10),dict(sensorId=4,seconds=True),dict(sensorId=-1,seconds=30),
                  dict(sensorId=True,seconds=30),dict(sensorId=4,seconds=30,periodUs=1),dict(sensorId=4)]:
            with self.subTest(params=p):
                with self.assertRaises(ValueError):validate_request(dict(op='motion_window',params=p))

if __name__=='__main__':unittest.main()
