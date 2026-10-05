"""Door timing, safety interlock, scheduler, and settings persistence."""

import json
import time
import unittest
from datetime import datetime, timedelta

from tests.helpers import make_controller
from coopdoor.controller import ConfirmNeeded, SaveError
from coopdoor.settings import ACTION_CLOSED, ACTION_OPEN, ACTION_UNKNOWN, Settings


class DoorTimingTest(unittest.TestCase):
    def test_relay_turns_off_after_duration(self):
        ctl, relays, _ = make_controller(openDurationMs=200)
        ctl.manual_move(ACTION_OPEN)
        self.assertTrue(relays.open_on)
        self.assertFalse(relays.close_on)
        self.assertEqual(ctl.door.state, "opening")
        time.sleep(0.35)
        self.assertFalse(relays.open_on)
        self.assertEqual(ctl.door.state, "idle")

    def test_relays_never_both_on(self):
        ctl, relays, _ = make_controller(openDurationMs=5000, closeDurationMs=5000)
        ctl.manual_move(ACTION_OPEN)
        ctl.manual_move(ACTION_CLOSED)  # reverse mid-move
        ctl.manual_stop()
        self.assertNotIn((True, True), relays.history)
        self.assertEqual((relays.open_on, relays.close_on), (False, False))

    def test_reverse_goes_through_all_off(self):
        ctl, relays, _ = make_controller(openDurationMs=5000, closeDurationMs=5000)
        ctl.manual_move(ACTION_OPEN)
        relays.history.clear()
        ctl.manual_move(ACTION_CLOSED)
        self.assertEqual(relays.history[0], (False, False))
        self.assertEqual(relays.history[-1], (False, True))
        ctl.manual_stop()

    def test_stale_timer_does_not_cut_a_newer_move_short(self):
        ctl, relays, _ = make_controller(openDurationMs=200, closeDurationMs=600)
        ctl.manual_move(ACTION_OPEN)
        time.sleep(0.1)
        ctl.manual_move(ACTION_CLOSED)
        time.sleep(0.25)  # the open's 200 ms timer would have fired by now
        self.assertTrue(relays.close_on, "close move was stopped by the old open timer")
        time.sleep(0.5)
        self.assertFalse(relays.close_on)

    def test_backstop_stops_overdue_move(self):
        ctl, relays, _ = make_controller(openDurationMs=100)
        ctl.manual_move(ACTION_OPEN)
        ctl.door._cancel_timer()  # simulate the timer thread failing
        time.sleep(0.15)
        self.assertTrue(relays.open_on)
        ctl.scheduler_tick()      # the once-a-second backstop
        self.assertFalse(relays.open_on)


class InterlockTest(unittest.TestCase):
    def test_repeat_needs_confirm(self):
        ctl, relays, _ = make_controller(openDurationMs=100)
        ctl.manual_move(ACTION_OPEN)
        time.sleep(0.15)
        with self.assertRaises(ConfirmNeeded) as cm:
            ctl.manual_move(ACTION_OPEN)
        self.assertIn("already OPEN", str(cm.exception))
        self.assertIn("manual", str(cm.exception))
        self.assertFalse(relays.open_on)
        ctl.manual_move(ACTION_OPEN, confirmed=True)
        self.assertTrue(relays.open_on)
        ctl.manual_stop()

    def test_opposite_action_allowed(self):
        ctl, _, _ = make_controller(lastAction=ACTION_OPEN)
        ctl.manual_move(ACTION_CLOSED)
        self.assertEqual(ctl.settings["lastAction"], ACTION_CLOSED)
        ctl.manual_stop()

    def test_stop_mid_move_resets_to_unknown(self):
        ctl, _, _ = make_controller(openDurationMs=5000)
        ctl.manual_move(ACTION_OPEN)
        ctl.manual_stop()
        self.assertEqual(ctl.settings["lastAction"], ACTION_UNKNOWN)
        ctl.manual_move(ACTION_OPEN)  # no confirm needed after unknown
        ctl.manual_stop()

    def test_stop_when_idle_keeps_state(self):
        ctl, _, _ = make_controller(lastAction=ACTION_CLOSED)
        ctl.manual_stop()
        self.assertEqual(ctl.settings["lastAction"], ACTION_CLOSED)

    def test_interlock_survives_restart(self):
        ctl, _, _ = make_controller(closeDurationMs=100)
        ctl.manual_move(ACTION_CLOSED)
        reloaded = Settings(ctl.settings.path)
        reloaded.load()
        self.assertEqual(reloaded["lastAction"], ACTION_CLOSED)
        self.assertEqual(reloaded["lastActionSource"], "manual")
        self.assertEqual(reloaded["lastActionAt"], "2026-10-05 12:00")

    def test_unsynced_clock_timestamp(self):
        ctl, _, _ = make_controller(synced=False, openDurationMs=100)
        ctl.manual_move(ACTION_OPEN)
        self.assertEqual(ctl.settings["lastActionAt"], "unknown time (clock not synced)")


