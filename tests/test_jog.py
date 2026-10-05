"""Fine tuning (Up/Down nudges): limits, interlock independence, tally."""

import time
import unittest

from tests.helpers import make_controller
from coopdoor.controller import DoorBusy, SaveError
from coopdoor.settings import ACTION_CLOSED, ACTION_OPEN
from coopdoor.web import create_app


class JogTest(unittest.TestCase):
    def test_up_and_down_drive_the_right_relays(self):
        ctl, relays, _ = make_controller()
        ctl.jog("up", 100)
        self.assertEqual((relays.open_on, relays.close_on), (True, False))
        time.sleep(0.15)
        self.assertEqual((relays.open_on, relays.close_on), (False, False))
        ctl.jog("down", 100)
        self.assertEqual((relays.open_on, relays.close_on), (False, True))
        time.sleep(0.15)
        self.assertFalse(relays.close_on)

    def test_does_not_touch_interlock(self):
        ctl, _, _ = make_controller(lastAction=ACTION_OPEN)
        before = ctl.settings.as_dict()
        ctl.jog("up", 100)          # same direction as last action: no confirm needed
        time.sleep(0.15)
        self.assertEqual(ctl.settings.as_dict(), before)

    def test_limits(self):
        ctl, relays, _ = make_controller()
        for bad in (0, 49, 5001, "abc", ""):
            with self.assertRaises(SaveError):
                ctl.jog("up", bad)
        with self.assertRaises(SaveError):
            ctl.jog("sideways", 500)
        self.assertEqual(relays.history, [(False, False)])

    def test_refused_while_moving(self):
        ctl, relays, _ = make_controller(openDurationMs=5000)
        ctl.manual_move(ACTION_OPEN)
        with self.assertRaises(DoorBusy):
            ctl.jog("down", 500)
        self.assertTrue(relays.open_on, "full move was disturbed")
        ctl.manual_stop()

    def test_stop_during_jog_keeps_last_action(self):
        ctl, relays, _ = make_controller(lastAction=ACTION_CLOSED)
        ctl.jog("up", 2000)
        time.sleep(0.2)
        ctl.manual_stop()
        self.assertFalse(relays.open_on)
        self.assertEqual(ctl.settings["lastAction"], ACTION_CLOSED)
        # Only the ~200 ms that actually ran is counted.
        self.assertTrue(150 <= ctl.status()["jogNetMs"] <= 400, ctl.status()["jogNetMs"])

    def test_stop_during_full_move_still_resets_to_unknown(self):
        ctl, _, _ = make_controller(openDurationMs=5000)
        ctl.jog("up", 100)
        time.sleep(0.15)
        ctl.manual_move(ACTION_OPEN)
        ctl.manual_stop()
        self.assertEqual(ctl.settings["lastAction"], "unknown")

    def test_tally_and_reset_on_full_move(self):
        ctl, _, _ = make_controller(closeDurationMs=100)
        for d in ("up", "up", "down"):
            ctl.jog(d, 100)
            time.sleep(0.15)
        self.assertEqual(ctl.status()["jogNetMs"], 100)
        ctl.manual_move(ACTION_CLOSED)
        self.assertEqual(ctl.status()["jogNetMs"], 0)


class JogWebTest(unittest.TestCase):
    def test_route(self):
        ctl, relays, _ = make_controller(openDurationMs=5000)
        c = create_app(ctl).test_client()
        self.assertEqual(c.post("/jog", data={"dir": "up", "ms": "500"}).status_code, 200)
        self.assertTrue(relays.open_on)
        self.assertEqual(c.post("/jog", data={"dir": "down", "ms": "500"}).status_code, 409)
        c.post("/stop")
        self.assertEqual(c.post("/jog", data={"dir": "up", "ms": "99999"}).status_code, 400)
        self.assertEqual(c.get("/jog").status_code, 405)


if __name__ == "__main__":
    unittest.main()
