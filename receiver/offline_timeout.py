"""Continuous application-link absence, independent of business permission deadlines."""
class OfflineTimeout:
    def __init__(self, limit=600):
        self.limit = limit
        self.waiting_since = None
        self.fired = False

    def start(self, now):
        self.waiting_since, self.fired = now, False

    def remaining(self, now, connected):
        if connected:
            self.waiting_since = None
            return self.limit
        if self.waiting_since is None:
            self.waiting_since = now
        return max(0, self.limit - max(0, now - self.waiting_since))

    def expired(self, now, connected):
        if self.fired or self.remaining(now, connected) > 0:
            return False
        self.fired = True
        return True
