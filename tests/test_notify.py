import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shelly_app.automation import Automation, normalize_schedule
from shelly_app.history import History
from shelly_app.notify import Notifier, default_notify, normalize_notify, publish
from tests.test_automation import Clock, FakePlug, FakeStore

MONDAY = datetime(2026, 9, 28)


class FakeNtfy(BaseHTTPRequestHandler):
    received = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.received.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        out = b'{"id":"x"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def ntfy_server():
    cls = type("N", (FakeNtfy,), {"received": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, cls.received


class NotifyAlertsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = History(Path(self.tmp.name) / "h.db")
        self.plug = FakePlug()
        self.sent = []
        self.cfg = normalize_notify({"enabled": True, "app_url": "http://100.1.2.3:8080",
                                     "high": {"enabled": True, "threshold_w": 400, "minutes": 15}})

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, rules, start):
        self.clock = Clock(start)
        sched = normalize_schedule({"rules": rules, "protect": {"enabled": True, "threshold_w": 30, "idle_minutes": 5}})
        store = FakeStore(sched)
        store.devices[0]["name"] = "Pc"
        notifier = Notifier(lambda: self.cfg, send_fn=lambda cfg, p: self.sent.append(p), background=False)
        return Automation(store, self.history, lambda d: self.plug, clock=self.clock, notifier=notifier)

    def titles(self):
        return [p["title"] for p in self.sent]

    def test_postponed_then_done(self):
        a = self.make([{"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"}], MONDAY.replace(hour=22, minute=59))
        self.plug.power = 250
        self.clock.advance(120, a)
        self.assertEqual(self.titles(), ["Arrêt de 23:00 reporté"])
        self.assertIn("Couper la prise", [x["label"] for x in self.sent[0]["actions"]])
        self.plug.power = 3
        self.clock.advance(7 * 60, a)
        self.assertEqual(self.titles(), ["Arrêt de 23:00 reporté", "Prise coupée"])

    def test_late_alert_once_per_night_with_cut_button(self):
        a = self.make([], MONDAY.replace(hour=1, minute=58))
        self.plug.power = 180
        self.clock.advance(15 * 60, a)
        self.assertEqual(self.titles(), ["Pc encore allumé à 02:00"])
        cut = [x for x in self.sent[0]["actions"] if x["action"] == "http"][0]
        self.assertEqual(cut["url"], "http://100.1.2.3:8080/api/devices/d1/switch")
        self.assertEqual(json.loads(cut["body"]), {"action": "off"})
        self.assertEqual(self.sent[0]["topic"], self.cfg["topic"])

    def test_no_late_alert_when_pc_is_off(self):
        a = self.make([], MONDAY.replace(hour=1, minute=58))
        self.plug.power = 2
        self.clock.advance(15 * 60, a)
        self.assertEqual(self.sent, [])

    def test_offline_then_back(self):
        a = self.make([], MONDAY.replace(hour=12))
        self.clock.advance(120, a)
        self.plug.offline = True
        self.clock.advance(9 * 60, a)
        self.assertEqual(self.sent, [])
        self.clock.advance(3 * 60, a)
        self.assertEqual(self.titles(), ["Pc ne répond plus"])
        self.clock.advance(30 * 60, a)
        self.assertEqual(len(self.sent), 1, "une seule alerte par coupure")
        self.plug.offline = False
        self.clock.advance(2 * 60, a)
        self.assertEqual(self.titles(), ["Pc ne répond plus", "Pc répond de nouveau"])

    def test_high_power(self):
        a = self.make([], MONDAY.replace(hour=12))
        self.plug.power = 500
        self.clock.advance(14 * 60, a)
        self.assertEqual(self.sent, [])
        self.clock.advance(3 * 60, a)
        self.assertEqual(self.titles(), ["Conso élevée : Pc"])
        self.clock.advance(30 * 60, a)
        self.assertEqual(len(self.sent), 1)

    def test_disabled_sends_nothing(self):
        self.cfg = normalize_notify({"enabled": False})
        a = self.make([{"id": "a", "time": "23:00", "days": list(range(7)), "action": "off"}], MONDAY.replace(hour=22, minute=59))
        self.plug.power = 250
        self.clock.advance(3600, a)
        self.assertEqual(self.sent, [])


class NotifyTransportTests(unittest.TestCase):
    def test_publish_json_with_token(self):
        srv, received = ntfy_server()
        cfg = normalize_notify({"server": f"http://127.0.0.1:{srv.server_port}", "token": "tk_abc"})
        publish(cfg, {"topic": cfg["topic"], "title": "Été 🌞", "message": "ok", "priority": 3, "tags": []})
        self.assertEqual(received[0]["path"], "/")
        self.assertEqual(received[0]["auth"], "Bearer tk_abc")
        self.assertEqual(received[0]["body"]["title"], "Été 🌞")
        srv.shutdown(); srv.server_close()

    def test_publish_error(self):
        cfg = normalize_notify({"server": "http://127.0.0.1:1"})
        with self.assertRaises(RuntimeError):
            publish(cfg, {"topic": "x", "message": "m"})

    def test_validation_and_random_topic(self):
        self.assertNotEqual(default_notify()["topic"], default_notify()["topic"])
        for bad in ({"topic": "a b"}, {"late": {"time": "25:00"}}, {"offline": {"minutes": 0}}):
            with self.assertRaises(ValueError):
                normalize_notify(bad)

    def test_server_routes(self):
        from shelly_app.server import make_server
        srv, received = ntfy_server()
        with tempfile.TemporaryDirectory() as tmp:
            app = make_server("127.0.0.1", 0, Path(tmp) / "d.json")
            threading.Thread(target=app.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{app.server_port}"

            def req(method, path, body=None):
                r = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                           method=method, headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(r) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())

            _, data = req("GET", "/api/settings")
            topic = data["notify"]["topic"]
            self.assertTrue(topic.startswith("shelly-"))
            self.assertEqual(req("GET", "/api/settings")[1]["notify"]["topic"], topic, "sujet stable")
            self.assertEqual(req("POST", "/api/notify/test", {})[0], 400)  # désactivées
            code, data = req("PUT", "/api/settings", {"notify": {"enabled": True, "server": f"http://127.0.0.1:{srv.server_port}"}})
            self.assertEqual((code, data["notify"]["enabled"], data["notify"]["topic"]), (200, True, topic))
            self.assertEqual(req("POST", "/api/notify/test", {})[0], 200)
            self.assertEqual(received[0]["body"]["topic"], topic)
            self.assertEqual(req("PUT", "/api/settings", {"notify": {"topic": "pas bon"}})[0], 400)
            app.shutdown(); app.server_close()
        srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    unittest.main()