class SchedulerTest(unittest.TestCase):
    def manual_ctl(self, at, **kw):
        kw.setdefault("openDurationMs", 100)
        kw.setdefault("closeDurationMs", 100)
        return make_controller(now=at, mode="manual", manualOpenHour=7, manualOpenMin=0,
                               manualCloseHour=20, manualCloseMin=0, **kw)

    def test_fires_open_once_at_target_minute(self):
        ctl, relays, now = self.manual_ctl(datetime(2026, 10, 5, 6, 59, 30))
        ctl.scheduler_tick()
        self.assertFalse(relays.open_on)
        now.dt = datetime(2026, 10, 5, 7, 0, 1)
        ctl.scheduler_tick()
        self.assertTrue(relays.open_on)
        self.assertEqual(ctl.settings["lastActionSource"], "schedule")
        time.sleep(0.15)
        n = len(relays.history)
        now.dt = datetime(2026, 10, 5, 7, 0, 40)
        ctl.scheduler_tick()
        self.assertEqual(len(relays.history), n, "fired twice in the same minute")

    def test_fires_again_next_day(self):
        ctl, relays, now = self.manual_ctl(datetime(2026, 10, 5, 7, 0))
        ctl.scheduler_tick()
        time.sleep(0.15)
        now.dt = datetime(2026, 10, 5, 20, 0)
        ctl.scheduler_tick()
        time.sleep(0.15)
        now.dt = datetime(2026, 10, 6, 7, 0)
        ctl.scheduler_tick()
        self.assertTrue(relays.open_on)

    def test_skips_redundant_move_silently(self):
        ctl, relays, now = self.manual_ctl(datetime(2026, 10, 5, 20, 0), lastAction=ACTION_CLOSED)
        ctl.scheduler_tick()
        self.assertEqual(relays.history, [(False, False)])  # only the startup all-off
        self.assertEqual(ctl._last_close_day, now.dt.toordinal())

    def test_disabled_scheduler_does_nothing(self):
        ctl, relays, _ = self.manual_ctl(datetime(2026, 10, 5, 7, 0), schedulerEnabled=False)
        ctl.scheduler_tick()
        self.assertFalse(relays.open_on)

    def test_waits_for_clock_sync(self):
        ctl, relays, _ = self.manual_ctl(datetime(2026, 10, 5, 7, 0), synced=False)
        ctl.scheduler_tick()
        self.assertFalse(relays.open_on)
        ctl.clock.synced = True
        ctl.scheduler_tick()
        self.assertTrue(relays.open_on)

    def test_sun_mode_targets(self):
        # Oct 5: sunrise 07:01, sunset 18:30 (see test_suntimes)
        ctl, relays, now = make_controller(now=datetime(2026, 10, 5, 12, 0), mode="sun",
                                           sunOpenOffsetMin=30, sunCloseOffsetMin=-15,
                                           openDurationMs=100, closeDurationMs=100)
        st = ctl.status()
        self.assertEqual((st["sunriseTime"], st["sunsetTime"]), ("07:01", "18:30"))
        self.assertEqual((st["calcOpenTime"], st["calcCloseTime"]), ("07:31", "18:15"))
        now.dt = datetime(2026, 10, 5, 18, 15)
        ctl.scheduler_tick()
        self.assertTrue(relays.close_on)

    def test_save_clears_day_latch(self):
        ctl, relays, now = self.manual_ctl(datetime(2026, 10, 5, 7, 0))
        ctl.scheduler_tick()
        time.sleep(0.15)
        ctl.manual_move(ACTION_CLOSED)
        time.sleep(0.15)
        now.dt = datetime(2026, 10, 5, 9, 0)
        ctl.save_form({"manOpen": "09:00"})
        ctl.scheduler_tick()
        self.assertTrue(relays.open_on, "new schedule time didn't fire after save")


class SaveFormTest(unittest.TestCase):
    FULL = {"openDur": "12500", "closeDur": "11000", "schedulerEnabled": "on", "mode": "manual",
            "manOpen": "06:45", "manClose": "19:30", "sunOpenOffset": "-10", "sunCloseOffset": "20"}

    def test_full_form(self):
        ctl, _, _ = make_controller()
        ctl.save_form(self.FULL)
        s = ctl.settings
        self.assertEqual((s["openDurationMs"], s["closeDurationMs"]), (12500, 11000))
        self.assertEqual((s["manualOpenHour"], s["manualOpenMin"]), (6, 45))
        self.assertEqual((s["sunOpenOffsetMin"], s["sunCloseOffsetMin"]), (-10, 20))
        self.assertEqual(s["mode"], "manual")
        with open(s.path) as f:
            self.assertEqual(json.load(f)["manualCloseHour"], 19)

    def test_invalid_saves_nothing(self):
        ctl, _, _ = make_controller()
        before = ctl.settings.as_dict()
        bad = dict(self.FULL, closeDur="999999")
        with self.assertRaises(SaveError):
            ctl.save_form(bad)
        self.assertEqual(ctl.settings.as_dict(), before)
        for field, value in (("openDur", "abc"), ("manOpen", "25:00"), ("sunOpenOffset", "9999")):
            with self.assertRaises(SaveError):
                ctl.save_form({field: value})

    def test_scheduler_off(self):
        ctl, _, _ = make_controller()
        ctl.save_form({"schedulerEnabled": "off"})
        self.assertFalse(ctl.settings["schedulerEnabled"])


class SettingsFileTest(unittest.TestCase):
    def test_corrupt_file_falls_back_to_defaults(self):
        ctl, _, _ = make_controller()
        with open(ctl.settings.path, "w") as f:
            f.write("{ not json")
        s = Settings(ctl.settings.path)
        s.load()
        self.assertEqual(s["openDurationMs"], 10000)

    def test_bad_field_uses_default_others_kept(self):
        ctl, _, _ = make_controller()
        with open(ctl.settings.path, "w") as f:
            json.dump({"openDurationMs": -5, "closeDurationMs": 7000, "mode": "manual"}, f)
        s = Settings(ctl.settings.path)
        s.load()
        self.assertEqual(s["openDurationMs"], 10000)
        self.assertEqual(s["closeDurationMs"], 7000)
        self.assertEqual(s["mode"], "manual")


if __name__ == "__main__":
    unittest.main()
