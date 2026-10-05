# ============================================================================
# ntpcheck.py - "will this server give the Pi its time?"
#
#   python3 -m coopdoor.ntpcheck              # checks the default router
#   python3 -m coopdoor.ntpcheck 192.168.1.1  # checks a specific server
#
# Sends one standard SNTP request and reports what came back, using the
# same acceptance rules as systemd-timesyncd (the Pi's time service):
# the server must answer, claim a stratum of 1-15, not flag itself as
# unsynchronized, and have a root distance under 5 s. If this says "usable",
# timesyncd will take time from it. install.sh runs it against the router.
#
# Exit code 0 = usable, 1 = answered but not usable, 2 = no answer.
#
# A handy test: unplug the router's WAN (internet) cable for a few minutes
# and run this again. If it still says "usable", the Pi can keep setting its
# clock from the router through an internet outage.
# ============================================================================

import socket
import struct
import subprocess
import sys
import time

NTP_PORT = 123
_NTP_EPOCH_OFFSET = 2208988800  # seconds from 1900-01-01 to 1970-01-01
_MAX_ROOT_DISTANCE_S = 5.0      # timesyncd's RootDistanceMaxSec default


def default_gateway():
    """The Pi's default router address, or None."""
    try:
        out = subprocess.run(["ip", "-4", "route", "show", "default"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    return None


def _to_ntp(t):
    secs = int(t) + _NTP_EPOCH_OFFSET
    return secs, int((t - int(t)) * 2 ** 32)


def _from_ntp(secs, frac):
    return secs - _NTP_EPOCH_OFFSET + frac / 2 ** 32


def query(host, port=NTP_PORT, timeout=3.0):
    """One SNTP v4 request. Returns a dict describing the reply, or raises
    OSError / socket.timeout if nothing usable came back."""
    packet = bytearray(48)
    packet[0] = (0 << 6) | (4 << 3) | 3  # LI=0, version 4, mode 3 (client)
    t1 = time.time()
    struct.pack_into("!II", packet, 40, *_to_ntp(t1))  # transmit timestamp

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(bytes(packet), (host, port))
        data, _ = s.recvfrom(512)
    t4 = time.time()
    if len(data) < 48:
        raise OSError("short reply (%d bytes)" % len(data))

    li_vn_mode, stratum = data[0], data[1]
    root_delay, root_disp = struct.unpack("!II", data[4:12])
    ref_id = data[12:16]
    orig = struct.unpack("!II", data[24:32])
    recv = _from_ntp(*struct.unpack("!II", data[32:40]))
    xmit = _from_ntp(*struct.unpack("!II", data[40:48]))

    if orig != _to_ntp(t1):
        raise OSError("reply doesn't match our request")
    leap = li_vn_mode >> 6
    mode = li_vn_mode & 7
    root_delay_s = root_delay / 2 ** 16
    root_disp_s = root_disp / 2 ** 16

    if 2 <= stratum <= 15:
        ref = ".".join(str(b) for b in ref_id)   # upstream server's IP
    else:
        ref = ref_id.decode("ascii", "replace").strip("\x00 ")  # e.g. GPS, LOCL, INIT

    return {
        "leap": leap,
        "stratum": stratum,
        "mode": mode,
        "reference": ref,
        "root_distance_s": root_delay_s / 2 + root_disp_s,
        "offset_s": ((recv - t1) + (xmit - t4)) / 2,
        "server_time": xmit,
    }


def problems(reply):
    """Reasons timesyncd would reject this reply (empty list = usable)."""
    out = []
    if reply["mode"] != 4:
        out.append("not a server reply (mode %d)" % reply["mode"])
    if reply["leap"] == 3:
        out.append("server says its own clock is NOT synchronized")
    if not 1 <= reply["stratum"] <= 15:
        out.append("stratum %d (server has no valid time source)" % reply["stratum"])
    if reply["root_distance_s"] > _MAX_ROOT_DISTANCE_S:
        out.append("root distance %.1f s is over the %.0f s limit"
                   % (reply["root_distance_s"], _MAX_ROOT_DISTANCE_S))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    host = argv[0] if argv else default_gateway()
    if not host:
        print("No default router found - is the network up?")
        return 2
    try:
        r = query(host)
    except (OSError, socket.timeout) as e:
        print("%s: NO ANSWER to a time request (%s)." % (host, e))
        print("  Either it isn't serving time, or a firewall rule blocks UDP port 123")
        print("  from this network to it. See README 'Time from the router'.")
        return 2
    print("%s: answered - stratum %d, upstream %s, clock offset vs this Pi %+.3f s,"
          " root distance %.3f s" % (host, r["stratum"], r["reference"] or "?",
                                     r["offset_s"], r["root_distance_s"]))
    issues = problems(r)
    if issues:
        print("  NOT usable by the Pi: " + "; ".join(issues))
        return 1
    print("  Usable: the Pi will take its time from %s." % host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
