"""Clock trust: reboot marker (single use, recent only) and the per-boot flag."""

import os
import tempfile
import unittest

from coopdoor.clock import ClockWatcher

NOW = 1_800_000_000.0


class ClockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.marker = os.path.join(self.dir, "clock-trusted-reboot")
        self.flag = os.path.join(self.dir, "run", "clock-synced")

    def watcher(self, uptime=60.0, now=NOW):
        w = ClockWatcher(reboot_marker=self.marker, runtime_flag=self.flag,
                         time_fn=lambda: now, uptime_fn=lambda: uptime)
        w._check_os = lambda: False  # no NTP in these tests
        return w

    def write_marker(self, epoch):
        with open(self.marker, "w") as f:
            f.write("%d\n" % epoch)

    def test_no_marker_not_synced(self):
        self.assertFalse(self.watcher().is_synced())

    def test_recent_marker_trusted_once(self):
        self.write_marker(NOW - 90)
        self.assertTrue(self.watcher().is_synced())
        self.assertFalse(os.path.exists(self.marker), "marker must be single-use")
        self.assertTrue(os.path.exists(self.flag), "per-boot flag not written")

    def test_old_marker_rejected_and_removed(self):
        self.write_marker(NOW - 5 * 3600)   # e.g. restored clock after a power cut
        self.assertFalse(self.watcher().is_synced())
        self.assertFalse(os.path.exists(self.marker))

    def test_marker_ignored_if_pi_has_been_up_a_while(self):
        self.write_marker(NOW - 60)
        self.assertFalse(self.watcher(uptime=3 * 3600).is_synced())

    def test_marker_from_the_future_rejected(self):
        self.write_marker(NOW + 3600)
        self.assertFalse(self.watcher().is_synced())

    def test_runtime_flag_survives_service_restart(self):
        w = self.watcher()
        w._check_os = lambda: True
        self.assertTrue(w.is_synced())
        restarted = self.watcher()            # NTP unreachable now
        self.assertTrue(restarted.is_synced())


if __name__ == "__main__":
    unittest.main()
