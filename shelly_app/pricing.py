"""Prix de l'électricité : tarif unique ou heures pleines / heures creuses."""

import re
from datetime import datetime

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Valeurs par défaut indicatives : à remplacer par celles de ta facture.
DEFAULT_PRICING = {
    "base": 0.2016,
    "hc_enabled": False,
    "hp": 0.2146,
    "hc": 0.1696,
    "hc_ranges": [["22:00", "06:00"]],
}


def _minutes(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def normalize_pricing(data):
    """Valide les tarifs envoyés par l'interface. Lève ValueError si invalide."""
    if not isinstance(data, dict):
        raise ValueError("Tarifs invalides")
    out = {"hc_enabled": bool(data.get("hc_enabled", False))}
    for key in ("base", "hp", "hc"):
        try:
            value = float(data.get(key, DEFAULT_PRICING[key]))
        except (TypeError, ValueError):
            raise ValueError("Prix invalide") from None
        if not 0 <= value <= 5:
            raise ValueError("Le prix du kWh doit être entre 0 et 5 €")
        out[key] = round(value, 5)
    ranges = data.get("hc_ranges", DEFAULT_PRICING["hc_ranges"])
    if not isinstance(ranges, list) or len(ranges) > 4:
        raise ValueError("Plages d'heures creuses invalides")
    clean = []
    for r in ranges:
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(t, str) and TIME_RE.match(t) for t in r)):
            raise ValueError("Plage d'heures creuses invalide (format HH:MM)")
        if r[0] == r[1]:
            raise ValueError("Une plage d'heures creuses ne peut pas être vide")
        clean.append([r[0], r[1]])
    if out["hc_enabled"] and not clean:
        raise ValueError("Ajoute au moins une plage d'heures creuses")
    out["hc_ranges"] = clean
    return out


def off_peak_checker(pricing):
    """Retourne une fonction ts -> True si ts tombe en heures creuses (None si tarif unique)."""
    if not pricing.get("hc_enabled"):
        return None
    ranges = [(_minutes(a), _minutes(b)) for a, b in pricing["hc_ranges"]]

    def check(ts):
        dt = datetime.fromtimestamp(ts)
        m = dt.hour * 60 + dt.minute
        for start, end in ranges:
            if start < end and start <= m < end:
                return True
            if start > end and (m >= start or m < end):  # plage à cheval sur minuit
                return True
        return False

    return check


def cost(wh, wh_hc, pricing):
    """Coût en euros d'une consommation (Wh), dont wh_hc en heures creuses."""
    if wh is None:
        return None
    if pricing.get("hc_enabled"):
        return round(((wh - wh_hc) * pricing["hp"] + wh_hc * pricing["hc"]) / 1000, 4)
    return round(wh * pricing["base"] / 1000, 4)
