#!/usr/bin/env bash
# Compile l'APK du widget sans Gradle, avec les seuls outils du SDK Android.
#   ANDROID_HOME=/chemin/vers/sdk ./android/build.sh
# Produit shelly_app/static/shelly-widget.apk (servi par le Pi à /shelly-widget.apk).
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-/opt/android-sdk}"
BT="$SDK/build-tools/34.0.0"
JAR="$SDK/platforms/android-34/android.jar"
VERSION_CODE="${VERSION_CODE:-1}"
VERSION_NAME="${VERSION_NAME:-1.0}"
OUT=../shelly_app/static/shelly-widget.apk
B=build

rm -rf "$B" && mkdir -p "$B/gen" "$B/classes" "$B/dex"

"$BT/aapt2" compile --dir res -o "$B/res.zip"
"$BT/aapt2" link -I "$JAR" --manifest AndroidManifest.xml -o "$B/base.apk" \
  --java "$B/gen" --min-sdk-version 26 --target-sdk-version 34 \
  --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" "$B/res.zip"

if ! javac -nowarn -Xlint:none --release 8 -classpath "$JAR" -d "$B/classes" \
  $(find src "$B/gen" -name '*.java'); then
  echo "Échec de compilation Java"; exit 1
fi

"$BT/d8" --min-api 26 --lib "$JAR" --output "$B/dex" $(find "$B/classes" -name '*.class')
cp "$B/base.apk" "$B/unsigned.apk"
(cd "$B/dex" && zip -q -j ../unsigned.apk classes.dex)
"$BT/zipalign" -f -p 4 "$B/unsigned.apk" "$B/aligned.apk"

# Clé de signature fixe : les mises à jour de l'APK s'installent par-dessus l'ancienne version.
if [ ! -f shelly-widget.keystore ]; then
  keytool -genkeypair -keystore shelly-widget.keystore -storepass shellywidget -keypass shellywidget \
    -alias shelly -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=Shelly Widget" >/dev/null 2>&1
fi
"$BT/apksigner" sign --ks shelly-widget.keystore --ks-pass pass:shellywidget --key-pass pass:shellywidget \
  --out "$OUT" "$B/aligned.apk"
"$BT/apksigner" verify "$OUT"
rm -f "$OUT.idsig"
echo "APK : $(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT") ($(du -h "$OUT" | cut -f1))"
