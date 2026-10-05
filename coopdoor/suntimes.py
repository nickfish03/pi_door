# ============================================================================
# suntimes.py - sunrise/sunset for a given date and location
#
# The NodeMCU version used the Dusk2Dawn library, which is itself a port of
# NOAA's solar calculator. This is the same NOAA algorithm (from NOAA's
# published "General Solar Position" spreadsheet), written out directly so
# the Pi needs no extra packages. It's accurate to well under a minute at
# this latitude; tests/test_suntimes.py checks it against reference values.
#
# Results come back as minutes since LOCAL midnight. The conversion from
# UTC to local time goes through the Pi's own timezone database, so DST is
# handled automatically - there's no isDST flag to pass around any more.
# ============================================================================

import calendar
import math
import time
from datetime import date

from . import config

# Standard "official" sunrise/sunset zenith: 90 deg + 50 arcminutes, which
# accounts for atmospheric refraction and the sun's radius - the same
# definition Dusk2Dawn and almanacs use.
_ZENITH_DEG = 90.833


def _julian_day(d):
    """Julian day number at 0h UTC on date d."""
    y, m = d.year, d.month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d.day + b - 1524.5


def _declination_and_eot(jd):
    """Sun's declination (deg) and the equation of time (minutes) at
    Julian day jd. Straight from the NOAA spreadsheet's formulas."""
    t = (jd - 2451545.0) / 36525.0  # Julian centuries since J2000

    geom_mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    geom_mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccent = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m = math.radians(geom_mean_anom)
    eq_of_ctr = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
                 + math.sin(2 * m) * (0.019993 - 0.000101 * t)
                 + math.sin(3 * m) * 0.000289)
    true_long = geom_mean_long + eq_of_ctr
    omega = math.radians(125.04 - 1934.136 * t)
    app_long = true_long - 0.00569 - 0.00478 * math.sin(omega)

    mean_obliq = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    obliq_corr = math.radians(mean_obliq + 0.00256 * math.cos(omega))

    decl = math.degrees(math.asin(math.sin(obliq_corr) * math.sin(math.radians(app_long))))

    y = math.tan(obliq_corr / 2) ** 2
    l0 = math.radians(geom_mean_long)
    eot = 4 * math.degrees(
        y * math.sin(2 * l0)
        - 2 * eccent * math.sin(m)
        + 4 * eccent * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0)
        - 1.25 * eccent * eccent * math.sin(2 * m)
    )
    return decl, eot


def _event_utc_minutes(d, lat, lon, rising):
    """Minutes after 0h UTC on date d of sunrise (rising=True) or sunset.
    May be > 1440 (e.g. a summer sunset here is after midnight UTC).
    Returns None if the sun doesn't rise/set that day (polar)."""
    jd0 = _julian_day(d)
    # First guess: local solar noon. Then refine twice using the sun's
    # position at the previous estimate - converges well within a minute.
    minutes = 720 - 4 * lon
    for _ in range(3):
        decl, eot = _declination_and_eot(jd0 + minutes / 1440.0)
        lat_r, decl_r = math.radians(lat), math.radians(decl)
        cos_ha = (math.cos(math.radians(_ZENITH_DEG)) / (math.cos(lat_r) * math.cos(decl_r))
                  - math.tan(lat_r) * math.tan(decl_r))
        if cos_ha < -1 or cos_ha > 1:
            return None
        ha = math.degrees(math.acos(cos_ha))
        if rising:
            minutes = 720 - 4 * (lon + ha) - eot
        else:
            minutes = 720 - 4 * (lon - ha) - eot
    return minutes


def _utc_minutes_to_local_minutes(d, utc_minutes):
    """Converts minutes-after-0h-UTC on date d into minutes after local
    midnight, using the system timezone (so DST is automatic)."""
    epoch = calendar.timegm((d.year, d.month, d.day, 0, 0, 0)) + round(utc_minutes * 60)
    lt = time.localtime(epoch)
    return lt.tm_hour * 60 + lt.tm_min + (1 if lt.tm_sec >= 30 else 0)


def compute_sun_times(d, lat=config.LOCATION_LAT, lon=config.LOCATION_LON):
    """Returns (sunrise_min, sunset_min) in minutes since local midnight for
    local calendar date d, or None for either if it doesn't happen.

    Uses d as the UTC calendar date for the calculation, which is correct
    for the western hemisphere (local date and UTC date overlap for the
    whole daytime). Fine for Rhinelander; revisit if this ever moves east
    of Greenwich."""
    if not isinstance(d, date):
        raise TypeError("expected a datetime.date")
    out = []
    for rising in (True, False):
        m = _event_utc_minutes(d, lat, lon, rising)
        out.append(None if m is None else _utc_minutes_to_local_minutes(d, m) % 1440)
    return out[0], out[1]


def format_minutes(minutes):
    """Minutes since midnight -> "HH:MM", or "--:--" for None."""
    if minutes is None:
        return "--:--"
    return "%02d:%02d" % ((minutes // 60) % 24, minutes % 60)
