"""ntpcheck against a fake NTP server on localhost."""

import socket
import struct
import threading
import time
import unittest

from coopdoor import ntpcheck


class FakeNtpServer:
    """Answers one request per call to serve(), like a router's ntpd would."""

    def __init__(self, leap=0, stratum=3, ref=(192, 168, 1, 1), root_disp_s=0.05, skew_s=0.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.leap, self.stratum, self.ref = leap, stratum, ref
        self.root_disp, self.skew = root_disp_s, skew_s
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        data, addr = self.sock.recvfrom(512)
        now = time.time() + self.skew
        secs, frac = ntpcheck._to_ntp(now)
        reply = bytearray(48)
        reply[0] = (self.leap << 6) | (4 << 3) | 4  # mode 4 = server
        reply[1] = self.stratum
        struct.pack_into("!II", reply, 4, 0, int(self.root_disp * 2 ** 16))
        reply[12:16] = bytes(self.ref)
        reply[24:32] = data[40:48]                   # originate = client's transmit
        struct.pack_into("!II", reply, 32, secs, frac)
        struct.pack_into("!II", reply, 40, secs, frac)
        self.sock.sendto(bytes(reply), addr)

    def close(self):
        self.sock.close()


class NtpCheckTest(unittest.TestCase):
    def test_good_router(self):
        srv = FakeNtpServer(skew_s=2.0)
        r = ntpcheck.query("127.0.0.1", port=srv.port)
        srv.close()
        self.assertEqual(r["stratum"], 3)
        self.assertEqual(r["reference"], "192.168.1.1")
        self.assertAlmostEqual(r["offset_s"], 2.0, delta=0.1)
        self.assertEqual(ntpcheck.problems(r), [])

    def test_unsynced_router_rejected(self):
        # What a router's ntpd typically says when it has lost its upstream
        # (or just booted with no internet): leap=3, stratum 16.
        srv = FakeNtpServer(leap=3, stratum=16, ref=b"INIT")
        r = ntpcheck.query("127.0.0.1", port=srv.port)
        srv.close()
        p = ntpcheck.problems(r)
        self.assertTrue(any("NOT synchronized" in x for x in p))
        self.assertTrue(any("stratum 16" in x for x in p))
        self.assertEqual(r["reference"], "INIT")

    def test_huge_root_distance_rejected(self):
        srv = FakeNtpServer(root_disp_s=9.0)
        r = ntpcheck.query("127.0.0.1", port=srv.port)
        srv.close()
        self.assertTrue(any("root distance" in x for x in ntpcheck.problems(r)))

    def test_no_answer(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))          # bound but never replies
        port = s.getsockname()[1]
        with self.assertRaises(OSError):  # socket.timeout is an OSError
            ntpcheck.query("127.0.0.1", port=port, timeout=0.3)
        s.close()


if __name__ == "__main__":
    unittest.main()
