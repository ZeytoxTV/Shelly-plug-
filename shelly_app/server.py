"""Serveur web local pour piloter des prises Shelly.

Lancement :  python3 -m shelly_app  [--port 8080] [--bind 0.0.0.0] [--config devices.json]
"""

import argparse
import json
import mimetypes
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .automation import DEFAULT_SCHEDULE, Automation, next_event, normalize_schedule
from .history import History
from .notify import Notifier, default_notify, normalize_notify
from .pricing import DEFAULT_PRICING, normalize_pricing, price_function
from .shelly import CloudClient, HybridClient, ShellyAuthError, ShellyClient, ShellyError

STATIC_DIR = Path(__file__).parent / "static"
# Change à chaque démarrage : la page se recharge d'elle-même après une mise à jour.
APP_VERSION = str(int(time.time()))
PUBLIC_FIELDS = ("id", "name", "host", "model", "gen", "mode", "device_id", "local_host")


class DeviceStore:
    """Liste des appareils, persistée dans un fichier JSON."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.devices = []
        if self.path.exists():
            self.devices = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.devices, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def public(self):
        with self.lock:
            return [
                {k: d.get(k) for k in PUBLIC_FIELDS} for d in self.devices
            ]

    def all(self):
        with self.lock:
            return [dict(d) for d in self.devices]

    def get(self, device_id):
        with self.lock:
            return next((dict(d) for d in self.devices if d["id"] == device_id), None)

    def add(self, device):
        with self.lock:
            device["id"] = uuid.uuid4().hex[:8]
            self.devices.append(device)
            self._save()
            return device

    def update(self, device_id, **fields):
        with self.lock:
            for d in self.devices:
                if d["id"] == device_id:
                    d.update(fields)
                    self._save()
                    return d
        return None

    def remove(self, device_id):
        with self.lock:
            before = len(self.devices)
            self.devices = [d for d in self.devices if d["id"] != device_id]
            if len(self.devices) != before:
                self._save()
                return True
            return False


class Settings:
    """Réglages généraux (tarifs), dans settings.json à côté de devices.json."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {"pricing": DEFAULT_PRICING}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        try:  # migre l'ancien format (tarif unique / HP-HC)
            self.data["pricing"] = normalize_pricing(self.data["pricing"])
        except ValueError:
            self.data["pricing"] = DEFAULT_PRICING
        if "notify" not in self.data:
            # Sujet ntfy tiré au hasard une fois pour toutes
            self.data["notify"] = default_notify()
            self._save()
        else:
            try:
                self.data["notify"] = normalize_notify(self.data["notify"], default_notify())
            except ValueError:
                self.data["notify"] = default_notify()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def notify(self):
        with self.lock:
            return json.loads(json.dumps(self.data["notify"]))

    def set_notify(self, notify):
        with self.lock:
            self.data["notify"] = notify
            self._save()

    @property
    def pricing(self):
        with self.lock:
            return json.loads(json.dumps(self.data["pricing"]))

    def set_pricing(self, pricing):
        with self.lock:
            self.data["pricing"] = pricing
            self._save()


def client_for(device):
    if device.get("mode") == "cloud":
        cloud = CloudClient(device["server"], device["auth_key"], device["device_id"])
        if device.get("local_host"):
            return HybridClient(cloud, device["local_host"], device["id"])
        return cloud
    return ShellyClient(
        device["host"], device.get("username"), device.get("password"), device.get("gen")
    )


