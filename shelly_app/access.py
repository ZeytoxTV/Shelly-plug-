"""Code d'accès (PIN) du site.

Sans PIN enregistré, tout est ouvert (comme avant). Avec un PIN, les appels /api/… venant du
Wi-Fi de la maison doivent présenter un jeton, obtenu une fois en tapant le code. Les appels
via Tailscale (100.64.0.0/10) et depuis le Pi lui-même passent sans code.

Le PIN est enregistré dans access.json (à côté de devices.json), haché, jamais en clair :
    python3 -m shelly_app --set-pin 123456
    python3 -m shelly_app --remove-pin
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from pathlib import Path

TRUSTED_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),  # Tailscale
    ipaddress.ip_network("127.0.0.0/8"),
)
COOKIE = "shelly_auth"
HEADER = "X-Shelly-Token"
MAX_FAILS = 5
LOCK_S = 60
_ITER = 200_000


def _hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITER).hex()


def valid_pin(pin):
    return isinstance(pin, str) and pin.isdigit() and 4 <= len(pin) <= 12


def is_trusted(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in TRUSTED_NETS)


def set_pin(path, pin):
    """Enregistre un nouveau PIN ; change aussi le jeton (les appareils déjà connectés doivent retaper le code)."""
    if not valid_pin(pin):
        raise ValueError("Le code doit faire 4 à 12 chiffres")
    salt = secrets.token_hex(16)
    data = {"salt": salt, "pin_hash": _hash(pin, salt), "token": secrets.token_urlsafe(32)}
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def remove_pin(path):
    Path(path).unlink(missing_ok=True)


class Access:
    """Vérifie les requêtes. Relit access.json s'il change (PIN modifié pendant que le serveur tourne)."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._mtime = None
        self._data = None
        self._fails = {}  # ip -> (nombre d'échecs, bloqué jusqu'à)

    def _load(self):
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._mtime, self._data = None, None
            return None
        if mtime != self._mtime:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = data if {"salt", "pin_hash", "token"} <= set(data) else None
            except (OSError, ValueError):
                self._data = None
            self._mtime = mtime
        return self._data

    @property
    def enabled(self):
        with self.lock:
            return self._load() is not None

    def allowed(self, ip, token):
        with self.lock:
            data = self._load()
        if data is None or is_trusted(ip):
            return True
        return bool(token) and hmac.compare_digest(str(token), data["token"])

    def login(self, ip, pin):
        """Renvoie (jeton, None) si le code est bon, sinon (None, message d'erreur)."""
        now = time.monotonic()
        with self.lock:
            data = self._load()
            if data is None:
                return "", None
            fails, until = self._fails.get(ip, (0, 0))
            if until > now:
                return None, f"Trop d'essais : réessaie dans {int(until - now) + 1} s"
            if isinstance(pin, str) and hmac.compare_digest(_hash(pin, data["salt"]), data["pin_hash"]):
                self._fails.pop(ip, None)
                return data["token"], None
            fails += 1
            if fails >= MAX_FAILS:
                self._fails[ip] = (0, now + LOCK_S)
                return None, f"Code incorrect. Trop d'essais : attends {LOCK_S} s"
            self._fails[ip] = (fails, 0)
            return None, "Code incorrect"
