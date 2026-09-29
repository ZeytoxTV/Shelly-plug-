# Shelly App

Petite appli web pour piloter tes prises **Shelly** (Plug S, Plus Plug S, Plug Gen3…) depuis ton ordinateur ou ton téléphone :
- **Wi‑Fi maison** : directement sur le réseau local, sans cloud ;
- **Cloud (partout)** : via le Cloud Shelly, pour piloter la prise quand tu n'es pas chez toi.

Aucune dépendance : uniquement la bibliothèque standard Python (3.8+).

## Fonctionnalités

- Allumer / éteindre d'un appui sur le gros bouton
- Mesures en direct (rafraîchies toutes les 3 s) : puissance, énergie cumulée, tension, température
- Minuterie : « allume/éteins puis inverse dans 5 min, 15 min, 1 h… »
- **Graphique de consommation** : puissance sur 24 h, énergie par jour sur 7 et 30 jours
- **Programmation hebdomadaire** : des horaires d'allumage/extinction différents pour chaque jour
- **Protection anti-coupure** : à l'heure d'un arrêt programmé, si l'appareil consomme encore
  (PC allumé, partie en cours…), l'arrêt est reporté jusqu'à ce qu'il soit au repos
- Plusieurs prises gérées dans la même page
- Détection automatique de la génération (Gen1 ou Gen2/Gen3)
- Authentification supportée (Basic pour Gen1, Digest SHA-256 pour Gen2+)
- Installable sur l'écran d'accueil du téléphone (PWA), thème clair/sombre automatique
- **Widget Android 4×4** : état, conso, courbe 24 h, prochain horaire et bouton marche/arrêt

## Démarrage

```bash
python3 -m shelly_app
```

Puis ouvre <http://localhost:8080>, appuie sur **+** et entre l'adresse IP de ta prise
(visible dans l'appli Shelly officielle ou sur l'interface de ta box).

### Programmation et protection anti-coupure

Bouton **🗓 Programmation** sur la carte d'une prise :

1. Choisis une heure, « Allumer » ou « Éteindre », et les jours (ou *Semaine* / *Week-end* / *Tous*).
2. Répète pour chaque horaire ; la vue *Semaine* montre ce qui est prévu jour par jour.
3. **Protection anti-coupure** (activée par défaut) : à l'heure d'un arrêt, si la prise mesure plus que
   le **seuil** (ex. 30 W), elle reste allumée. Elle s'éteint seulement quand la consommation est restée
   sous le seuil pendant la durée choisie (ex. 5 min) — typiquement après l'arrêt du PC.
   Choisis un seuil entre la veille du PC éteint (quelques W) et sa conso allumé (souvent 50 W et plus).

Les horaires sont appliqués par le serveur (le Pi), même si aucune page n'est ouverte ;
ils suivent l'heure du Pi (`timedatectl` pour vérifier le fuseau). Les dernières actions automatiques
(allumages, arrêts, reports) sont listées en bas du panneau.

### Graphique

Bouton **📈 Consommation** : le serveur relève la consommation chaque minute et garde 90 jours
d'historique (`history.db`, à côté de `devices.json`). Touche le graphique pour lire une valeur ;
« Voir en tableau » donne les chiffres.

### Widget Android 4×4

Une mini-appli Android (32 Ko, sans pub ni traceur) ajoute un widget sur l'écran d'accueil :
état de la prise, puissance, énergie du jour, courbe des dernières 24 h, prochain horaire
(ou arrêt reporté) et un gros bouton **Allumer / Éteindre**.

1. Sur le téléphone, ouvre l'appli web et touche **📱 Installer le widget Android** en bas de page
   (ou `http://<pi>:8080/shelly-widget.apk`). Autorise l'installation depuis le navigateur.
2. Appui long sur l'écran d'accueil → **Widgets** → **Shelly Widget** → place-le en 4×4.
3. Vérifie l'adresse du Pi (Tailscale doit être actif sur le téléphone) et choisis la prise.

Le widget se met à jour environ toutes les 15 min, après chaque appui, et avec **↻**.
Toucher la courbe ouvre l'appli web. Si l'appareil consomme plus de 30 W, **Éteindre** demande
un second appui dans les 5 s, pour ne pas couper le PC par erreur.

