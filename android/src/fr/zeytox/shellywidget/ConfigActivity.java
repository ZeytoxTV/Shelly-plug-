package fr.zeytox.shellywidget;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Réglages : adresse du serveur (le Pi) et choix de la prise affichée par le widget.
 * Ouvert à l'ajout du widget, via « Paramètres » du widget, ou depuis l'icône de l'appli.
 */
public class ConfigActivity extends Activity {
    private int widgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private EditText server;
    private LinearLayout list;
    private TextView status;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setResult(RESULT_CANCELED);
        Bundle extras = getIntent().getExtras();
        if (extras != null) {
            widgetId = extras.getInt(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        }

        int pad = dp(20);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad * 2, pad, pad);

        TextView title = new TextView(this);
        title.setText(widgetId == AppWidgetManager.INVALID_APPWIDGET_ID ? "Shelly Widget" : "Choisis la prise du widget");
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        TextView help = new TextView(this);
        help.setText(widgetId == AppWidgetManager.INVALID_APPWIDGET_ID
                ? "Pour ajouter un widget : appui long sur l'écran d'accueil → Widgets → Shelly Widget.\n\n"
                  + "Adresse de ton Pi (maison ou Tailscale : l'autre est trouvée automatiquement) :"
                : "Adresse de ton Pi (maison ou Tailscale : l'autre est trouvée automatiquement) :");
        help.setPadding(0, dp(12), 0, dp(6));
        root.addView(help);

        server = new EditText(this);
        server.setSingleLine(true);
        server.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        server.setText(Api.server(this));
        root.addView(server, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Button load = new Button(this);
        load.setText("Charger mes prises");
        load.setOnClickListener(v -> loadDevices());
        root.addView(load);

        status = new TextView(this);
        status.setPadding(0, dp(12), 0, dp(6));
        root.addView(status);

        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        root.addView(list);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
        loadDevices();
    }

    private void loadDevices() {
        final String url = Api.normalize(server.getText().toString());
        status.setText("Connexion à " + url + "…");
        list.removeAllViews();
        new Thread(() -> {
            try {
                JSONArray devices = Api.devices(getApplicationContext(), url);
                JSONObject info = Api.learnAddresses(getApplicationContext(), url);
                runOnUiThread(() -> {
                    showDevices(url, devices);
                    showAddresses(info);
                });
            } catch (Api.ServerError e) {
                if (e.authRequired) {
                    runOnUiThread(() -> askPin(url, null));
                } else {
                    runOnUiThread(() -> status.setText("Le Pi a répondu : " + e.getMessage()));
                }
            } catch (Exception e) {
                runOnUiThread(() -> status.setText("Impossible de joindre le Pi : " + e.getMessage()
                        + "\n\nVérifie que tu es sur le Wi‑Fi de la maison ou que Tailscale est activé, "
                        + "et que l'adresse est la bonne."));
            }
        }).start();
    }

    /** Le Pi demande le code d'accès (PIN activé, connexion par le Wi‑Fi) : on le demande une fois. */
    private void askPin(String url, String error) {
        status.setText((error != null ? error + "\n\n" : "")
                + "🔒 Le site est protégé par un code d'accès sur le Wi‑Fi. Tape-le une fois :");
        list.removeAllViews();
        EditText pin = new EditText(this);
        pin.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        pin.setHint("Code d'accès");
        list.addView(pin, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        Button ok = new Button(this);
        ok.setText("Valider");
        ok.setOnClickListener(v -> {
            final String code = pin.getText().toString().trim();
            status.setText("Vérification du code…");
            new Thread(() -> {
                try {
                    Api.login(getApplicationContext(), url, code);
                    runOnUiThread(this::loadDevices);
                } catch (Exception e) {
                    runOnUiThread(() -> askPin(url, "❌ " + e.getMessage()));
                }
            }).start();
        });
        list.addView(ok);
    }

    private void showDevices(String url, JSONArray devices) {
        Api.prefs(this).edit().putString("server", url).putString("server_ok", url).apply();
        ShellyLinkWidget.updateAll(this);
        if (devices.length() == 0) {
            status.setText("Connecté, mais aucune prise : ajoute-la d'abord dans l'appli web.");
            return;
        }
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            status.setText("✅ Connecté au Pi (" + devices.length() + " prise(s)). "
                    + "Ajoute maintenant le widget depuis l'écran d'accueil.");
            for (int i = 0; i < devices.length(); i++) {
                TextView t = new TextView(this);
                t.setText("• " + devices.optJSONObject(i).optString("name"));
                list.addView(t);
            }
            ShellyWidget.scheduleAlarm(this);
            for (int id : ShellyWidget.allIds(this)) refreshLater(id);
            return;
        }
        status.setText("Touche la prise à afficher :");
        for (int i = 0; i < devices.length(); i++) {
            JSONObject d = devices.optJSONObject(i);
            Button b = new Button(this);
            b.setText(d.optString("name") + "  ·  " + d.optString("host"));
            b.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
            final String id = d.optString("id");
            b.setOnClickListener(v -> choose(id));
            list.addView(b);
        }
    }

    private void showAddresses(JSONObject info) {
        if (info == null) return;
        StringBuilder sb = new StringBuilder("\n\nAdresses du Pi trouvées :");
        JSONArray lan = info.optJSONArray("lan"), ts = info.optJSONArray("tailscale");
        for (int i = 0; lan != null && i < lan.length(); i++) sb.append("\n🏠 ").append(lan.optString(i)).append(" (maison)");
        for (int i = 0; ts != null && i < ts.length(); i++) sb.append("\n🌍 ").append(ts.optString(i)).append(" (Tailscale)");
        status.append(sb.toString());
    }

    private void choose(String deviceId) {
        Api.prefs(this).edit()
                .putString("device_" + widgetId, deviceId)
                .remove("on_" + widgetId)
                .remove("power_" + widgetId)
                .apply();
        ShellyWidget.scheduleAlarm(this);
        refreshLater(widgetId);
        setResult(RESULT_OK, new Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId));
        finish();
    }

    private void refreshLater(int id) {
        final android.content.Context app = getApplicationContext();
        new Thread(() -> ShellyWidget.refresh(app, id)).start();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
