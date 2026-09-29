# Shelly App

Petite appli web pour piloter tes prises **Shelly** (Plug S, Plus Plug S, Plug Gen3…) depuis ton ordinateur ou ton téléphone :
- **Wi‑Fi maison** : directement sur le réseau local, sans cloud ;
- **Cloud (partout)** : via le Cloud Shelly, pour piloter la prise quand tu n'es pas chez toi.

Aucune dépendance : uniquement la bibliothèque standard Python (3.8+).

## Fonctionnalités

- Allumer / éteindre d'un appui sur le gros bouton
- Mesures en direct (rafraîchies toutes les 3 s) : puissance, énergie cumulée, tension, température
- Minuterie : « allume/éteins puis inverse dans 5 min, 15 min, 1 h… »
- Plusieurs prises gérées dans la même page
- Détection automatique de la génération (Gen1 ou Gen2/Gen3)
- Authentification supportée (Basic pour Gen1, Digest SHA-256 pour Gen2+)
- Installable sur l'écran d'accueil du téléphone (PWA), thème clair/sombre automatique

## Démarrage

```bash
python3 -m shelly_app
```

Puis ouvre <http://localhost:8080>, appuie sur **+** et entre l'adresse IP de ta prise
(visible dans l'appli Shelly officielle ou sur l'interface de ta box).

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
Relance la même commande pour mettre à jour.

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
```

## Tests

```bash
python3 -m unittest
```

Les tests simulent des prises Gen1 et Gen2 (avec authentification) : pas besoin de matériel.
