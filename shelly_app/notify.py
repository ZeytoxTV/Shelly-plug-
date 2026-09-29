"""Notifications sur le téléphone via ntfy (https://ntfy.sh).

Le serveur publie un message JSON sur un « sujet » ntfy ; l'appli ntfy du téléphone, abonnée à ce
sujet, affiche la notification. Le sujet sert de mot de passe : il est généré au hasard.
"""

import json
import re
import secrets
import threading
import urllib.error
import urllib.request

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def default_notify():
    return {
        "enabled": False,
        "server": "https://ntfy.sh",
        "topic": "shelly-" + secrets.token_hex(6),
        "token": "",
        "app_url": "",
        # PC encore allumé à une heure tardive
        "late": {"enabled": True, "time": "02:00", "threshold_w": 30},
        # Arrêt programmé reporté, puis effectué
        "postponed": {"enabled": True},
        # Prise injoignable depuis N minutes (et retour)
        "offline": {"enabled": True, "minutes": 10},
        # Conso au-dessus d'un seuil pendant N minutes
        "high": {"enabled": False, "threshold_w": 400, "minutes": 15},
        # Chaque allumage / arrêt programmé
        "schedule": {"enabled": False},
    }


def _num(value, lo, hi, label):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} invalide") from None
    if not lo <= v <= hi:
        raise ValueError(f"{label} : entre {lo:g} et {hi:g}")
    return v


def normalize_notify(data, current=None):
    """Valide les réglages envoyés par l'interface. Lève ValueError si invalide."""
    if not isinstance(data, dict):
        raise ValueError("Réglages de notification invalides")
    base = current or default_notify()
    out = {**base}
    out["enabled"] = bool(data.get("enabled", base["enabled"]))
    server = str(data.get("server", base["server"]) or "https://ntfy.sh").strip().rstrip("/")
    if not server.startswith(("http://", "https://")):
        server = "https://" + server
    out["server"] = server
    topic = str(data.get("topic", base["topic"]) or "").strip()
    if not TOPIC_RE.match(topic):
        raise ValueError("Sujet ntfy invalide (lettres, chiffres, - et _ uniquement)")
    out["topic"] = topic
    out["token"] = str(data.get("token", base.get("token", "")) or "").strip()
    app_url = str(data.get("app_url", base.get("app_url", "")) or "").strip().rstrip("/")
    out["app_url"] = app_url if app_url.startswith(("http://", "https://")) else ""

    late = {**base["late"], **(data.get("late") or {})}
    if not TIME_RE.match(str(late.get("time", ""))):
        raise ValueError("Heure de l'alerte « PC allumé tard » invalide")
    out["late"] = {"enabled": bool(late["enabled"]), "time": late["time"],
                   "threshold_w": _num(late["threshold_w"], 0, 5000, "Seuil")}
    out["postponed"] = {"enabled": bool({**base["postponed"], **(data.get("postponed") or {})}["enabled"])}
    off = {**base["offline"], **(data.get("offline") or {})}
    out["offline"] = {"enabled": bool(off["enabled"]), "minutes": int(_num(off["minutes"], 1, 1440, "Durée"))}
    high = {**base["high"], **(data.get("high") or {})}
    out["high"] = {"enabled": bool(high["enabled"]), "threshold_w": _num(high["threshold_w"], 1, 5000, "Seuil"),
                   "minutes": int(_num(high["minutes"], 1, 1440, "Durée"))}
    out["schedule"] = {"enabled": bool({**base["schedule"], **(data.get("schedule") or {})}["enabled"])}
    return out


class Notifier:
    """Envoie les notifications ; `send_fn` est remplaçable pour les tests."""

    def __init__(self, get_config, send_fn=None, background=True):
        self.get_config = get_config
        self.send_fn = send_fn or publish
        self.background = background

    def notify(self, kind, title, message, device=None, priority=3, tags=None, cut_button=False, wait=False):
        cfg = self.get_config()
        if not cfg or not cfg.get("enabled"):
            return False
        if kind != "test" and not (cfg.get(kind) or {}).get("enabled"):
            return False
        payload = {
            "topic": cfg["topic"],
            "title": title,
            "message": message,
            "priority": priority,
            "tags": tags or [],
        }
        app = cfg.get("app_url")
        if app:
            payload["click"] = app + "/"
            actions = [{"action": "view", "label": "Ouvrir", "url": app + "/", "clear": True}]
            if cut_button and device:
                # Envoyé depuis le téléphone : nécessite Tailscale actif, comme l'appli
                actions.append({
                    "action": "http", "label": "Couper la prise", "method": "POST", "clear": True,
                    "url": f"{app}/api/devices/{device['id']}/switch",
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"action": "off"}),
                })
            payload["actions"] = actions
        if wait:
            self.send_fn(cfg, payload)
            return True
        if not self.background:
            self._send_quietly(cfg, payload)
            return True
        threading.Thread(target=self._send_quietly, args=(cfg, payload), daemon=True).start()
        return True

    def _send_quietly(self, cfg, payload):
        try:
            self.send_fn(cfg, payload)
        except Exception as e:  # une notification ratée ne doit rien casser
            print(f"[ntfy] échec d'envoi : {e}", flush=True)


def publish(cfg, payload):
    """Publie un message JSON sur le serveur ntfy. Lève RuntimeError en cas d'échec."""
    req = urllib.request.Request(
        cfg["server"] + "/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if cfg.get("token"):
        req.add_header("Authorization", f"Bearer {cfg['token']}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", "")
        except ValueError:
            pass
        raise RuntimeError(f"ntfy a refusé le message ({e.code}) {detail}".strip()) from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Serveur ntfy injoignable ({getattr(e, 'reason', e)})") from e
