#!/usr/bin/env bash
# Lancé toutes les heures par shelly-app-update.timer (installé par install.sh).
# Récupère la dernière version depuis GitHub et redémarre l'appli seulement si le code a changé.
# devices.json et history.db ne sont pas suivis par git : ils sont conservés.
set -euo pipefail
DIR="$1"; BRANCH="$2"; RUN_USER="$3"
as_user() { runuser -u "$RUN_USER" -- "$@"; }

as_user git -C "$DIR" fetch -q origin "$BRANCH"
LOCAL="$(as_user git -C "$DIR" rev-parse HEAD)"
REMOTE="$(as_user git -C "$DIR" rev-parse "origin/$BRANCH")"
if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0
fi
as_user git -C "$DIR" reset -q --hard "origin/$BRANCH"
systemctl restart shelly-app
echo "Shelly App mise à jour : ${LOCAL:0:7} -> ${REMOTE:0:7}"
