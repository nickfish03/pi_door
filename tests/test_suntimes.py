"""Sunrise/sunset vs reference values for Rhinelander, WI.

Reference times are from the `astral` library (NOAA-based, the same family
of algorithm Dusk2Dawn uses), rounded to the nearest minute. Our result may
differ by at most 1 minute from rounding at the half-minute boundary."""

import unittest
from datetime import date

from tests import helpers  # noqa: F401  (sets TZ=America/Chicago)
from coopdoor.suntimes import compute_sun_times, format_minutes

# date -> (sunrise, sunset), local time
REFERENCE = {
    date(2026, 3, 7): ("06:25", "17:53"),    # day before DST starts
    date(2026, 3, 8): ("07:23", "18:54"),    # DST starts
    date(2026, 6, 21): ("05:09", "20:50"),   # summer solstice (sunset after 0h UTC)
    date(2026, 10, 5): ("07:01", "18:30"),
    date(2026, 11, 1): ("06:38", "16:44"),   # DST ends
    date(2026, 12, 21): ("07:36", "16:16"),  # winter solstice
}


def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class SunTimesTest(unittest.TestCase):
    def test_matches_reference_within_a_minute(self):
        for d, (rise, sset) in REFERENCE.items():
            r, s = compute_sun_times(d)
            self.assertLessEqual(abs(r - to_min(rise)), 1, "sunrise %s: got %s" % (d, format_minutes(r)))
            self.assertLessEqual(abs(s - to_min(sset)), 1, "sunset %s: got %s" % (d, format_minutes(s)))

    def test_dst_shifts_by_about_an_hour(self):
        r1, _ = compute_sun_times(date(2026, 3, 7))
        r2, _ = compute_sun_times(date(2026, 3, 8))
        self.assertTrue(55 <= r2 - r1 <= 60)

    def test_polar_returns_none(self):
        self.assertEqual(compute_sun_times(date(2026, 12, 21), lat=80.0, lon=0.0), (None, None))

    def test_format(self):
        self.assertEqual(format_minutes(None), "--:--")
        self.assertEqual(format_minutes(7 * 60 + 5), "07:05")


if __name__ == "__main__":
    unittest.main()
