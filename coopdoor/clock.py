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
# Once it has, we latch that for the rest of this boot: the Pi's clock
# keeps good time on its own through a WiFi outage (just like the ESP did),
# and the kernel's "synchronized" flag can flip back to "no" after many
# hours offline, which mustn't stop the scheduler. The latch is also
# written to /run (cleared every boot), so a service restart in the middle
# of a WiFi outage - a crash, or a `git pull` + restart - doesn't lose it.
#
# One more case: the WiFi watchdog reboots the Pi when WiFi has been down
# for 10+ minutes. Without help, the Pi would come back up with no network
# AND no trusted clock, pausing the scheduler for the rest of the outage -
# exactly when it's needed. So before rebooting, the watchdog drops a
# marker file if the clock was synced at that moment. A clean reboot saves
# and restores the time (fake-hwclock), so after it the clock is only off by
# roughly the reboot time (well under a minute or two). The marker is
# accepted once - it's deleted when read - and only if it's recent and the
# Pi only just booted, so it can never vouch for a clock restored after an
# unexpected power cut.
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

# A reboot marker is only honored if it was written at most this long ago
# (by the restored clock) and the Pi has been up for less than this long.
_MARKER_MAX_AGE_S = 900


def _read_uptime_s():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


class ClockWatcher:
    def __init__(self, assume_synced=False, recheck_s=30.0, reboot_marker=None,
                 runtime_flag=None, time_fn=time.time, uptime_fn=_read_uptime_s):
        self._synced = bool(assume_synced)
        self._recheck_s = recheck_s
        self._last_check = 0.0
        self._lock = threading.Lock()
        self._announced = False
        self._runtime_flag = runtime_flag
        self._time = time_fn
        self._uptime = uptime_fn

        if not self._synced and runtime_flag and os.path.exists(runtime_flag):
            self._synced = True
            log.info("Clock was already NTP-synced earlier this boot - scheduler active")
        if not self._synced and reboot_marker:
            if self._check_reboot_marker(reboot_marker):
                self._synced = True
                self._write_runtime_flag()

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
                self._write_runtime_flag()
                log.info("System clock is NTP-synced - scheduler active")
            elif not self._announced:
                self._announced = True
                log.warning("System clock not NTP-synced yet - scheduler waits until it is")
            return self._synced

    def _write_runtime_flag(self):
        if not self._runtime_flag:
            return
        try:
            os.makedirs(os.path.dirname(self._runtime_flag), exist_ok=True)
            with open(self._runtime_flag, "w") as f:
                f.write("%d\n" % self._time())
        except OSError as e:
            log.warning("Couldn't record clock-synced flag %s: %s", self._runtime_flag, e)

    def _check_reboot_marker(self, path):
        try:
            with open(path) as f:
                raw = f.read().strip()
        except FileNotFoundError:
            return False
        except OSError as e:
            log.warning("Couldn't read reboot marker %s: %s", path, e)
            return False
        # Single use: remove it no matter what, so it can't vouch for a
        # later boot.
        try:
            os.unlink(path)
        except OSError as e:
            log.warning("Couldn't remove reboot marker %s: %s", path, e)
        try:
            written = float(raw)
            uptime = self._uptime()
        except (ValueError, OSError):
            return False
        age = self._time() - written
        if 0 <= age <= _MARKER_MAX_AGE_S and uptime <= _MARKER_MAX_AGE_S:
            log.info("Pi was rebooted by the WiFi watchdog %ds ago with a synced clock - "
                     "trusting the restored time; scheduler active", age)
            return True
        log.warning("Ignoring stale reboot marker (age %.0fs, uptime %.0fs)", age, uptime)
        return False

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
