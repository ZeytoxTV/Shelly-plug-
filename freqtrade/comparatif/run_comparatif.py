"""Comparatif automatique de stratégies Freqtrade.

Télécharge les stratégies communautaires (freqtrade-strategies + NostalgiaForInfinity),
récupère l'historique de marché, backteste chaque stratégie séparément puis écrit un
classement dans user_data/comparatif/rapport.md.

Lancement (depuis le dossier freqtrade/) :
    docker compose run --rm comparatif
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SOURCES = {
    "freqtrade-strategies": "https://github.com/freqtrade/freqtrade-strategies/archive/HEAD.zip",
    "NostalgiaForInfinity": "https://github.com/iterativv/NostalgiaForInfinity/archive/HEAD.zip",
}

PAIRES = "BTC ETH SOL XRP DOGE ADA AVAX LINK DOT LTC TRX BCH SUI NEAR ATOM".split()

# Périodes de marché observées sur le BTC (clôtures mensuelles).
PERIODES = {
    "Hausse (avr→juil 2025)": ("2025-04-01", "2025-08-01"),
    "Baisse (oct 2025→fév 2026)": ("2025-10-01", "2026-03-01"),
    "Sans tendance (mars→oct 2026)": ("2026-03-01", "2026-11-01"),
}
DEBUT_TEST = "20250101"
DEBUT_DONNEES = "20240801"  # marge pour le préchauffage des indicateurs

# Unités de temps autorisées : on écarte le 1m (trop sensible aux frais / à la latence).
TF_OK = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
EXCLUS = re.compile(r"DoesNothing|backtest_validation|lookahead", re.I)


def log(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def telecharger_strategies(dest: Path) -> list[Path]:
    """Télécharge les dépôts et copie les stratégies spot éligibles dans dest."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.glob("*.py"):
        f.unlink()
    retenues: list[Path] = []
    for nom, url in SOURCES.items():
        log(f"Téléchargement de {nom}")
        with urllib.request.urlopen(url, timeout=120) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        fichiers = [n for n in z.namelist() if n.endswith(".py")]
        if nom == "NostalgiaForInfinity":
            # On ne garde que la version la plus récente (NostalgiaForInfinityX<N>.py)
            nfi = [n for n in fichiers if re.search(r"/NostalgiaForInfinityX\d+\.py$", n)]
            fichiers = sorted(nfi, key=lambda n: int(re.search(r"X(\d+)\.py$", n).group(1)))[-1:]
        else:
            fichiers = [
                n for n in fichiers
                if "/user_data/strategies/" in n and "/futures/" not in n and "/lookahead_bias/" not in n
            ]
        for n in fichiers:
            cible = dest / Path(n).name
            cible.write_bytes(z.read(n))
            retenues.append(cible)
    return retenues


def lire_strategie(fichier: Path) -> tuple[str, str] | None:
    """Renvoie (nom de classe, timeframe) si la stratégie est éligible."""
    src = fichier.read_text(errors="ignore")
    classe = re.search(r"^class\s+(\w+)\s*\(", src, re.M)
    tf = re.search(r"^\s+timeframe\s*=\s*['\"](\w+)['\"]", src, re.M)
    if not classe or not tf or tf.group(1) not in TF_OK or EXCLUS.search(classe.group(1)):
        return None
    return classe.group(1), tf.group(1)


def construire_config(base: Path, sortie: Path, exchange_override: dict | None) -> None:
    c = json.loads(base.read_text())
    for cle in ("api_server", "telegram"):
        c.pop(cle, None)
    c["max_open_trades"] = 4
    c["dry_run"] = True
    c["dry_run_wallet"] = 1000
    c["exchange"]["pair_whitelist"] = [f"{p}/USDT" for p in PAIRES]
    c["exchange"]["pair_blacklist"] = []
    c["pairlists"] = [{"method": "StaticPairList"}]
    if exchange_override:
        c["exchange"].update(exchange_override)
    sortie.write_text(json.dumps(c, indent=2))


def ft(*args: str, log_file: Path | None = None) -> int:
    cmd = ["freqtrade", *args]
    if log_file:
        with log_file.open("w") as fh:
            return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    return subprocess.run(cmd).returncode


