#!/usr/bin/env python3
"""Unit tests for the pure (no-radio, no-TX) parts of bin/qso.py.

Covers: pick_offset parity filtering / ceiling / exclusion / higher-freq
preference, directed-CQ parsing + filter policy, busy-detection, and
stalled-CQ detection. Run: python3 tools/test_sequencer.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import qso

EVEN, ODD = "000000", "000015"          # slot ids with even/odd parity

def dec(freq, slot=EVEN, snr=-10, msg="CQ K0XXX EN10"):
    return {"freq": freq, "slot": slot, "snr": snr, "msg": msg}


class TestPickOffset(unittest.TestCase):
    def test_other_parity_ignored(self):
        # even-slot signals at 400/800/2400 leave 800-2400 as the big gap;
        # the odd-slot signal at 1600 sits mid-gap and must NOT count for an
        # even-parity transmitter -> we land right on it
        decs = [dec(400, EVEN), dec(800, EVEN), dec(2400, EVEN), dec(1600, ODD)]
        f0, gap = qso.pick_offset(decs, our_parity=0)
        self.assertEqual(f0, 1600)

    def test_same_parity_blocks(self):
        # the same signal in OUR parity slot must push us off 1600
        decs = [dec(400, EVEN), dec(800, EVEN), dec(2400, EVEN), dec(1600, EVEN)]
        f0, gap = qso.pick_offset(decs, our_parity=0)
        self.assertNotEqual(f0, 1600)
        self.assertGreaterEqual(min(abs(f0 - x) for x in (400, 800, 1600, 2400)), 60)

    def test_ceiling_and_floor(self):
        for decs in ([], [dec(1425)], [dec(500), dec(700), dec(900)],
                     [dec(f) for f in range(450, 2450, 73)]):
            f0, _ = qso.pick_offset(decs, 0)
            self.assertGreaterEqual(f0, 400)
            self.assertLessEqual(f0, 2450)

    def test_exclusion_zone(self):
        # empty band would pick 1425; excluding it must move >100 Hz away
        f0_free, _ = qso.pick_offset([], 0)
        f0, _ = qso.pick_offset([], 0, exclude_hz=[f0_free])
        self.assertGreater(abs(f0 - f0_free), 100)

    def test_higher_freq_preferred_among_near_equal_gaps(self):
        # 340-1400 and 1400-2510 are within 20% of each other -> take the
        # higher-frequency candidate
        f0, _ = qso.pick_offset([dec(1400, EVEN)], 0)
        self.assertGreater(f0, 1400)

    def test_clearance_at_least_60(self):
        decs = [dec(1000, EVEN), dec(1300, EVEN)]
        f0, _ = qso.pick_offset(decs, 0)
        self.assertGreaterEqual(min(abs(f0 - 1000), abs(f0 - 1300)), 60)


class TestCQFilter(unittest.TestCase):
    def decision(self, msg):
        """None = not answerable; else (call, grid)."""
        pc = qso.parse_cq(msg)
        if not pc:
            return None
        call, grid, mod = pc
        return (call, grid) if qso.cq_answerable(mod) else None

    def test_plain_cq_answer(self):
        self.assertEqual(self.decision("CQ K1ABC FN42"), ("K1ABC", "FN42"))

    def test_cq_dx_skip(self):
        self.assertIsNone(self.decision("CQ DX K1ABC"))

    def test_cq_pota_answer(self):
        self.assertEqual(self.decision("CQ POTA W9AV EN53"), ("W9AV", "EN53"))

    def test_cq_eu_skip(self):
        self.assertIsNone(self.decision("CQ EU K1ABC"))

    def test_cq_test_skip(self):
        self.assertIsNone(self.decision("CQ TEST K1ABC"))

    def test_cq_qrp_answer(self):
        self.assertEqual(self.decision("CQ QRP K1ABC"), ("K1ABC", ""))

    def test_short_call_with_grid(self):
        # K2A is a call (grid follows), not a modifier
        self.assertEqual(self.decision("CQ K2A FN13"), ("K2A", "FN13"))

    def test_no_grid_answer_empty_grid(self):
        self.assertEqual(self.decision("CQ N0XYZ"), ("N0XYZ", ""))

    def test_case_insensitive_modifier(self):
        self.assertIsNone(self.decision("CQ dx K1ABC"))
        self.assertEqual(self.decision("CQ pota W9AV EN53"), ("W9AV", "EN53"))

    def test_compound_call(self):
        self.assertEqual(self.decision("CQ VE3ABC/W8"), ("VE3ABC/W8", ""))

    def test_jt9_artifact_stripped(self):
        # rx-loop decodes carry trailing decoder markers like 'a1'
        self.assertEqual(self.decision("CQ AD9DU EN52                         a1"),
                         ("AD9DU", "EN52"))

    def test_not_a_cq(self):
        self.assertIsNone(self.decision("W9XYZ K1ABC -05"))


class TestBusyDetection(unittest.TestCase):
    def test_report_to_other_is_busy(self):
        self.assertTrue(qso.target_busy("W9XYZ K1ABC -05", "K1ABC"))
        self.assertTrue(qso.target_busy("W9XYZ K1ABC R-12", "K1ABC"))

    def test_report_to_us_is_not_busy(self):
        self.assertFalse(qso.target_busy("N0CALL K1ABC -05", "K1ABC", mycall="N0CALL"))

    def test_cq_is_not_busy_and_free(self):
        self.assertFalse(qso.target_busy("CQ K1ABC FN42", "K1ABC"))
        self.assertTrue(qso.target_free("CQ K1ABC FN42", "K1ABC"))

    def test_rr73_to_other_reads_as_free(self):
        # RR73 ends his QSO -> we may resume calling next cycle
        self.assertTrue(qso.target_free("W9XYZ K1ABC RR73", "K1ABC"))

    def test_other_station_message_not_busy(self):
        self.assertFalse(qso.target_busy("W9XYZ N4QQQ -05", "K1ABC"))


class TestStalledCQDetection(unittest.TestCase):
    def test_target_cq_detected(self):
        self.assertTrue(qso.is_target_cq("CQ K1ABC FN42", "K1ABC"))
        self.assertTrue(qso.is_target_cq("CQ DX K1ABC", "K1ABC"))
        self.assertTrue(qso.is_target_cq("CQ K1ABC", "K1ABC"))

    def test_non_cq_not_detected(self):
        self.assertFalse(qso.is_target_cq("W9XYZ K1ABC RR73", "K1ABC"))
        self.assertFalse(qso.is_target_cq("K1ABC N0CALL AA00", "K1ABC"))

    def test_other_cq_not_detected(self):
        self.assertFalse(qso.is_target_cq("CQ W9XYZ EN53", "K1ABC"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
