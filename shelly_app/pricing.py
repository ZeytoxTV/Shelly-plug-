"""Prix de l'électricité : un prix normal, des périodes moins chères, et des changements datés.

Format enregistré (settings.json → "pricing") :

    {"tariffs": [
        {"from": null,         "price": 0.2456, "periods": [
            {"name": "Heures creuses", "price": 0.1915, "start": "00:00", "end": "06:00"}]},
        {"from": "2026-11-01", "price": 0.2615, "periods": [...]}
    ]}

- "price" : prix du kWh TTC hors périodes (tarif unique, ou heures pleines) ;
- "periods" : plages horaires à un autre prix (heures creuses, heures vertes…), éventuellement
  à cheval sur minuit ;
- "from" : date d'effet (le premier tarif s'applique depuis toujours). Chaque mesure est
  facturée au tarif en vigueur ce jour-là : un changement de prix ne réécrit pas le passé.
"""

import re
from datetime import date, datetime

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TARIFFS = 6
MAX_PERIODS = 8

# Valeur par défaut indicative : à remplacer par celle de ta facture.
DEFAULT_PRICING = {"tariffs": [{"from": None, "price": 0.2016, "periods": []}]}


def _minutes(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _price(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("Prix invalide") from None
    if not 0 <= value <= 5:
        raise ValueError("Le prix du kWh doit être entre 0 et 5 €")
    return round(value, 5)


def _from_legacy(data):
    """Ancien format (tarif unique ou HP/HC) → nouveau format."""
    if data.get("hc_enabled"):
        periods = [
            {"name": "Heures creuses", "price": data.get("hc", 0), "start": a, "end": b}
            for a, b in data.get("hc_ranges", [])
        ]
        return {"tariffs": [{"from": None, "price": data.get("hp", 0), "periods": periods}]}
    return {"tariffs": [{"from": None, "price": data.get("base", DEFAULT_PRICING["tariffs"][0]["price"]), "periods": []}]}


def normalize_pricing(data):
    """Valide les tarifs envoyés par l'interface. Lève ValueError si invalide."""
    if not isinstance(data, dict):
        raise ValueError("Tarifs invalides")
    if "tariffs" not in data:
        data = _from_legacy(data)
    tariffs = data["tariffs"]
    if not isinstance(tariffs, list) or not 1 <= len(tariffs) <= MAX_TARIFFS:
        raise ValueError("Liste de tarifs invalide")
    clean = []
    for i, t in enumerate(tariffs):
        if not isinstance(t, dict):
            raise ValueError("Tarif invalide")
        start = t.get("from")
        if i == 0:
            start = None  # le premier tarif s'applique depuis toujours
        elif not (isinstance(start, str) and DATE_RE.match(start)):
            raise ValueError("Indique la date d'effet du changement de prix")
        else:
            try:
                date.fromisoformat(start)
            except ValueError:
                raise ValueError(f"Date invalide : {start}") from None
        periods = t.get("periods") or []
        if not isinstance(periods, list) or len(periods) > MAX_PERIODS:
            raise ValueError("Liste de périodes invalide")
        clean_periods = []
        for p in periods:
            if not isinstance(p, dict):
                raise ValueError("Période invalide")
            a, b = p.get("start"), p.get("end")
            if not (isinstance(a, str) and isinstance(b, str) and TIME_RE.match(a) and TIME_RE.match(b)):
                raise ValueError("Heure de période invalide (format HH:MM)")
            if a == b:
                raise ValueError("Une période ne peut pas commencer et finir à la même heure")
            clean_periods.append({
                "name": str(p.get("name") or "Période")[:40],
                "price": _price(p.get("price")),
                "start": a,
                "end": b,
            })
        clean.append({"from": start, "price": _price(t.get("price")), "periods": clean_periods})
    dates = [t["from"] for t in clean[1:]]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("Les changements de prix doivent être dans l'ordre des dates, sans doublon")
    return {"tariffs": clean}


def price_function(pricing):
    """Retourne une fonction ts -> prix du kWh (€) en vigueur à cet instant."""
    pricing = normalize_pricing(pricing)
    compiled = []
    for t in pricing["tariffs"]:
        start = date.fromisoformat(t["from"]) if t["from"] else date.min
        periods = [(_minutes(p["start"]), _minutes(p["end"]), p["price"]) for p in t["periods"]]
        compiled.append((start, t["price"], periods))

    def price_at(ts):
        dt = datetime.fromtimestamp(ts)
        d = dt.date()
        tariff = compiled[0]
        for c in compiled:
            if c[0] <= d:
                tariff = c
        m = dt.hour * 60 + dt.minute
        for start, end, price in tariff[2]:
            if (start < end and start <= m < end) or (start > end and (m >= start or m < end)):
                return price
        return tariff[1]

    return price_at


def current_tariff(pricing, today=None):
    """Tarif en vigueur aujourd'hui (pour l'affichage)."""
    today = today or date.today()
    current = pricing["tariffs"][0]
    for t in pricing["tariffs"]:
        if t["from"] is None or date.fromisoformat(t["from"]) <= today:
            current = t
    return current