class Handler(BaseHTTPRequestHandler):
    store: DeviceStore = None
    history: History = None
    settings: Settings = None
    automation: Automation = None
    server_version = "ShellyApp/1.0"

    def log_message(self, fmt, *args):
        pass

    # --- Helpers ---------------------------------------------------------

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-App-Version", APP_VERSION)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _parts(self):
        return [p for p in self.path.split("?", 1)[0].split("/") if p]

    def _device_call(self, device, fn):
        try:
            result = fn(client_for(device))
            self.automation.observe(device["id"], result)
            result["automation"] = self.automation.state(device["id"])
            return self._json(200, result)
        except ShellyAuthError as e:
            return self._json(401, {"error": str(e)})
        except ShellyError as e:
            return self._json(502, {"error": str(e)})

    # --- Routes ----------------------------------------------------------

    def do_GET(self):
        parts = self._parts()
        if parts == ["api", "settings"]:
            return self._json(200, {"pricing": self.settings.pricing, "notify": self.settings.notify})
        if parts[:2] == ["api", "devices"]:
            if len(parts) == 2:
                return self._json(200, self.store.public())
            if len(parts) == 4 and parts[3] == "status":
                device = self.store.get(parts[2])
                if not device:
                    return self._json(404, {"error": "Appareil inconnu"})
                try:
                    status = self.automation.get_status(device)
                except ShellyAuthError as e:
                    return self._json(401, {"error": str(e)})
                except ShellyError as e:
                    return self._json(502, {"error": str(e)})
                status["automation"] = self.automation.state(device["id"])
                return self._json(200, status)
            if len(parts) == 4 and parts[3] == "widget":
                device = self.store.get(parts[2])
                if not device:
                    return self._json(404, {"error": "Appareil inconnu"})
                return self._widget(device)
            if len(parts) == 4 and parts[3] == "cost":
                device = self.store.get(parts[2])
                if not device:
                    return self._json(404, {"error": "Appareil inconnu"})
                return self._json(200, self._cost(device["id"]))
            if len(parts) == 4 and parts[3] in ("history", "schedule"):
                device = self.store.get(parts[2])
                if not device:
                    return self._json(404, {"error": "Appareil inconnu"})
                if parts[3] == "schedule":
                    return self._json(200, self._schedule_payload(device))
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                rng = query.get("range", "24h")
                price_at = price_function(self.settings.pricing)
                if rng == "24h":
                    wh, eur = self.history.energy_between(device["id"], time.time() - 86400, price_at=price_at)
                    return self._json(200, {
                        "range": rng,
                        "power": self.history.power_series(device["id"]),
                        "energy": {"wh": wh, "eur": eur},
                    })
                if rng in ("7d", "30d"):
                    days = self.history.daily_energy(device["id"], int(rng[:-1]), price_at=price_at)
                    return self._json(200, {"range": rng, "daily": days})
                return self._json(400, {"error": "range doit être 24h, 7d ou 30d"})
            return self._json(404, {"error": "Introuvable"})
        return self._static(parts)

    def do_POST(self):
        parts = self._parts()
        body = self._body()
        if body is None:
            return self._json(400, {"error": "JSON invalide"})

        if parts == ["api", "devices"]:
            return self._add_device(body)

        if parts == ["api", "notify", "test"]:
            notifier = self.automation.notifier
            try:
                sent = notifier.notify("test", "Shelly App : ça marche ! 🎉",
                                       "Tu recevras ici les alertes de tes prises.", priority=3,
                                       tags=["electric_plug"], wait=True)
            except RuntimeError as e:
                return self._json(502, {"error": str(e)})
            if not sent:
                return self._json(400, {"error": "Active d'abord les notifications et enregistre"})
            return self._json(200, {"ok": True})

        if len(parts) == 4 and parts[:2] == ["api", "devices"] and parts[3] == "switch":
            device = self.store.get(parts[2])
            if not device:
                return self._json(404, {"error": "Appareil inconnu"})
            action = body.get("action")
            if action not in ("on", "off", "toggle"):
                return self._json(400, {"error": "action doit être on, off ou toggle"})
            timer = body.get("timer")
            if timer is not None:
                try:
                    timer = int(timer)
                except (TypeError, ValueError):
                    return self._json(400, {"error": "timer doit être un nombre de secondes"})
                if timer <= 0:
                    timer = None
            return self._device_call(device, lambda c: c.switch(action, timer))

        return self._json(404, {"error": "Introuvable"})

    def do_PUT(self):
        parts = self._parts()
        body = self._body()
        if body is None:
            return self._json(400, {"error": "JSON invalide"})
        if parts == ["api", "settings"]:
            try:
                pricing = normalize_pricing(body["pricing"]) if "pricing" in body else None
                notify = normalize_notify(body["notify"], self.settings.notify) if "notify" in body else None
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            if pricing is None and notify is None:
                return self._json(400, {"error": "Rien à enregistrer"})
            if pricing is not None:
                self.settings.set_pricing(pricing)
            if notify is not None:
                self.settings.set_notify(notify)
            return self._json(200, {"pricing": self.settings.pricing, "notify": self.settings.notify})
        if len(parts) == 4 and parts[:2] == ["api", "devices"] and parts[3] == "schedule":
            device = self.store.get(parts[2])
            if not device:
                return self._json(404, {"error": "Appareil inconnu"})
            try:
                schedule = normalize_schedule(body)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            device = self.store.update(device["id"], schedule=schedule)
            return self._json(200, self._schedule_payload(device))
        return self._json(404, {"error": "Introuvable"})

    def _widget(self, device):
        """Résumé compact pour le widget Android : un seul appel par rafraîchissement."""
        payload = {"id": device["id"], "name": device.get("name"), "online": True, "error": None}
        try:
            status = self.automation.get_status(device)
            payload.update(on=status.get("on"), power=status.get("power"), stale=bool(status.get("stale")))
        except ShellyError as e:
            payload.update(online=False, error=str(e), on=None, power=None)
        today = self.history.daily_energy(device["id"], 1)[0]["wh"]
        spark = self.history.power_series(device["id"], hours=24, bucket_s=1800)
        payload.update(
            today_wh=today,
            spark=[p["avg"] for p in spark],
            pending=self.automation.state(device["id"]),
            next=next_event(device.get("schedule") or DEFAULT_SCHEDULE),
        )
        return self._json(200, payload)

    def _cost(self, device_id, now=None):
        """Énergie et coût d'aujourd'hui et du mois en cours."""
        price_at = price_function(self.settings.pricing)
        now = now or time.time()
        dt = datetime.fromtimestamp(now)
        periods = {
            "today": dt.replace(hour=0, minute=0, second=0, microsecond=0),
            "month": dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        }
        out = {"price_now": price_at(now)}
        for key, start in periods.items():
            wh, eur = self.history.energy_between(device_id, start.timestamp(), price_at=price_at)
            out[key] = {"wh": wh, "eur": eur}
        return out

    def _schedule_payload(self, device):
        return {
            "schedule": device.get("schedule") or DEFAULT_SCHEDULE,
            "pending": self.automation.state(device["id"]),
            "events": self.history.events(device["id"]),
        }

    def do_DELETE(self):
        parts = self._parts()
        if len(parts) == 3 and parts[:2] == ["api", "devices"]:
            if self.store.remove(parts[2]):
                self.automation.forget(parts[2])
                self.history.forget(parts[2])
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "Appareil inconnu"})
        return self._json(404, {"error": "Introuvable"})

    def _add_device(self, body):
        cloud = body.get("mode") == "cloud"
        if cloud:
            fields = {k: (body.get(k) or "").strip() for k in ("server", "auth_key", "device_id")}
            if not all(fields.values()):
                return self._json(400, {"error": "Serveur, clé cloud et identifiant requis"})
            if "xx" in fields["server"].lower():
                return self._json(400, {"error": "Remplace « XX » par le numéro de ton serveur (affiché avec la clé dans l'appli Shelly)"})
            client = CloudClient(**fields)
            device = {"mode": "cloud", **fields, "device_id": client.device_id, "host": "Cloud Shelly"}
        else:
            host = (body.get("host") or "").strip()
            if not host:
                return self._json(400, {"error": "Adresse IP requise"})
            client = ShellyClient(host, body.get("username"), body.get("password"))
            device = {
                "mode": "local",
                "host": host,
                "username": body.get("username") or None,
                "password": body.get("password") or None,
            }
        try:
            info = client.info()
        except ShellyAuthError as e:
            return self._json(401, {"error": str(e)})
        except ShellyError as e:
            return self._json(502, {"error": str(e)})
        device.update(
            name=(body.get("name") or "").strip() or info.get("name") or info.get("model") or device["host"],
            gen=info["gen"],
            model=info.get("model"),
        )
        device = self.store.add(device)
        return self._json(201, {k: device.get(k) for k in PUBLIC_FIELDS})

    def _static(self, parts):
        rel = "/".join(parts) or "index.html"
        path = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            return self._json(404, {"error": "Introuvable"})
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        elif path.suffix == ".apk":
            ctype = "application/vnd.android.package-archive"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def make_server(bind, port, config, history_db=None):
    store = DeviceStore(config)
    history = History(history_db or Path(config).with_name("history.db"))
    settings = Settings(Path(config).with_name("settings.json"))
    automation = Automation(store, history, client_for, notifier=Notifier(lambda: settings.notify))
    handler = type(
        "BoundHandler",
        (Handler,),
        {"store": store, "history": history, "automation": automation, "settings": settings},
    )
    server = ThreadingHTTPServer((bind, port), handler)
    server.automation = automation
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pilote tes prises Shelly depuis le navigateur.")
    parser.add_argument("--bind", default="0.0.0.0", help="adresse d'écoute (défaut : 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="port (défaut : 8080)")
    parser.add_argument("--config", default="devices.json", help="fichier des appareils")
    args = parser.parse_args(argv)

    server = make_server(args.bind, args.port, args.config)
    server.automation.start()
    print(f"Shelly App disponible sur http://localhost:{args.port}  (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
