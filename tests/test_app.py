import hashlib
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shelly_app.server import make_server
from shelly_app.shelly import ShellyAuthError, ShellyClient


class FakeShelly(BaseHTTPRequestHandler):
    """Simule une prise Shelly Gen1 ou Gen2 (avec auth digest SHA-256 optionnelle)."""

    gen = 2
    password = None
    state = None

    def log_message(self, *args):
        pass

    def _send(self, payload, code=200, headers=()):
        body = json.dumps(payload).encode()
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not self.password or self.path.startswith("/shelly"):
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[7:]))
        H = lambda s: hashlib.sha256(s.encode()).hexdigest()
        ha1 = H(f"admin:shellyplug:{self.password}")
        ha2 = H(f"GET:{f['uri']}")
        expected = H(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:auth:{ha2}")
        return f.get("response") == expected

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        s = self.state
        if not self._authorized():
            return self._send(
                {"code": 401},
                401,
                [("WWW-Authenticate", 'Digest qop="auth", realm="shellyplug", nonce="abc", algorithm=SHA-256')],
            )
        if url.path == "/shelly":
            if self.gen == 1:
                return self._send({"type": "SHPLG-S", "mac": "AA", "auth": False})
            return self._send({"gen": 2, "model": "SNPL-00112EU", "name": "Salon", "mac": "BB", "auth_en": bool(self.password)})
        if self.gen == 1:
            if url.path == "/settings":
                return self._send({"name": "Cuisine"})
            if url.path == "/status":
                return self._send({"relays": [{"ison": s["on"], "has_timer": False}], "meters": [{"power": 12.5, "total": 600}], "temperature": 30.1})
            if url.path == "/relay/0":
                s["on"] = {"on": True, "off": False, "toggle": not s["on"]}[q["turn"]]
                s["timer"] = q.get("timer")
                return self._send({"ison": s["on"]})
        else:
            if url.path == "/rpc/Switch.GetStatus":
                return self._send({"id": 0, "output": s["on"], "apower": 42.0, "voltage": 230.2, "current": 0.2, "aenergy": {"total": 1500.0}, "temperature": {"tC": 35.0}})
            if url.path == "/rpc/Switch.Set":
                s["on"] = q["on"] == "true"
                s["timer"] = q.get("toggle_after")
                return self._send({"was_on": not s["on"]})
        self._send({"error": "not found"}, 404)


def start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def fake(gen, password=None):
    cls = type("F", (FakeShelly,), {"gen": gen, "password": password, "state": {"on": False, "timer": None}})
    return start(cls), cls.state


class ClientTests(unittest.TestCase):
    def test_gen2(self):
        srv, state = fake(2)
        c = ShellyClient(f"127.0.0.1:{srv.server_port}")
        self.assertEqual(c.info()["name"], "Salon")
        st = c.switch("on", timer=60)
        self.assertTrue(st["on"])
        self.assertEqual(state["timer"], "60")
        self.assertEqual(st["power"], 42.0)
        self.assertFalse(c.switch("toggle")["on"])
        srv.shutdown(); srv.server_close()

    def test_gen1(self):
        srv, state = fake(1)
        c = ShellyClient(f"127.0.0.1:{srv.server_port}")
        info = c.info()
        self.assertEqual((info["gen"], info["name"]), (1, "Cuisine"))
        st = c.switch("toggle")
        self.assertTrue(st["on"])
        self.assertEqual(st["energy_wh"], 10)
        srv.shutdown(); srv.server_close()

    def test_digest_auth(self):
        srv, _ = fake(2, password="secret")
        self.assertTrue(ShellyClient(f"127.0.0.1:{srv.server_port}", password="secret").switch("on")["on"])
        with self.assertRaises(ShellyAuthError):
            ShellyClient(f"127.0.0.1:{srv.server_port}", password="wrong").status()
        with self.assertRaises(ShellyAuthError):
            ShellyClient(f"127.0.0.1:{srv.server_port}").status()
        srv.shutdown(); srv.server_close()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "devices.json"
        self.app = make_server("127.0.0.1", 0, self.config)
        threading.Thread(target=self.app.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        self.plug, self.state = fake(2)

    def tearDown(self):
        self.app.shutdown(); self.app.server_close()
        self.plug.shutdown(); self.plug.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow(self):
        code, dev = self.req("POST", "/api/devices", {"host": f"127.0.0.1:{self.plug.server_port}", "password": "x"})
        self.assertEqual(code, 201)
        self.assertEqual(dev["name"], "Salon")
        self.assertNotIn("password", dev)
        self.assertEqual(self.req("GET", "/api/devices")[1][0]["id"], dev["id"])
        self.assertNotIn("password", self.req("GET", "/api/devices")[1][0])

        code, st = self.req("POST", f"/api/devices/{dev['id']}/switch", {"action": "toggle", "timer": 300})
        self.assertEqual((code, st["on"], self.state["timer"]), (200, True, "300"))
        self.assertTrue(self.req("GET", f"/api/devices/{dev['id']}/status")[1]["on"])

        self.assertEqual(self.req("POST", f"/api/devices/{dev['id']}/switch", {"action": "boom"})[0], 400)
        self.assertEqual(self.req("DELETE", f"/api/devices/{dev['id']}")[0], 200)
        self.assertEqual(json.loads(self.config.read_text()), [])

    def test_unreachable(self):
        self.assertEqual(self.req("POST", "/api/devices", {"host": "127.0.0.1:1"})[0], 502)

    def test_static(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"Mes prises", r.read())
        self.assertEqual(self.req("GET", "/../server.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
