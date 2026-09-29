"""Programmation hebdomadaire, protection anti-coupure et relevé de consommation.

Tourne en tâche de fond dans le serveur :
- relève la consommation de chaque prise une fois par minute (pour les graphiques) ;
- applique les horaires programmés, jour par jour ;
- ne coupe pas la prise si l'appareil consomme encore (PC allumé, partie en cours…) :
  l'arrêt est reporté jusqu'à ce que la consommation reste sous le seuil
  pendant quelques minutes.
"""

import re
import threading
import time
from datetime import datetime

from .shelly import ShellyClient, ShellyError, same_device

SAMPLE_EVERY_S = 60
# Âge max d'un état partagé entre la page, le widget et les relevés : le cloud Shelly
# n'accepte qu'une requête par seconde, une prise locale répond vite.
CACHE_S = {"cloud": 8, "local": 2}
# En cas d'échec (cloud saturé, coupure Wi-Fi brève), on affiche la dernière valeur connue
STALE_MAX_S = 300
# Prise ajoutée via le cloud : on recherche son adresse locale au plus toutes les 10 min
DISCOVERY_EVERY_S = 600
TICK_S = 15
CATCH_UP_MIN = 5  # une règle ratée de peu (redémarrage, requête lente) est encore appliquée
DAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

DEFAULT_SCHEDULE = {
    "enabled": True,
    "rules": [],
    "protect": {"enabled": True, "threshold_w": 15, "idle_minutes": 5},
}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def normalize_schedule(data):
    """Valide une programmation envoyée par l'interface. Lève ValueError si invalide."""
    if not isinstance(data, dict):
        raise ValueError("Programmation invalide")
    rules = data.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("Liste d'horaires invalide")
    clean = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise ValueError("Horaire invalide")
        t = str(r.get("time", ""))
        if not TIME_RE.match(t):
            raise ValueError(f"Heure invalide : {t!r}")
        days = r.get("days")
        if not isinstance(days, list) or not days or not all(d in range(7) for d in days):
            raise ValueError("Choisis au moins un jour")
        if r.get("action") not in ("on", "off"):
            raise ValueError("Action invalide")
        clean.append(
            {
                "id": str(r.get("id") or f"r{i}")[:32],
                "time": t,
                "days": sorted(set(days)),
                "action": r["action"],
            }
        )
    p = data.get("protect") or {}
    try:
        threshold = float(p.get("threshold_w", 15))
        idle = int(p.get("idle_minutes", 5))
    except (TypeError, ValueError):
        raise ValueError("Seuil ou durée invalide") from None
    if not 0 <= threshold <= 5000 or not 1 <= idle <= 240:
        raise ValueError("Seuil (0–5000 W) ou durée (1–240 min) hors limites")
    return {
        "enabled": bool(data.get("enabled", True)),
        "rules": clean,
        "protect": {"enabled": bool(p.get("enabled", True)), "threshold_w": threshold, "idle_minutes": idle},
    }


