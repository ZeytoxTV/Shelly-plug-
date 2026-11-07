# Bot de trading Freqtrade

Bot crypto [Freqtrade](https://www.freqtrade.io) prêt à l'emploi, configuré en **mode simulation (dry-run)** :
il trade avec un portefeuille fictif de 1000 USDT, **aucun argent réel n'est utilisé**.

- Plateforme : Binance (spot)
- Paires : BTC, ETH, SOL, DOGE, XRP contre USDT
- Stratégie : `SampleStrategy` (exemple fourni par freqtrade, RSI + Bollinger + TEMA, bougies 5 min)
- Max 3 trades ouverts en même temps
- Interface web (FreqUI) sur http://localhost:8080

## 1. Installer Docker

- Windows / Mac : installe [Docker Desktop](https://www.docker.com/products/docker-desktop/) et lance-le.
- Linux : `curl -fsSL https://get.docker.com | sh`

## 2. Récupérer le projet

```bash
git clone https://github.com/ZeytoxTV/Shelly-plug-.git
cd Shelly-plug-/freqtrade
```

## 3. Créer le fichier des secrets

```bash
cp .env.example .env
```

Ouvre `.env` et remplace les trois `change_moi` :
- `FREQTRADE__API_SERVER__PASSWORD` : ton mot de passe pour l'interface web
- `FREQTRADE__API_SERVER__JWT_SECRET_KEY` et `FREQTRADE__API_SERVER__WS_TOKEN` : des valeurs aléatoires
  d'au moins 32 caractères (génère-les avec `openssl rand -hex 32`, ou tape n'importe quoi de long).

Pas besoin de clés Binance en dry-run.

## 4. Lancer le bot

```bash
docker compose pull
docker compose up -d
```

Puis ouvre **http://localhost:8080**, connecte-toi avec `freqtrader` + ton mot de passe.

Commandes utiles :

```bash
docker compose logs -f      # voir ce que fait le bot
docker compose stop         # arrêter
docker compose up -d        # relancer
```

## 5. Tester une stratégie sur le passé (backtest)

À faire **avant** de penser au réel :

```bash
# Télécharge 3 mois d'historique
docker compose run --rm freqtrade download-data --config /freqtrade/user_data/config.json -t 5m 1h --days 90

# Rejoue la stratégie sur cet historique
docker compose run --rm freqtrade backtesting --config /freqtrade/user_data/config.json --strategy SampleStrategy
```

> Test fait le 03/11/2026 (août → novembre 2026, données KuCoin) : `SampleStrategy` a fait **-10,8 %**
> (99 trades, 84 % gagnants mais les pertes sont plus grosses que les gains).
> C'est une stratégie d'exemple : elle **perd de l'argent**, ne la passe pas en réel.

Les stratégies sont dans `user_data/strategies/`. Pour en changer, mets le fichier `.py` dedans et
modifie `--strategy` dans `docker-compose.yml`.

## 5 bis. Comparatif automatique des stratégies

Le script `comparatif/run_comparatif.py` télécharge les stratégies de
[freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) et la dernière version de
[NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity), récupère ~2 ans d'historique
Binance sur 15 cryptos, backteste chaque stratégie depuis janvier 2025 et sort un classement :

- 🛡️ **bot stable** : rentable, faible perte max, un maximum de mois positifs ;
- 🧪 **bot labo** : meilleur profit brut avec une perte max < 40 % ;
- détail par période : hausse (avr→juil 2025), baisse (oct 2025→fév 2026), sans tendance (mars→oct 2026).

```bash
docker compose run --rm comparatif
```

Compte 30 min à 2 h selon la machine (NostalgiaForInfinity est très lent). Le rapport est écrit dans
`user_data/comparatif/rapport.md`.

Options : `--sans-telechargement` (réutilise les stratégies déjà téléchargées), `--sans-donnees`
(réutilise l'historique), `--strategies Nom1 Nom2` (ne tester que celles-là). Exemple :
`docker compose run --rm comparatif --sans-donnees --strategies Supertrend Heracles`.

## 6. Passer en réel (plus tard, prudemment)

Seulement après des backtests positifs **et** plusieurs semaines de dry-run correctes :

1. Sur Binance, crée une clé API avec **uniquement** « Enable Spot & Margin Trading ».
   **Ne coche jamais « Enable Withdrawals »**, et limite la clé à l'IP de ta machine si possible.
2. Mets la clé et le secret dans `.env` (`FREQTRADE__EXCHANGE__KEY` / `FREQTRADE__EXCHANGE__SECRET`).
3. Dans `user_data/config.json`, mets `"dry_run": false` et commence avec une petite somme.
4. `docker compose up -d --force-recreate`

⚠️ Le trading automatique peut faire perdre tout l'argent engagé. N'investis que ce que tu peux perdre.

## Notifications Telegram (optionnel)

1. Crée un bot avec [@BotFather](https://t.me/BotFather) → tu obtiens un token.
2. Récupère ton chat id avec [@userinfobot](https://t.me/userinfobot).
3. Mets-les dans `.env` et passe `"enabled": true` dans la section `telegram` de `config.json`.