Pour recompiler l'APK : `ANDROID_HOME=/chemin/sdk ./android/build.sh` (build-tools 34, sans Gradle).
La clé de signature `android/shelly-widget.keystore` est versionnée pour que les nouvelles
versions s'installent par-dessus l'ancienne.

### Piloter à distance (mode Cloud)

Pas besoin d'être sur le Wi‑Fi de la maison : l'appli passe par les serveurs Shelly,
comme l'appli officielle. Dans le formulaire d'ajout, choisis **Cloud (partout)** et renseigne :

| Champ | Où le trouver dans l'appli Shelly |
| --- | --- |
| Serveur | Paramètres utilisateur → Clé d'autorisation cloud (ex. `shelly-103-eu.shelly.cloud`) |
| Clé d'autorisation | Même écran, « Obtenir la clé » |
| Identifiant du dispositif | Prise → Paramètres → Informations sur le dispositif (ex. `083a8dc17ef5`) |

Le Cloud Shelly limite à 1 requête par seconde : les mesures sont rafraîchies toutes les 10 s
dans ce mode. La clé cloud donne accès à tous tes appareils Shelly : garde `devices.json` pour toi.

### Depuis ton téléphone

Le serveur écoute sur tout le réseau local par défaut. Sur ton téléphone (même Wi‑Fi),
ouvre `http://<IP-de-ton-ordinateur>:8080`, puis « Ajouter à l'écran d'accueil ».

### Options

| Option | Défaut | Rôle |
| --- | --- | --- |
| `--port` | `8080` | Port HTTP |
| `--bind` | `0.0.0.0` | Adresse d'écoute (`127.0.0.1` pour limiter à cette machine) |
| `--config` | `devices.json` | Fichier où sont enregistrées les prises |

> ⚠️ Les mots de passe des prises sont stockés en clair dans `devices.json` (jamais renvoyés au navigateur). L'appli n'a pas d'authentification propre : n'expose pas le port sur Internet.

## Installation sur un Raspberry Pi

Une seule commande, à lancer sur le Pi (en SSH ou directement) :

```bash
curl -fsSL https://raw.githubusercontent.com/ZeytoxTV/Shelly-plug-/claude/shelly-plug-controller-app-0lh3tk/install.sh | bash
```

Le script installe ce qu'il manque (`python3`, `git`), télécharge l'appli dans `~/shelly-app`,
crée un service systemd qui la démarre avec le Pi, puis affiche l'adresse à ouvrir.

**Mises à jour automatiques** : toutes les heures, le Pi regarde s'il y a une nouvelle version sur
GitHub (quelques Ko) et, seulement si c'est le cas, l'installe et redémarre l'appli. Tes prises,
horaires et historique sont conservés, et la page ouverte sur le téléphone se recharge d'elle-même.
Pour désactiver : relancer l'installation avec `SHELLY_AUTO_UPDATE=0`.
Journal : `journalctl -u shelly-app-update`.

Ressources sur le Pi : environ 30–40 Mo de RAM, ~6 Mo de disque par prise pour 30 jours d'historique
(90 jours max conservés).

Pour ouvrir l'appli depuis ton téléphone **hors de chez toi**, ajoute Tailscale (VPN gratuit) :

```bash
curl -fsSL https://raw.githubusercontent.com/ZeytoxTV/Shelly-plug-/claude/shelly-plug-controller-app-0lh3tk/install.sh | TAILSCALE=1 bash
```

puis installe l'appli Tailscale sur ton téléphone avec le même compte, et ouvre l'adresse `http://100.x.x.x:8080` affichée.

## API

L'interface s'appuie sur une petite API JSON, utilisable aussi en script :

```bash
curl http://localhost:8080/api/devices                                   # liste
curl http://localhost:8080/api/devices/<id>/status                       # état
curl -X POST http://localhost:8080/api/devices/<id>/switch \
     -H 'Content-Type: application/json' -d '{"action":"on","timer":600}' # on / off / toggle
curl http://localhost:8080/api/devices/<id>/history?range=24h           # 24h, 7d ou 30d
curl http://localhost:8080/api/devices/<id>/schedule                     # programmation (PUT pour modifier)
curl http://localhost:8080/api/devices/<id>/widget                       # résumé compact (widget)
```

## Tests

```bash
python3 -m unittest
```

Les tests simulent des prises Gen1 et Gen2 (avec authentification) : pas besoin de matériel.
