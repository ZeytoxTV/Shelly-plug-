package fr.zeytox.shellywidget;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;

/**
 * Accès au serveur Shelly App (le Pi) et aux réglages enregistrés.
 *
 * Le Pi a plusieurs adresses : celle de la maison (Wi‑Fi, 192.168.x.x) et celle de Tailscale
 * (100.x.x.x). Le widget les essaie l'une après l'autre, en commençant par la dernière qui a
 * répondu : il marche à la maison sans Tailscale, et dehors avec.
 */
final class Api {
    static final String DEFAULT_SERVER = "http://100.100.255.12:8080";
    private static final int TIMEOUT_MS = 8000;
    /** Délai de connexion court : une adresse injoignable (Wi‑Fi absent, VPN coupé) est vite écartée. */
    private static final int CONNECT_TIMEOUT_MS = 2500;
    private static final long INFO_REFRESH_MS = 6 * 3600 * 1000L;

    private Api() {}

    /** Le Pi a répondu avec une erreur (ex. prise injoignable) : inutile d'essayer une autre adresse. */
    static final class ServerError extends Exception {
        final int code;
        final boolean authRequired;

        ServerError(String message, int code, boolean authRequired) {
            super(message);
            this.code = code;
            this.authRequired = authRequired;
        }
    }

    /** Jeton d'accès du site (si un code PIN est activé sur le Pi), envoyé à chaque requête. */
    private static volatile String token;

    static void loadToken(Context c) {
        token = prefs(c).getString("token", null);
    }

    /** Envoie le code PIN au Pi et retient le jeton reçu. */
    static void login(Context c, String base, String pin) throws Exception {
        JSONObject body = new JSONObject();
        body.put("pin", pin);
        String t = new JSONObject(request("POST", normalize(base) + "/api/login", body.toString(), CONNECT_TIMEOUT_MS))
                .optString("token", "");
        prefs(c).edit().putString("token", t).apply();
        token = t;
    }

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("shelly", Context.MODE_PRIVATE);
    }

    /** Adresse saisie par l'utilisateur (ou la dernière qui a marché). */
    static String server(Context c) {
        SharedPreferences p = prefs(c);
        return p.getString("server_ok", p.getString("server", DEFAULT_SERVER));
    }

    static String deviceFor(Context c, int widgetId) {
        return prefs(c).getString("device_" + widgetId, null);
    }

    static String normalize(String server) {
        String s = server.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        return s;
    }

    /** Adresses à essayer, dans l'ordre : dernière réussie, maison, Tailscale, adresse saisie. */
    static List<String> candidates(Context c) {
        SharedPreferences p = prefs(c);
        LinkedHashSet<String> out = new LinkedHashSet<>();
        String ok = p.getString("server_ok", null);
        if (ok != null) out.add(ok);
        try {
            JSONObject known = new JSONObject(p.getString("addresses", "{}"));
            for (String key : new String[] {"lan", "tailscale"}) {
                JSONArray arr = known.optJSONArray(key);
                for (int i = 0; arr != null && i < arr.length(); i++) out.add(normalize(arr.getString(i)));
            }
        } catch (Exception ignored) {
        }
        out.add(p.getString("server", DEFAULT_SERVER));
        return new ArrayList<>(out);
    }

    static String request(String method, String url, String body, int connectTimeout) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(connectTimeout);
        conn.setReadTimeout(TIMEOUT_MS);
        conn.setRequestMethod(method);
        if (token != null && !token.isEmpty()) conn.setRequestProperty("X-Shelly-Token", token);
        if (body != null) {
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.getBytes(StandardCharsets.UTF_8));
            }
        }
        int code = conn.getResponseCode();
        InputStream in = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String text = "";
        if (in != null) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            in.close();
            text = buf.toString("UTF-8");
        }
        conn.disconnect();
        if (code >= 400) {
            String msg = "Erreur " + code;
            boolean auth = false;
            try {
                JSONObject err = new JSONObject(text);
                msg = err.optString("error", msg);
                auth = err.optBoolean("auth_required", false);
            } catch (Exception ignored) {
            }
            throw new ServerError(msg, code, auth);
        }
        return text;
    }

    static String request(String method, String url, String body) throws Exception {
        return request(method, url, body, TIMEOUT_MS);
    }

    /** Appelle le Pi sur la première adresse qui répond, et la retient pour la prochaine fois. */
    static String call(Context c, String method, String path, String body) throws Exception {
        if (token == null) loadToken(c);
        Exception last = null;
        ServerError authError = null;
        for (String base : candidates(c)) {
            try {
                String text = request(method, base + path, body, CONNECT_TIMEOUT_MS);
                SharedPreferences p = prefs(c);
                if (!base.equals(p.getString("server_ok", null))) p.edit().putString("server_ok", base).apply();
                maybeLearnAddresses(c, base);
                return text;
            } catch (ServerError e) {
                // Code PIN demandé sur le Wi‑Fi : Tailscale passe sans code, on essaie l'adresse suivante
                if (e.authRequired) {
                    authError = e;
                    continue;
                }
                throw e; // le Pi a répondu : pas la peine d'essayer ailleurs
            } catch (IOException e) {
                last = e; // adresse injoignable : on essaie la suivante
            }
        }
        if (authError != null) {
            throw new ServerError("Code d'accès requis : ouvre l'appli Shelly Widget pour le saisir", 401, true);
        }
        throw new Exception("Pi injoignable" + (last != null ? " (" + last.getClass().getSimpleName() + ")" : ""));
    }

    /** Récupère les adresses du Pi (maison + Tailscale) via /api/info, au plus toutes les 6 h. */
    static void maybeLearnAddresses(Context c, String base) {
        SharedPreferences p = prefs(c);
        if (System.currentTimeMillis() - p.getLong("addresses_at", 0) < INFO_REFRESH_MS) return;
        learnAddresses(c, base);
    }

    static JSONObject learnAddresses(Context c, String base) {
        try {
            JSONObject info = new JSONObject(request("GET", base + "/api/info", null, CONNECT_TIMEOUT_MS));
            prefs(c).edit()
                    .putString("addresses", info.toString())
                    .putLong("addresses_at", System.currentTimeMillis())
                    .apply();
            return info;
        } catch (Exception e) {
            return null; // ancien serveur sans /api/info : on garde l'adresse saisie
        }
    }

    /** Première adresse qui répond (pour ouvrir l'appli web), ou la dernière connue. */
    static String reachableServer(Context c) {
        if (token == null) loadToken(c);
        for (String base : candidates(c)) {
            try {
                request("GET", base + "/api/info", null, 1500);
                prefs(c).edit().putString("server_ok", base).apply();
                return base;
            } catch (ServerError e) {
                return base; // ancien serveur : il répond, c'est le bon
            } catch (Exception ignored) {
            }
        }
        return server(c);
    }

    static JSONArray devices(Context c, String server) throws Exception {
        if (token == null) loadToken(c);
        return new JSONArray(request("GET", normalize(server) + "/api/devices", null, CONNECT_TIMEOUT_MS));
    }

    static JSONObject widget(Context c, String deviceId) throws Exception {
        return new JSONObject(call(c, "GET", "/api/devices/" + deviceId + "/widget", null));
    }

    static void switchTo(Context c, String deviceId, String action) throws Exception {
        JSONObject body = new JSONObject();
        body.put("action", action);
        call(c, "POST", "/api/devices/" + deviceId + "/switch", body.toString());
    }
}
