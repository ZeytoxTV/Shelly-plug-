#!/usr/bin/env bash
# Installe Shelly App sur un Raspberry Pi (ou tout Linux avec systemd) et la lance au démarrage.
#
#   curl -fsSL https://raw.githubusercontent.com/ZeytoxTV/Shelly-plug-/claude/shelly-plug-controller-app-0lh3tk/install.sh | bash
#
# Variables optionnelles : SHELLY_PORT (défaut 8080), SHELLY_BRANCH, SHELLY_DIR,
# TAILSCALE=1 pour installer aussi Tailscale (accès depuis ton téléphone hors de chez toi).
set -euo pipefail

REPO="https://github.com/ZeytoxTV/Shelly-plug-.git"
BRANCH="${SHELLY_BRANCH:-claude/shelly-plug-controller-app-0lh3tk}"
PORT="${SHELLY_PORT:-8080}"
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
DIR="${SHELLY_DIR:-$RUN_HOME/shelly-app}"

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"
as_user() { if [ "$(id -un)" = "$RUN_USER" ]; then "$@"; else sudo -u "$RUN_USER" "$@"; fi; }

echo "==> Vérification des paquets (python3, git)"
missing=()
command -v python3 >/dev/null || missing+=(python3)
command -v git >/dev/null || missing+=(git)
if [ ${#missing[@]} -gt 0 ]; then
  $SUDO apt-get update -q
  $SUDO apt-get install -y -q "${missing[@]}"
fi

echo "==> Téléchargement dans $DIR"
if [ -d "$DIR/.git" ]; then
  as_user git -C "$DIR" fetch -q origin "$BRANCH"
  as_user git -C "$DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  as_user git clone -q --branch "$BRANCH" "$REPO" "$DIR"
fi

echo "==> Création du service systemd (démarrage automatique)"
$SUDO tee /etc/systemd/system/shelly-app.service >/dev/null <<EOF
[Unit]
Description=Shelly App
Wants=network-online.target
After=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
ExecStart=$(command -v python3) -m shelly_app --port $PORT --config $DIR/devices.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable -q shelly-app
$SUDO systemctl restart shelly-app

sleep 2
if ! $SUDO systemctl is-active -q shelly-app; then
  echo "!! Le service n'a pas démarré. Journal :"
  $SUDO journalctl -u shelly-app -n 20 --no-pager
  exit 1
fi

if [ "${TAILSCALE:-0}" = "1" ]; then
  echo "==> Installation de Tailscale"
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  $SUDO tailscale up
fi

IP="$(hostname -I | awk '{print $1}')"
echo
echo "✅ Shelly App tourne et redémarrera toute seule avec le Pi."
echo "   Sur le Wi-Fi de la maison : http://$IP:$PORT"
if command -v tailscale >/dev/null && TS_IP="$(tailscale ip -4 2>/dev/null | head -1)" && [ -n "$TS_IP" ]; then
  echo "   De partout (Tailscale)    : http://$TS_IP:$PORT"
fi
echo
TZ_NAME="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo inconnu)"
echo "   Heure du Pi : $(date '+%H:%M') ($TZ_NAME) — les horaires programmés suivent cette heure."
echo "   (Si ce n'est pas la bonne : sudo timedatectl set-timezone Europe/Paris)"
echo
echo "   Mise à jour : relance cette même commande."
echo "   Journal     : sudo journalctl -u shelly-app -f"
