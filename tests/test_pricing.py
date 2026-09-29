import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shelly_app.history import History
from shelly_app.pricing import cost, normalize_pricing, off_peak_checker


def ts(h, m=0, day=28):
    return datetime(2026, 9, day, h, m).timestamp()


class PricingTests(unittest.TestCase):
    def test_off_peak_ranges(self):
        check = off_peak_checker(normalize_pricing({"hc_enabled": True, "hc_ranges": [["22:00", "06:00"], ["12:30", "14:00"]]}))
        self.assertTrue(check(ts(23)))
        self.assertTrue(check(ts(2)))
        self.assertTrue(check(ts(13)))
        self.assertFalse(check(ts(6)))
        self.assertFalse(check(ts(21, 59)))
        self.assertFalse(check(ts(14)))
        self.assertIsNone(off_peak_checker(normalize_pricing({"hc_enabled": False})))

    def test_cost(self):
        base = normalize_pricing({"base": 0.20})
        self.assertAlmostEqual(cost(1500, 0, base), 0.30)
        hphc = normalize_pricing({"hc_enabled": True, "hp": 0.25, "hc": 0.15})
        self.assertAlmostEqual(cost(2000, 1000, hphc), 0.40)
        self.assertIsNone(cost(None, None, base))

    def test_validation(self):
        for bad in ({"base": -1}, {"base": "abc"}, {"hc_enabled": True, "hc_ranges": []},
                    {"hc_ranges": [["25:00", "06:00"]]}, {"hc_ranges": [["06:00", "06:00"]]}):
            with self.assertRaises(ValueError):
                normalize_pricing(bad)

    def test_daily_split_off_peak(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = History(Path(tmp) / "h.db")
            energy = 0.0
            # 100 W constant de 20:00 à 23:59 : 2 h pleines puis 2 h creuses
            for minute in range(20 * 60, 24 * 60, 1):
                energy += 100 / 60
                h.add_sample("d", {"power": 100, "energy_wh": energy, "on": True},
                             ts(minute // 60, minute % 60))
            check = off_peak_checker(normalize_pricing({"hc_enabled": True, "hc_ranges": [["22:00", "06:00"]]}))
            day = h.daily_energy("d", 1, now=ts(23, 59), off_peak=check)[0]
            self.assertAlmostEqual(day["wh"], 400, delta=3)
            self.assertAlmostEqual(day["wh_hc"], 200, delta=3)
            wh, hc = h.energy_between("d", ts(21), ts(23), off_peak=check)
            self.assertAlmostEqual(wh, 200, delta=3)
            self.assertAlmostEqual(hc, 100, delta=3)
            self.assertEqual(h.energy_between("d", ts(1), ts(2)), (None, None))


if __name__ == "__main__":
    unittest.main()
