# ============================================================================
# clock.py - "can we trust the clock yet?"
#
# The ESP8266 booted at 1970 until NTP synced, so "is the year sane?" was
# enough. A Pi is sneakier: it has no battery clock, so at boot it restores
# the time it saved at last shutdown (fake-hwclock). That time looks
# perfectly plausible but could be hours or days stale - after a power
# outage, for instance - and acting on it would run the door at the wrong
# time. So instead of a sanity check on the date, ask the OS whether NTP
# has actually synced since this boot.
#
# Once it has, we latch that for the life of the process: the Pi's clock
# keeps good time on its own through a WiFi outage (just like the ESP did),
# and the kernel's "synchronized" flag can flip back to "no" after many
# hours offline, which mustn't stop the scheduler.
# ============================================================================

import logging
import os
import subprocess
import threading
import time

log = logging.getLogger(__name__)

# systemd-timesyncd (the Pi OS default) creates this file the first time it
# syncs after boot. /run is cleared on every boot, so it can't be stale.
_TIMESYNC_FLAG = "/run/systemd/timesync/synchronized"


class ClockWatcher:
    def __init__(self, assume_synced=False, recheck_s=30.0):
        self._synced = bool(assume_synced)
        self._recheck_s = recheck_s
        self._last_check = 0.0
        self._lock = threading.Lock()
        self._announced = False

    def is_synced(self):
        if self._synced:
            return True
        with self._lock:
            now = time.monotonic()
            if self._last_check and now - self._last_check < self._recheck_s:
                return False
            self._last_check = now
            if self._check_os():
                self._synced = True
                log.info("System clock is NTP-synced - scheduler active")
            elif not self._announced:
                self._announced = True
                log.warning("System clock not NTP-synced yet - scheduler waits until it is")
            return self._synced

    @staticmethod
    def _check_os():
        if os.path.exists(_TIMESYNC_FLAG):
            return True
        try:
            out = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return out == "yes"
        except (OSError, subprocess.SubprocessError):
            return False
