import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shelly_app.automation import Automation, normalize_schedule
from shelly_app.history import History
from shelly_app.shelly import ShellyError


class FakePlug:
    def __init__(self):
        self.on = True
        self.power = 0.0
        self.energy = 0.0
        self.calls = []
        self.offline = False

    def status(self):
        if self.offline:
            raise ShellyError("injoignable")
        return {"on": self.on, "power": self.power if self.on else 0.0, "energy_wh": self.energy}

    def switch(self, action, timer=None):
        if self.offline:
            raise ShellyError("injoignable")
        self.calls.append(action)
        self.on = action == "on"
        return self.status()


class FakeStore:
    def __init__(self, schedule):
        self.devices = [{"id": "d1", "schedule": schedule}]

    def all(self):
        return [dict(d) for d in self.devices]


class Clock:
    def __init__(self, dt):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds, automation, step=15):
        end = self.t + seconds
        while self.t < end:
            self.t += step
            automation.tick()


# Lundi 28 septembre 2026
MONDAY = datetime(2026, 9, 28)


def schedule(rules, **protect):
    return normalize_schedule(
        {"rules": rules, "protect": {"enabled": True, "threshold_w": 30, "idle_minutes": 5, **protect}}
    )


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = History(Path(self.tmp.name) / "h.db")
        self.plug = FakePlug()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, sched, start):
        self.clock = Clock(start)
        store = FakeStore(sched)
        return Automation(store, self.history, lambda d: self.plug, clock=self.clock)

    def messages(self):
        return [e["message"] for e in self.history.events("d1")]

    def test_rules_only_on_their_days(self):
        sched = schedule([
            {"id": "a", "time": "18:00", "days": [0], "action": "on"},  # lundi
            {"id": "b", "time": "18:00", "days": [1], "action": "off"},  # mardi
        ])
        self.plug.on = False
        a = self.make(sched, MONDAY.replace(hour=17, minute=59))
        self.clock.advance(120, a)
        self.assertEqual(self.plug.calls, ["on"])
        self.clock.advance(120, a)
        self.assertEqual(self.plug.calls, ["on"], "une règle ne doit s'appliquer qu'une fois")

    def test_off_postponed_while_gaming_then_applied(self):
        sched = schedule([{"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"}])
        a = self.make(sched, MONDAY.replace(hour=22, minute=59))
        self.plug.power = 250  # partie en cours
        self.clock.advance(120, a)
        self.assertEqual(self.plug.calls, [])
        self.assertIsNotNone(a.state("d1"))
        self.clock.advance(3600, a)  # toujours en train de jouer une heure plus tard
        self.assertEqual(self.plug.calls, [])

        self.plug.power = 3  # PC éteint, reste la veille
        self.clock.advance(4 * 60, a)
        self.assertEqual(self.plug.calls, [], "attendre idle_minutes sous le seuil")
        self.clock.advance(3 * 60, a)
        self.assertEqual(self.plug.calls, ["off"])
        self.assertIsNone(a.state("d1"))
        self.assertTrue(any("reporté" in m for m in self.messages()))

    def test_short_dip_resets_idle_timer(self):
        sched = schedule([{"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"}])
        a = self.make(sched, MONDAY.replace(hour=22, minute=59))
        self.plug.power = 200
        self.clock.advance(120, a)
        self.plug.power = 5  # écran de chargement
        self.clock.advance(3 * 60, a)
        self.plug.power = 200
        self.clock.advance(2 * 60, a)
        self.plug.power = 5
        self.clock.advance(4 * 60, a)
        self.assertEqual(self.plug.calls, [])

    def test_off_immediate_when_idle(self):
        sched = schedule([{"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"}])
        a = self.make(sched, MONDAY.replace(hour=22, minute=59))
        self.plug.power = 4
        self.clock.advance(120, a)
        self.assertEqual(self.plug.calls, ["off"])

    def test_protection_disabled_cuts_anyway(self):
        sched = schedule([{"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"}], enabled=False)
        a = self.make(sched, MONDAY.replace(hour=22, minute=59))
        self.plug.power = 250
        self.clock.advance(120, a)
        self.assertEqual(self.plug.calls, ["off"])

    def test_on_rule_cancels_pending_off(self):
        sched = schedule([
            {"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"},
            {"id": "b", "time": "23:30", "days": list(range(7)), "action": "on"},
        ])
        a = self.make(sched, MONDAY.replace(hour=22, minute=59))
        self.plug.power = 250
        self.clock.advance(35 * 60, a)
        self.assertEqual(self.plug.calls, ["on"])
        self.assertIsNone(a.state("d1"))

    def test_retries_when_plug_temporarily_offline(self):
        sched = schedule([{"id": "a", "time": "07:00", "days": list(range(7)), "action": "on"}])
        self.plug.on = False
        a = self.make(sched, MONDAY.replace(hour=6, minute=59))
        self.plug.offline = True
        self.clock.advance(120, a)
        self.plug.offline = False
        self.clock.advance(60, a)
        self.assertEqual(self.plug.calls, ["on"])

    def test_samples_and_daily_energy(self):
        a = self.make(schedule([]), MONDAY.replace(hour=10))
        self.plug.power = 120
        for _ in range(60):
            self.plug.energy += 2  # 120 W pendant une minute
            self.clock.advance(60, a, step=60)
        now = self.clock()
        daily = self.history.daily_energy("d1", 7, now=now)
        self.assertEqual(daily[-1]["date"], "2026-09-28")
        self.assertAlmostEqual(daily[-1]["wh"], 118, delta=4)
        self.assertIsNone(daily[0]["wh"])
        series = self.history.power_series("d1", now=now)
        self.assertEqual(max(p["avg"] or 0 for p in series), 120)

    def test_normalize_rejects_bad_input(self):
        for bad in (
            {"rules": [{"time": "25:00", "days": [0], "action": "on"}]},
            {"rules": [{"time": "10:00", "days": [], "action": "on"}]},
            {"rules": [{"time": "10:00", "days": [7], "action": "on"}]},
            {"rules": [{"time": "10:00", "days": [0], "action": "boom"}]},
            {"rules": [], "protect": {"threshold_w": -1}},
        ):
            with self.assertRaises(ValueError):
                normalize_schedule(bad)


if __name__ == "__main__":
    unittest.main()