class Automation:
    def __init__(self, store, history, client_for, clock=time.time, notifier=None):
        self.store = store
        self.history = history
        self.client_for = client_for
        self.clock = clock
        self.lock = threading.Lock()
        self.latest = {}  # device_id -> (ts, status)
        self.last_sample = {}  # device_id -> ts
        self.fired = set()  # clés "rule:date:heure" déjà appliquées
        self.pending = {}  # device_id -> {"since", "low_since", "rule_time"}
        self.fetch_locks = {}  # device_id -> Lock : une seule lecture à la fois par prise
        self.discovery_at = {}  # device_id -> dernier essai de connexion locale
        self.notifier = notifier
        self.last_ok = {}  # device_id -> dernière lecture réussie
        self.offline_alerted = set()
        self.late_alerted = set()  # "device:date"
        self.high_since = {}  # device_id -> début de la conso élevée
        self.high_alerted = set()
        self._stop = threading.Event()

    # --- Relevés -----------------------------------------------------------

    def observe(self, device_id, status):
        """Mémorise un état complet ; en garde une mesure par minute dans l'historique."""
        if status.get("stale"):
            return
        if status.get("partial"):
            # Réponse du cloud après une commande : on met juste à jour marche/arrêt
            with self.lock:
                cached = self.latest.get(device_id)
                if cached:
                    self.latest[device_id] = (cached[0], {**cached[1], "on": status.get("on")})
            return
        now = self.clock()
        with self.lock:
            self.latest[device_id] = (now, status)
            due = now - self.last_sample.get(device_id, 0) >= SAMPLE_EVERY_S - 1
            if due:
                self.last_sample[device_id] = now
        if due:
            self.history.add_sample(device_id, status, now)

    def get_status(self, device, max_age=None, allow_stale=True):
        """État de la prise, partagé entre tous les demandeurs.

        Une seule lecture à la fois par prise ; un état assez récent est réutilisé. Si la prise
        (ou le cloud) ne répond pas, renvoie le dernier état connu marqué "stale" plutôt qu'une
        erreur — sauf pour les décisions automatiques (allow_stale=False).
        """
        dev_id = device["id"]
        if max_age is None:
            # Prise cloud lue en local : même fraîcheur qu'une prise locale
            mode = "local" if device.get("local_host") else (device.get("mode") or "local")
            max_age = CACHE_S.get(mode, 2)
        with self.lock:
            fetch_lock = self.fetch_locks.setdefault(dev_id, threading.Lock())
        with fetch_lock:
            with self.lock:
                cached = self.latest.get(dev_id)
            if cached and self.clock() - cached[0] <= max_age:
                return dict(cached[1])
            try:
                status = self.client_for(device).status()
            except ShellyError:
                age = self.clock() - cached[0] if cached else None
                if allow_stale and cached and age <= STALE_MAX_S:
                    return {**cached[1], "stale": True, "stale_age": int(age)}
                raise
            self.observe(dev_id, status)
            self.last_ok[dev_id] = self.clock()
            self._maybe_discover_local(device, status)
            return dict(status)

    def _maybe_discover_local(self, device, status):
        """Prise cloud : si le cloud donne une IP locale, vérifie que c'est bien elle et l'utilise."""
        ip = status.get("local_ip")
        if device.get("mode") != "cloud" or not ip or status.get("via") == "local":
            return
        if ip == device.get("local_host") or not hasattr(self.store, "update"):
            return
        now = self.clock()
        if now - self.discovery_at.get(device["id"], 0) < DISCOVERY_EVERY_S:
            return
        self.discovery_at[device["id"]] = now

        def probe():
            try:
                info = ShellyClient(ip, timeout=2.5).get("/shelly")
            except ShellyError:
                return
            if same_device(info.get("mac"), device.get("device_id")):
                self.store.update(device["id"], local_host=ip)
                self.history.add_event(device["id"], f"Connexion locale activée ({ip}) : le cloud sert de secours")

        threading.Thread(target=probe, daemon=True).start()

    def _fresh_status(self, device, max_age=0):
        return self.get_status(device, max_age=max_age, allow_stale=False)

    # --- Boucle -------------------------------------------------------------

    def tick(self):
        now = self.clock()
        dt = datetime.fromtimestamp(now)
        for device in self.store.all():
            try:
                self._tick_device(device, now, dt)
            except ShellyError:
                pass  # prise injoignable : on réessaiera au prochain passage
            self._check_alerts(device, now, dt)
        if int(now) % 3600 < TICK_S:
            self.history.purge(now)

    def _notify(self, kind, title, message, device=None, **kw):
        if self.notifier:
            self.notifier.notify(kind, title, message, device=device, **kw)

    def _notify_cfg(self):
        return (self.notifier.get_config() if self.notifier else None) or {}

    def _check_alerts(self, device, now, dt):
        """Alertes ntfy : prise injoignable, PC allumé tard, conso élevée."""
        cfg = self._notify_cfg()
        if not cfg.get("enabled"):
            return
        dev_id, name = device["id"], device.get("name") or "Prise"
        self.last_ok.setdefault(dev_id, now)  # pas de fausse alerte au démarrage

        off = cfg.get("offline") or {}
        silent = now - self.last_ok[dev_id]
        if off.get("enabled") and silent >= off["minutes"] * 60 and dev_id not in self.offline_alerted:
            self.offline_alerted.add(dev_id)
            self._notify("offline", f"{name} ne répond plus",
                         f"Aucune réponse de la prise depuis {int(silent // 60)} min (ni en local, ni via le cloud).",
                         device=device, priority=4, tags=["warning"])
        elif dev_id in self.offline_alerted and silent < 120:
            self.offline_alerted.discard(dev_id)
            self._notify("offline", f"{name} répond de nouveau", "La prise est de nouveau joignable.",
                         device=device, priority=2, tags=["white_check_mark"])

        with self.lock:
            cached = self.latest.get(dev_id)
        status = cached[1] if cached and now - cached[0] <= 2 * SAMPLE_EVERY_S else None
        power = (status or {}).get("power") or 0
        is_on = bool((status or {}).get("on"))

        late = cfg.get("late") or {}
        if late.get("enabled") and status is not None:
            h, m = map(int, late["time"].split(":"))
            key = f"{dev_id}:{dt.date()}"
            if 0 <= dt.hour * 60 + dt.minute - (h * 60 + m) < CATCH_UP_MIN and key not in self.late_alerted:
                self.late_alerted.add(key)
                if is_on and power >= late["threshold_w"]:
                    self._notify("late", f"{name} encore allumé à {late['time']}",
                                 f"L'appareil consomme {power:.0f} W. Tu joues encore ou il est resté allumé ?",
                                 device=device, priority=4, tags=["zzz"], cut_button=True)
            if len(self.late_alerted) > 1000:
                self.late_alerted = {k for k in self.late_alerted if str(dt.date()) in k}

        high = cfg.get("high") or {}
        if high.get("enabled") and status is not None:
            if is_on and power >= high["threshold_w"]:
                since = self.high_since.setdefault(dev_id, now)
                if now - since >= high["minutes"] * 60 and dev_id not in self.high_alerted:
                    self.high_alerted.add(dev_id)
                    self._notify("high", f"Conso élevée : {name}",
                                 f"{power:.0f} W depuis {int((now - since) // 60)} min "
                                 f"(seuil {high['threshold_w']:.0f} W).",
                                 device=device, priority=4, tags=["zap"], cut_button=True)
            else:
                self.high_since.pop(dev_id, None)
                self.high_alerted.discard(dev_id)

    def _tick_device(self, device, now, dt):
        dev_id = device["id"]
        # Relevé périodique pour les graphiques
        if now - self.last_sample.get(dev_id, 0) >= SAMPLE_EVERY_S:
            try:
                self._fresh_status(device, max_age=SAMPLE_EVERY_S / 2)
            except ShellyError:
                pass  # les horaires sont quand même tentés

        sched = device.get("schedule") or DEFAULT_SCHEDULE
        if sched.get("enabled"):
            minute_now = dt.hour * 60 + dt.minute
            for rule in sched.get("rules", []):
                if dt.weekday() not in rule["days"]:
                    continue
                h, m = map(int, rule["time"].split(":"))
                late = minute_now - (h * 60 + m)
                key = f"{dev_id}:{rule['id']}:{dt.date()}:{rule['time']}"
                if 0 <= late < CATCH_UP_MIN and key not in self.fired:
                    try:
                        self._apply_rule(device, sched, rule)
                        self.fired.add(key)
                    except ShellyError as e:
                        # Réessayé à chaque passage pendant CATCH_UP_MIN minutes
                        if late == CATCH_UP_MIN - 1 and key + ":err" not in self.fired:
                            self.fired.add(key + ":err")
                            self.history.add_event(dev_id, f"Échec de l'horaire {rule['time']} : {e}")
            if len(self.fired) > 5000:
                self.fired = {k for k in self.fired if str(dt.date()) in k}

        if dev_id in self.pending:
            self._check_pending(device, sched, now)

    def _apply_rule(self, device, sched, rule):
        dev_id = device["id"]
        client = self.client_for(device)
        if rule["action"] == "on":
            self.pending.pop(dev_id, None)
            self.observe(dev_id, client.switch("on"))
            self.history.add_event(dev_id, f"Allumage programmé ({rule['time']})")
            self._notify("schedule", f"{device.get('name') or 'Prise'} allumée",
                         f"Allumage programmé de {rule['time']}.", device=device, priority=2, tags=["electric_plug"])
            return

        protect = sched.get("protect") or {}
        if protect.get("enabled"):
            status = self._fresh_status(device)
            power = status.get("power") or 0
            if status.get("on") and power >= protect["threshold_w"]:
                self.pending[dev_id] = {"since": self.clock(), "low_since": None, "rule_time": rule["time"]}
                self.history.add_event(
                    dev_id,
                    f"Arrêt de {rule['time']} reporté : l'appareil consomme {power:.0f} W",
                )
                self._notify("postponed", f"Arrêt de {rule['time']} reporté",
                             f"{device.get('name') or 'La prise'} consomme encore {power:.0f} W : la prise sera coupée "
                             f"quand l'appareil sera au repos.",
                             device=device, priority=3, tags=["hourglass"], cut_button=True)
                return
        self.pending.pop(dev_id, None)
        self.observe(dev_id, client.switch("off"))
        self.history.add_event(dev_id, f"Arrêt programmé ({rule['time']})")
        self._notify("schedule", f"{device.get('name') or 'Prise'} éteinte",
                     f"Arrêt programmé de {rule['time']}.", device=device, priority=2, tags=["electric_plug"])

    def _check_pending(self, device, sched, now):
        dev_id = device["id"]
        p = self.pending[dev_id]
        protect = sched.get("protect") or {}
        status = self._fresh_status(device, max_age=SAMPLE_EVERY_S)
        if not status.get("on"):
            self.pending.pop(dev_id, None)
            self.history.add_event(dev_id, "Prise éteinte manuellement, arrêt reporté annulé")
            return
        if not sched.get("enabled") or not protect.get("enabled"):
            self.pending.pop(dev_id, None)
            return
        if (status.get("power") or 0) >= protect["threshold_w"]:
            p["low_since"] = None
            return
        if p["low_since"] is None:
            p["low_since"] = now
        if now - p["low_since"] >= protect["idle_minutes"] * 60:
            self.pending.pop(dev_id, None)
            self.observe(dev_id, self.client_for(device).switch("off"))
            self.history.add_event(
                dev_id,
                f"Arrêt reporté effectué : moins de {protect['threshold_w']:.0f} W "
                f"depuis {protect['idle_minutes']} min",
            )
            self._notify("postponed", "Prise coupée",
                         f"{device.get('name') or 'L’appareil'} est au repos : l'arrêt de {p['rule_time']} est fait.",
                         device=device, priority=2, tags=["white_check_mark"])

    def state(self, device_id):
        p = self.pending.get(device_id)
        if not p:
            return None
        return {"pending_off_since": int(p["since"]), "rule_time": p["rule_time"], "low_since": p["low_since"]}

    def forget(self, device_id):
        self.pending.pop(device_id, None)
        self.discovery_at.pop(device_id, None)
        with self.lock:
            self.latest.pop(device_id, None)
            self.last_sample.pop(device_id, None)

    # --- Thread ---------------------------------------------------------

    def start(self):
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:  # la boucle ne doit jamais mourir
                    print(f"[automation] erreur : {e}", flush=True)
                self._stop.wait(TICK_S)

        threading.Thread(target=loop, daemon=True, name="automation").start()

    def stop(self):
        self._stop.set()


def next_event(schedule, now=None):
    """Prochaine action programmée : {"time", "action", "day", "in_days"} ou None."""
    if not schedule or not schedule.get("enabled") or not schedule.get("rules"):
        return None
    dt = datetime.fromtimestamp(now or time.time())
    minute_now = dt.hour * 60 + dt.minute
    best = None
    for offset in range(8):
        day = (dt.weekday() + offset) % 7
        for r in schedule["rules"]:
            if day not in r["days"]:
                continue
            h, m = map(int, r["time"].split(":"))
            minutes = h * 60 + m
            if offset == 0 and minutes <= minute_now:
                continue
            key = (offset, minutes)
            if best is None or key < best[0]:
                best = (key, {"time": r["time"], "action": r["action"], "day": DAY_NAMES[day], "in_days": offset})
        if best:
            return best[1]
    return None