def charger_resultat(dossier: Path, strategie: str) -> dict | None:
    zips = sorted(dossier.glob("backtest-result-*.zip"))
    if not zips:
        return None
    with zipfile.ZipFile(zips[-1]) as z:
        nom = next(n for n in z.namelist() if re.search(r"backtest-result-[\d_-]+\.json$", n))
        return json.loads(z.read(nom))["strategy"].get(strategie)


def analyser(strat: str, r: dict) -> dict:
    depart = r["starting_balance"]
    regimes = {}
    for label, (debut, fin) in PERIODES.items():
        tot = sum(
            t["profit_abs"] for t in r["trades"]
            if debut <= t["close_date"][:10] < fin
        )
        regimes[label] = tot / depart * 100
    mois = r.get("periodic_breakdown", {}).get("month", [])
    mois_pos = sum(1 for m in mois if m.get("profit_abs", 0) > 0)
    mois_actifs = sum(1 for m in mois if m.get("trades", 0) > 0) or 1
    dd = r["max_drawdown_account"] * 100
    profit = r["profit_total"] * 100
    nb_mois = max(r["backtest_days"] / 30.44, 1)
    return {
        "strategie": strat,
        "timeframe": r["timeframe"],
        "profit": profit,
        "profit_mensuel": ((1 + r["profit_total"]) ** (1 / nb_mois) - 1) * 100 if r["profit_total"] > -1 else -100,
        "drawdown": dd,
        "trades": r["total_trades"],
        "winrate": r["winrate"] * 100 if r.get("winrate") is not None else 0,
        "profit_factor": r.get("profit_factor") or 0,
        "calmar": r.get("calmar") or 0,
        "mois_positifs": f"{mois_pos}/{mois_actifs}",
        "ratio_mois": mois_pos / mois_actifs,
        "regimes": regimes,
    }


def score_stable(a: dict) -> float:
    """Régularité : gagne dans toutes les périodes, faible perte max, mois positifs."""
    if a["trades"] < 30 or a["profit"] <= 0:
        return -1e9
    penalite = sum(min(v, 0) for v in a["regimes"].values()) * 2
    return a["profit"] / max(a["drawdown"], 1) * 10 + a["ratio_mois"] * 20 + penalite


def score_labo(a: dict) -> float:
    """Performance brute, tant que la perte max reste supportable (< 40 %)."""
    if a["trades"] < 30 or a["drawdown"] > 40:
        return -1e9
    return a["profit"]


