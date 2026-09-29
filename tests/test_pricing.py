import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shelly_app.history import History
from shelly_app.pricing import current_tariff, normalize_pricing, price_function

# Offre à 3 prix avec changement de tarif daté (ex. ENGIE Happy Heures Vertes)
ENGIE = {"tariffs": [
    {"from": None, "price": 0.24557, "periods": [
        {"name": "Heures creuses", "price": 0.19154, "start": "00:00", "end": "06:00"},
        {"name": "Happy Heures Vertes", "price": 0.03674, "start": "15:00", "end": "17:00"}]},
    {"from": "2026-11-01", "price": 0.26153, "periods": [
        {"name": "Heures creuses", "price": 0.20405, "start": "00:00", "end": "06:00"},
        {"name": "Happy Heures Vertes", "price": 0.03674, "start": "15:00", "end": "17:00"}]},
]}


def ts(h, m=0, day=28, month=9):
    return datetime(2026, month, day, h, m).timestamp()


class PricingTests(unittest.TestCase):
    def test_price_at_periods_and_dates(self):
        price = price_function(ENGIE)
        self.assertEqual(price(ts(10)), 0.24557)  # heures pleines
        self.assertEqual(price(ts(3)), 0.19154)  # heures creuses
        self.assertEqual(price(ts(16)), 0.03674)  # heures vertes
        self.assertEqual(price(ts(17)), 0.24557)  # fin de plage exclue
        self.assertEqual(price(ts(10, day=1, month=11)), 0.26153)  # nouveau tarif
        self.assertEqual(price(ts(3, day=1, month=11)), 0.20405)

    def test_period_over_midnight(self):
        price = price_function({"tariffs": [{"price": 0.25, "periods": [
            {"name": "HC", "price": 0.15, "start": "22:00", "end": "06:00"}]}]})
        self.assertEqual((price(ts(23)), price(ts(2)), price(ts(6)), price(ts(21, 59))), (0.15, 0.15, 0.25, 0.25))

    def test_legacy_format_is_migrated(self):
        p = normalize_pricing({"hc_enabled": True, "hp": 0.25, "hc": 0.15, "hc_ranges": [["22:00", "06:00"]]})
        self.assertEqual(p["tariffs"][0]["periods"][0]["price"], 0.15)
        self.assertEqual(normalize_pricing({"base": 0.2})["tariffs"][0], {"from": None, "price": 0.2, "periods": []})

    def test_current_tariff(self):
        p = normalize_pricing(ENGIE)
        self.assertEqual(current_tariff(p, datetime(2026, 10, 31).date())["price"], 0.24557)
        self.assertEqual(current_tariff(p, datetime(2026, 11, 1).date())["price"], 0.26153)

    def test_validation(self):
        bad = [
            {"tariffs": []},
            {"tariffs": [{"price": -1}]},
            {"tariffs": [{"price": "abc"}]},
            {"tariffs": [{"price": 0.2, "periods": [{"price": 0.1, "start": "25:00", "end": "06:00"}]}]},
            {"tariffs": [{"price": 0.2, "periods": [{"price": 0.1, "start": "06:00", "end": "06:00"}]}]},
            {"tariffs": [{"price": 0.2}, {"price": 0.3}]},  # pas de date
            {"tariffs": [{"price": 0.2}, {"from": "2026-12-01", "price": 0.3}, {"from": "2026-11-01", "price": 0.3}]},
        ]
        for b in bad:
            with self.assertRaises(ValueError, msg=b):
                normalize_pricing(b)

    def test_costs_follow_the_price_of_each_moment(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = History(Path(tmp) / "h.db")
            energy = 0.0
            # 1 kW constant de 14:00 à 17:59 : 1 h pleine, 2 h vertes, 1 h pleine
            for minute in range(14 * 60, 18 * 60):
                energy += 1000 / 60
                h.add_sample("d", {"power": 1000, "energy_wh": energy, "on": True}, ts(minute // 60, minute % 60))
            price = price_function(ENGIE)
            day = h.daily_energy("d", 1, now=ts(18), price_at=price)[0]
            self.assertAlmostEqual(day["wh"], 4000, delta=20)
            expected = 2 * 0.24557 + 2 * 0.03674
            self.assertAlmostEqual(day["eur"], expected, delta=0.01)
            wh, eur = h.energy_between("d", ts(15), ts(16, 59), price_at=price)
            self.assertAlmostEqual(eur, 2 * 0.03674, delta=0.005)
            self.assertEqual(h.energy_between("d", ts(1), ts(2)), (None, None))


if __name__ == "__main__":
    unittest.main()
