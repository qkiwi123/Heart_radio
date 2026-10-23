import unittest
from transport_policy import choose
class PolicyTests(unittest.TestCase):
    def test_preference_when_both_ready(self):
        self.assertEqual(choose('auto_bluetooth',dict(rfcomm=True,wifi=True)),'rfcomm')
        self.assertEqual(choose('auto_wifi',dict(rfcomm=True,wifi=True)),'wifi')
    def test_stable_route_does_not_flap_when_preferred_appears(self):
        self.assertEqual(choose('auto_bluetooth',dict(rfcomm=True,wifi=True),'wifi',100),'wifi')
    def test_fallback_after_preference_window(self):
        self.assertIsNone(choose('auto_wifi',dict(rfcomm=True,wifi=False),waiting_seconds=4))
        self.assertEqual(choose('auto_wifi',dict(rfcomm=True,wifi=False),waiting_seconds=5),'rfcomm')
    def test_only_modes_and_no_candidates_never_claim_false_connection(self):
        self.assertIsNone(choose('wifi',dict(rfcomm=True,wifi=False),waiting_seconds=999))
        self.assertIsNone(choose('auto_bluetooth',dict(rfcomm=False,wifi=False),waiting_seconds=999))