def ecrire_rapport(analyses: list[dict], echecs: dict, sortie: Path, fin: str) -> str:
    def ligne(a: dict) -> str:
        reg = " | ".join(f"{v:+.1f} %" for v in a["regimes"].values())
        return (
            f"| {a['strategie']} | {a['timeframe']} | {a['profit']:+.1f} % | {a['profit_mensuel']:+.2f} % "
            f"| {a['drawdown']:.1f} % | {a['trades']} | {a['winrate']:.0f} % | {a['profit_factor']:.2f} "
            f"| {a['mois_positifs']} | {reg} |"
        )

    entete = (
        "| Stratégie | TF | Profit total | Profit/mois | Perte max | Trades | Gagnants | Profit factor "
        "| Mois positifs | " + " | ".join(PERIODES) + " |\n"
        "|---|---|---|---|---|---|---|---|---|" + "---|" * len(PERIODES)
    )
    stables = sorted((a for a in analyses if score_stable(a) > -1e9), key=score_stable, reverse=True)
    labos = sorted((a for a in analyses if score_labo(a) > -1e9), key=score_labo, reverse=True)
    tout = sorted(analyses, key=lambda a: a["profit"], reverse=True)

    md = [
        "# Comparatif de stratégies Freqtrade",
        "",
        f"Généré le {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC — période {DEBUT_TEST} → {fin}, "
        f"{len(PAIRES)} paires spot USDT, 1000 USDT de départ, 4 trades max, frais inclus.",
        "",
        "Les colonnes de périodes donnent le profit (≈, en % du capital de départ) des trades clôturés "
        "pendant cette période.",
        "",
        "## 🛡️ Candidats « bot stable »",
        "Rentable sur l'ensemble, faible perte max, un maximum de mois positifs, pénalisé s'il perd dans une période.",
        "",
        entete,
        *[ligne(a) for a in stables[:10]],
        "",
        "## 🧪 Candidats « bot labo »",
        "Meilleur profit brut avec une perte max inférieure à 40 %.",
        "",
        entete,
        *[ligne(a) for a in labos[:10]],
        "",
        "## Toutes les stratégies (par profit total)",
        "",
        entete,
        *[ligne(a) for a in tout],
    ]
    if echecs:
        md += ["", "## Stratégies en échec", ""]
        md += [f"- **{s}** : {raison}" for s, raison in echecs.items()]
    md += [
        "",
        "⚠️ Un bon backtest ne garantit rien : valider ensuite plusieurs semaines en dry-run.",
    ]
    texte = "\n".join(md) + "\n"
    sortie.write_text(texte)
    return texte


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--userdir", default="/freqtrade/user_data")
    p.add_argument("--strategies", nargs="*", help="Limiter à ces classes (test)")
    p.add_argument("--sans-telechargement", action="store_true",
                   help="Ne pas re-télécharger les stratégies (réutilise celles déjà présentes)")
    p.add_argument("--sans-donnees", action="store_true", help="Ne pas re-télécharger l'historique")
    p.add_argument("--exchange-override", help="JSON fusionné dans config.exchange (tests)")
    args = p.parse_args()

    ud = Path(args.userdir)
    travail = ud / "comparatif"
    strat_dir = travail / "strategies"
    res_dir = travail / "resultats"
    logs_dir = travail / "logs"
    for d in (res_dir, logs_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    fin = datetime.now(timezone.utc).strftime("%Y%m%d")

    if args.sans_telechargement:
        fichiers = sorted(strat_dir.glob("*.py"))
    else:
        fichiers = telecharger_strategies(strat_dir)

    candidates = {}
    for f in fichiers:
        info = lire_strategie(f)
        if info and (not args.strategies or info[0] in args.strategies):
            candidates[info[0]] = info[1]
    if not candidates:
        sys.exit("Aucune stratégie éligible trouvée.")
    log(f"{len(candidates)} stratégies retenues : {', '.join(sorted(candidates))}")

    config = travail / "config-comparatif.json"
    override = json.loads(args.exchange_override) if args.exchange_override else None
    construire_config(ud / "config.json", config, override)

    if not args.sans_donnees:
        log("Téléchargement de l'historique de marché (peut prendre 10-20 min la première fois)")
        tfs = sorted({"5m", "15m", "1h", "4h", "1d"} | set(candidates.values()))
        code = ft("download-data", "--userdir", str(ud), "-c", str(config),
                  "--timerange", f"{DEBUT_DONNEES}-", "-t", *tfs)
        if code != 0:
            sys.exit("Échec du téléchargement des données.")

    analyses, echecs = [], {}
    for i, strat in enumerate(sorted(candidates), 1):
        print(f"[{i}/{len(candidates)}] Backtest {strat}…", flush=True)
        dossier = res_dir / strat
        dossier.mkdir()
        code = ft("backtesting", "--userdir", str(ud), "-c", str(config),
                  "--strategy-path", str(strat_dir), "--strategy", strat,
                  "--timerange", f"{DEBUT_TEST}-{fin}", "--breakdown", "month",
                  "--export", "trades", "--backtest-directory", str(dossier), "--cache", "none",
                  log_file=logs_dir / f"{strat}.log")
        r = charger_resultat(dossier, strat) if code == 0 else None
        if r is None:
            derniere = (logs_dir / f"{strat}.log").read_text(errors="ignore").strip().splitlines()[-1:]
            echecs[strat] = (derniere[0][:200] if derniere else f"code {code}")
            print(f"   ✗ échec ({echecs[strat]})")
            continue
        a = analyser(strat, r)
        analyses.append(a)
        print(f"   ✓ profit {a['profit']:+.1f} %, perte max {a['drawdown']:.1f} %, {a['trades']} trades")

    rapport = travail / "rapport.md"
    print("\n" + ecrire_rapport(analyses, echecs, rapport, fin))
    print(f"Rapport écrit dans {rapport}")


if __name__ == "__main__":
    main()
