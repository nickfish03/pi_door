"""Shared test scaffolding: a controllable clock, fake relays, temp settings."""

import os
import tempfile
import time
from datetime import datetime

# All schedule tests assume the Pi's real timezone.
os.environ["TZ"] = "America/Chicago"
time.tzset()

from coopdoor.controller import CoopController  # noqa: E402
from coopdoor.relays import FakeRelays  # noqa: E402
from coopdoor.settings import Settings  # noqa: E402


class FakeClock:
    def __init__(self, synced=True):
        self.synced = synced

    def is_synced(self):
        return self.synced


class Now:
    """Settable stand-in for datetime.now."""

    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def make_controller(now=datetime(2026, 10, 5, 12, 0), synced=True, **overrides):
    tmp = tempfile.mkdtemp(prefix="coopdoor-test-")
    settings = Settings(os.path.join(tmp, "settings.json"))
    settings.load()
    for k, v in overrides.items():
        settings[k] = v
    clock_now = Now(now)
    relays = FakeRelays()
    ctl = CoopController(relays, settings, FakeClock(synced), now_fn=clock_now, version="test")
    ctl.door._deadtime = 0  # keep tests fast
    return ctl, relays, clock_now
