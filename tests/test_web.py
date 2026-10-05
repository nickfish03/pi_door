"""HTTP routes behave like the NodeMCU's (status codes, JSON keys, 409 flow)."""

import time
import unittest

from tests.helpers import make_controller
from coopdoor.web import create_app


class WebTest(unittest.TestCase):
    def setUp(self):
        self.ctl, self.relays, _ = make_controller(openDurationMs=100, closeDurationMs=100)
        self.client = create_app(self.ctl).test_client()

    def test_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Coop Door", r.data)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        r.close()

    def test_status_has_every_field_the_page_reads(self):
        import re
        with open("coopdoor/static/index.html") as f:
            page_fields = set(re.findall(r"\bd\.([a-zA-Z]+)", f.read())) - {"message", "value"}
        d = self.client.get("/status").get_json()
        self.assertFalse(page_fields - set(d), "page reads fields /status doesn't send")

    def test_open_then_409_then_confirm(self):
        self.assertEqual(self.client.post("/open").status_code, 200)
        time.sleep(0.15)
        r = self.client.post("/open")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["needsConfirm"])
        self.assertFalse(self.relays.open_on)
        self.assertEqual(self.client.post("/open?confirm=1").status_code, 200)
        self.assertTrue(self.relays.open_on)

    def test_get_open_not_allowed(self):
        self.assertEqual(self.client.get("/open").status_code, 405)

    def test_stop(self):
        self.ctl.settings["openDurationMs"] = 5000
        self.client.post("/open")
        self.assertEqual(self.client.get("/status").get_json()["doorState"], "opening")
        self.assertEqual(self.client.post("/stop").status_code, 200)
        d = self.client.get("/status").get_json()
        self.assertEqual((d["doorState"], d["lastAction"]), ("idle", "unknown"))

    def test_save_ok_and_rejected(self):
        r = self.client.post("/save", data={"openDur": "15000", "mode": "manual"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/status").get_json()["openDurationMs"], 15000)
        r = self.client.post("/save", data={"openDur": "10"})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"open duration", r.data)


if __name__ == "__main__":
    unittest.main()
