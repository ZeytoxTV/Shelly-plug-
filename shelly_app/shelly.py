"""Client minimal pour les prises Shelly (Gen1 et Gen2/Gen3), via l'API HTTP locale."""

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 5


class ShellyError(Exception):
    pass


class ShellyAuthError(ShellyError):
    pass


def _parse_digest_challenge(header):
    fields = dict(re.findall(r'(\w+)="?([^",]*)"?', header.split(" ", 1)[1]))
    return fields


class ShellyClient:
    def __init__(self, host, username=None, password=None, gen=None):
        host = host.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        self.base = host
        self.username = username or None
        self.password = password or None
        self.gen = gen

    # --- Transport -------------------------------------------------------

    def _open(self, path, auth_header=None):
        req = urllib.request.Request(self.base + path)
        if auth_header:
            req.add_header("Authorization", auth_header)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")

    def _digest_header(self, challenge, path):
        c = _parse_digest_challenge(challenge)
        algo = c.get("algorithm", "MD5").upper()
        h = hashlib.sha256 if algo.startswith("SHA-256") else hashlib.md5

        def H(s):
            return h(s.encode()).hexdigest()

        user = self.username or "admin"
        realm, nonce = c.get("realm", ""), c.get("nonce", "")
        cnonce = os.urandom(8).hex()
        nc = "00000001"
        ha1 = H(f"{user}:{realm}:{self.password}")
        ha2 = H(f"GET:{path}")
        response = H(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        return (
            f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{path}", algorithm={algo}, response="{response}", '
            f'qop=auth, nc={nc}, cnonce="{cnonce}"'
        )

    def get(self, path, params=None):
        if params:
            path += "?" + urllib.parse.urlencode(params)
        try:
            try:
                auth = None
                if self.password and self.gen == 1:
                    token = base64.b64encode(
                        f"{self.username or 'admin'}:{self.password}".encode()
                    ).decode()
                    auth = f"Basic {token}"
                return self._open(path, auth)
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                challenge = e.headers.get("WWW-Authenticate", "")
                if not self.password:
                    raise ShellyAuthError("Mot de passe requis pour cet appareil")
                if challenge.lower().startswith("digest"):
                    auth = self._digest_header(challenge, path)
                else:
                    token = base64.b64encode(
                        f"{self.username or 'admin'}:{self.password}".encode()
                    ).decode()
                    auth = f"Basic {token}"
                try:
                    return self._open(path, auth)
                except urllib.error.HTTPError as e2:
                    if e2.code == 401:
                        raise ShellyAuthError("Identifiants refusés par l'appareil")
                    raise
        except urllib.error.HTTPError as e:
            raise ShellyError(f"Erreur HTTP {e.code} de l'appareil") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "reason", e)
            raise ShellyError(f"Appareil injoignable ({reason})") from e

    # --- API haut niveau ------------------------------------------------

    def info(self):
        """Détecte la génération et retourne les infos de base."""
        data = self.get("/shelly")
        if data.get("gen", 1) >= 2:
            self.gen = data["gen"]
            return {
                "gen": data["gen"],
                "model": data.get("model") or data.get("app"),
                "name": data.get("name"),
                "mac": data.get("mac"),
            }
        self.gen = 1
        if data.get("auth") and not self.password:
            raise ShellyAuthError("Mot de passe requis pour cet appareil")
        name = None
        try:
            name = self.get("/settings").get("name")
        except ShellyAuthError:
            raise
        except ShellyError:
            pass
        return {"gen": 1, "model": data.get("type"), "name": name, "mac": data.get("mac")}

    def _ensure_gen(self):
        if self.gen is None:
            self.info()

    def status(self):
        self._ensure_gen()
        if self.gen >= 2:
            s = self.get("/rpc/Switch.GetStatus", {"id": 0})
            return {
                "on": bool(s.get("output")),
                "power": s.get("apower"),
                "voltage": s.get("voltage"),
                "current": s.get("current"),
                "energy_wh": (s.get("aenergy") or {}).get("total"),
                "temperature": (s.get("temperature") or {}).get("tC"),
                "timer_remaining": _remaining(s),
            }
        s = self.get("/status")
        relay = (s.get("relays") or [{}])[0]
        meter = (s.get("meters") or [{}])[0]
        total = meter.get("total")
        return {
            "on": bool(relay.get("ison")),
            "power": meter.get("power"),
            "voltage": None,
            "current": None,
            # Gen1 : "total" est en watt-minutes
            "energy_wh": total / 60 if total is not None else None,
            "temperature": s.get("temperature"),
            "timer_remaining": relay.get("timer_remaining") if relay.get("has_timer") else None,
        }

    def switch(self, action, timer=None):
        if action not in ("on", "off", "toggle"):
            raise ValueError("action invalide")
        self._ensure_gen()
        if self.gen >= 2:
            if action == "toggle":
                on = not self.status()["on"]
            else:
                on = action == "on"
            params = {"id": 0, "on": "true" if on else "false"}
            if timer:
                params["toggle_after"] = timer
            self.get("/rpc/Switch.Set", params)
        else:
            params = {"turn": action}
            if timer:
                params["timer"] = timer
            self.get("/relay/0", params)
        return self.status()


def _remaining(s):
    if s.get("timer_started_at") is None or s.get("timer_duration") is None:
        return None
    return max(0, round(s["timer_started_at"] + s["timer_duration"] - time.time()))
