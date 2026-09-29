#!/usr/bin/env bash
# Met à jour Shelly App puis enregistre les prix de l'offre ENGIE « Elec Happy Heures Vertes 1 an »
# (option 6 kVA, prix TTC du courrier de renouvellement de septembre 2026), avec la plage verte 15h-17h.
#   curl -fsSL https://raw.githubusercontent.com/ZeytoxTV/Shelly-plug-/claude/shelly-plug-controller-app-0lh3tk/scripts/tarif-engie-hhv.sh | bash
set -euo pipefail
BASE="https://raw.githubusercontent.com/ZeytoxTV/Shelly-plug-/claude/shelly-plug-controller-app-0lh3tk"
PORT="${SHELLY_PORT:-8080}"

curl -fsSL "$BASE/install.sh" | bash

echo "==> Enregistrement des prix ENGIE Happy Heures Vertes"
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -fs -o /dev/null "http://localhost:$PORT/api/settings" && break
  sleep 1
done
curl -fsS -o /dev/null -X PUT "http://localhost:$PORT/api/settings" -H 'Content-Type: application/json' -d '{"pricing":{"tariffs":[
  {"from":null,"price":0.24557,"periods":[
    {"name":"Heures creuses","price":0.19154,"start":"00:00","end":"06:00"},
    {"name":"Happy Heures Vertes","price":0.03674,"start":"15:00","end":"17:00"}]},
  {"from":"2026-11-01","price":0.26153,"periods":[
    {"name":"Heures creuses","price":0.20405,"start":"00:00","end":"06:00"},
    {"name":"Happy Heures Vertes","price":0.03674,"start":"15:00","end":"17:00"}]}]}}'
echo "✅ Prix enregistrés : HP 0,24557 € · HC 0,19154 € · Heures vertes 0,03674 € (nouveaux prix au 01/11/2026)."
