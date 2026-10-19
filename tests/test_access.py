import ipaddress
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from shelly_app import access
from shelly_app.server import main, make_server

ONLY_TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"),)


class AccessUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "access.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_without_pin(self):
        a = access.Access(self.path)
        self.assertFalse(a.enabled)
        self.assertTrue(a.allowed("192.168.1.50", None))

    def test_pin_required_on_lan_not_tailscale(self):
        access.set_pin(self.path, "123456")
        a = access.Access(self.path)
        self.assertTrue(a.enabled)
        self.assertFalse(a.allowed("192.168.1.50", None))
        self.assertFalse(a.allowed("192.168.1.50", "faux"))
        self.assertTrue(a.allowed("100.101.1.2", None))
        self.assertTrue(a.allowed("127.0.0.1", None))
        self.assertNotIn("123456", self.path.read_text())  # jamais en clair

        token, err = a.login("192.168.1.50", "123456")
        self.assertIsNone(err)
        self.assertTrue(a.allowed("192.168.1.50", token))

    def test_new_pin_revokes_old_token(self):
        access.set_pin(self.path, "1111")
        a = access.Access(self.path)
        token, _ = a.login("192.168.1.50", "1111")
        access.set_pin(self.path, "2222")
        self.assertFalse(a.allowed("192.168.1.50", token))
        access.remove_pin(self.path)
        self.assertTrue(a.allowed("192.168.1.50", None))

    def test_lockout_after_too_many_tries(self):
        access.set_pin(self.path, "1234")
        a = access.Access(self.path)
        for _ in range(access.MAX_FAILS):
            self.assertIsNone(a.login("192.168.1.50", "0000")[0])
        token, err = a.login("192.168.1.50", "1234")
        self.assertIsNone(token)
        self.assertIn("Trop d'essais", err)
        self.assertIsNotNone(a.login("192.168.1.51", "1234")[0])  # les autres appareils ne sont pas bloqués

    def test_pin_format(self):
        for bad in ("12", "abcd", "1234567890123", ""):
            with self.assertRaises(ValueError):
                access.set_pin(self.path, bad)

    def test_cli(self):
        config = Path(self.tmp.name) / "devices.json"
        with mock.patch("builtins.print"):
            main(["--config", str(config), "--set-pin", "4321"])
        self.assertTrue(self.path.exists())
        with mock.patch("builtins.print"):
            main(["--config", str(config), "--remove-pin"])
        self.assertFalse(self.path.exists())


@mock.patch.object(access, "TRUSTED_NETS", ONLY_TAILSCALE)  # 127.0.0.1 joue le rôle du Wi-Fi maison
class AccessServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = Path(self.tmp.name) / "devices.json"
        access.set_pin(config.with_name("access.json"), "123456")
        self.app = make_server("127.0.0.1", 0, config)
        threading.Thread(target=self.app.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.app.server_port}"

    def tearDown(self):
        self.app.shutdown(); self.app.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def test_api_locked_until_login(self):
        code, data, _ = self.req("GET", "/api/devices")
        self.assertEqual((code, data["auth_required"]), (401, True))
        self.assertEqual(self.req("PUT", "/api/settings", {"pricing": {}})[0], 401)
        self.assertEqual(self.req("GET", "/api/info")[0], 200)  # le widget en a besoin pour trouver le Pi
        self.assertEqual(self.req("GET", "/api/auth")[1], {"enabled": True, "ok": False})

        self.assertEqual(self.req("POST", "/api/login", {"pin": "000000"})[0], 401)
        code, data, headers = self.req("POST", "/api/login", {"pin": "123456"})
        self.assertEqual(code, 200)
        token = data["token"]
        self.assertIn("HttpOnly", headers["Set-Cookie"])

        self.assertEqual(self.req("GET", "/api/devices", headers={"X-Shelly-Token": token})[:2], (200, []))
        self.assertEqual(self.req("GET", "/api/devices", headers={"Cookie": f"shelly_auth={token}"})[:2], (200, []))
        self.assertTrue(self.req("GET", "/api/auth", headers={"X-Shelly-Token": token})[1]["ok"])

    def test_static_pages_stay_reachable(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertEqual(resp.status, 200)

    def test_cross_site_post_refused(self):
        token = self.req("POST", "/api/login", {"pin": "123456"})[1]["token"]
        code, _, _ = self.req("POST", "/api/devices", {"host": "1.2.3.4"},
                              headers={"X-Shelly-Token": token, "Origin": "http://evil.example"})
        self.assertEqual(code, 403)


if __name__ == "__main__":
    unittest.main()
