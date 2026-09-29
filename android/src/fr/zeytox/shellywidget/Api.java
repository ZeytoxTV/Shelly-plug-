package fr.zeytox.shellywidget;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Accès au serveur Shelly App (le Pi) et aux réglages enregistrés. */
final class Api {
    static final String DEFAULT_SERVER = "http://100.100.255.12:8080";
    private static final int TIMEOUT_MS = 8000;

    private Api() {}

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("shelly", Context.MODE_PRIVATE);
    }

    static String server(Context c) {
        return prefs(c).getString("server", DEFAULT_SERVER);
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

    static String request(String method, String url, String body) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(TIMEOUT_MS);
        conn.setReadTimeout(TIMEOUT_MS);
        conn.setRequestMethod(method);
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
            try {
                msg = new JSONObject(text).optString("error", msg);
            } catch (Exception ignored) {
            }
            throw new Exception(msg);
        }
        return text;
    }

    static JSONArray devices(String server) throws Exception {
        return new JSONArray(request("GET", normalize(server) + "/api/devices", null));
    }

    static JSONObject widget(Context c, String deviceId) throws Exception {
        return new JSONObject(request("GET", server(c) + "/api/devices/" + deviceId + "/widget", null));
    }

    static void switchTo(Context c, String deviceId, String action) throws Exception {
        JSONObject body = new JSONObject();
        body.put("action", action);
        request("POST", server(c) + "/api/devices/" + deviceId + "/switch", body.toString());
    }
}
