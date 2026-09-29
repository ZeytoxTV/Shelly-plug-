"""Historique de consommation (SQLite) et agrégats pour les graphiques."""

import sqlite3
import threading
import time
from datetime import datetime, timedelta

RETENTION_DAYS = 90
MAX_GAP_S = 300  # au-delà, on n'intègre pas la puissance entre deux mesures


class History:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    device_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    power REAL,
                    energy_wh REAL,
                    is_on INTEGER
                );
                CREATE INDEX IF NOT EXISTS samples_dev_ts ON samples(device_id, ts);
                CREATE TABLE IF NOT EXISTS events (
                    device_id TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_dev_ts ON events(device_id, ts);
                """
            )

    def add_sample(self, device_id, status, ts=None):
        ts = int(ts or time.time())
        with self.lock:
            self.db.execute(
                "INSERT INTO samples VALUES (?, ?, ?, ?, ?)",
                (device_id, ts, status.get("power"), status.get("energy_wh"), int(bool(status.get("on")))),
            )
            self.db.commit()

    def add_event(self, device_id, message, ts=None):
        with self.lock:
            self.db.execute(
                "INSERT INTO events VALUES (?, ?, ?)", (device_id, int(ts or time.time()), message)
            )
            self.db.commit()

    def events(self, device_id, limit=15):
        with self.lock:
            rows = self.db.execute(
                "SELECT ts, message FROM events WHERE device_id = ? ORDER BY ts DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
        return [{"ts": ts, "message": m} for ts, m in rows]

    def purge(self, now=None):
        limit = int((now or time.time()) - RETENTION_DAYS * 86400)
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE ts < ?", (limit,))
            self.db.execute("DELETE FROM events WHERE ts < ?", (limit,))
            self.db.commit()

    def forget(self, device_id):
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE device_id = ?", (device_id,))
            self.db.execute("DELETE FROM events WHERE device_id = ?", (device_id,))
            self.db.commit()

    def _rows(self, device_id, since):
        with self.lock:
            return self.db.execute(
                "SELECT ts, power, energy_wh FROM samples WHERE device_id = ? AND ts >= ? ORDER BY ts",
                (device_id, since),
            ).fetchall()

    def power_series(self, device_id, hours=24, bucket_s=600, now=None):
        """Puissance moyenne et max par tranche (défaut : 10 min sur 24 h)."""
        now = int(now or time.time())
        start = (now - hours * 3600) // bucket_s * bucket_s
        buckets = {}
        for ts, power, _ in self._rows(device_id, start):
            if power is None:
                continue
            b = buckets.setdefault((ts - start) // bucket_s, [0.0, 0, 0.0])
            b[0] += power
            b[1] += 1
            b[2] = max(b[2], power)
        points = []
        for i in range((now - start) // bucket_s + 1):
            b = buckets.get(i)
            points.append(
                {
                    "ts": start + i * bucket_s,
                    "avg": round(b[0] / b[1], 1) if b else None,
                    "max": round(b[2], 1) if b else None,
                }
            )
        return points

    def _deltas(self, device_id, since, until=None):
        """Énergie consommée entre deux mesures successives : (ts, Wh)."""
        prev = None
        for ts, power, energy in self._rows(device_id, since - MAX_GAP_S):
            if until is not None and ts > until:
                break
            if prev is not None and ts >= since:
                pts, ppower, penergy = prev
                delta = None
                if energy is not None and penergy is not None and energy >= penergy:
                    delta = energy - penergy
                elif ppower is not None and ts - pts <= MAX_GAP_S:
                    delta = ppower * (ts - pts) / 3600
                if delta is not None:
                    yield ts, delta
            prev = (ts, power, energy)

    def daily_energy(self, device_id, days=7, now=None, price_at=None):
        """Énergie (Wh) et coût (€, si price_at est fourni) par jour, en heure locale."""
        now = now or time.time()
        today = datetime.fromtimestamp(now).date()
        first = today - timedelta(days=days - 1)
        since = int(datetime.combine(first, datetime.min.time()).timestamp())
        totals = {first + timedelta(days=i): [0.0, 0.0] for i in range(days)}
        has_data = set()
        for ts, delta in self._deltas(device_id, since):
            day = datetime.fromtimestamp(ts).date()
            if day in totals:
                totals[day][0] += delta
                if price_at:
                    totals[day][1] += delta * price_at(ts) / 1000
                has_data.add(day)
        return [
            {
                "date": d.isoformat(),
                "wh": round(wh, 1) if d in has_data else None,
                "eur": round(eur, 4) if d in has_data and price_at else None,
            }
            for d, (wh, eur) in totals.items()
        ]

    def energy_between(self, device_id, since, until=None, price_at=None):
        """(Wh, €) consommés sur une période ; (None, None) sans mesure."""
        total = eur = 0.0
        seen = False
        for ts, delta in self._deltas(device_id, int(since), until):
            seen = True
            total += delta
            if price_at:
                eur += delta * price_at(ts) / 1000
        if not seen:
            return None, None
        return round(total, 1), (round(eur, 4) if price_at else None)
