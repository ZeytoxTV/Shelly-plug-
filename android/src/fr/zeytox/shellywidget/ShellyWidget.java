package fr.zeytox.shellywidget;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.SystemClock;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Widget 4×4 : état, puissance, énergie du jour, courbe 24 h, prochain horaire et bouton marche/arrêt.
 * Se rafraîchit toutes les ~15 min (alarme inexacte, économe en batterie), après chaque action,
 * et à la demande avec ↻.
 */
public class ShellyWidget extends AppWidgetProvider {
    static final String ACTION_TOGGLE = "fr.zeytox.shellywidget.TOGGLE";
    static final String ACTION_REFRESH = "fr.zeytox.shellywidget.REFRESH";
    static final String ACTION_REFRESH_ALL = "fr.zeytox.shellywidget.REFRESH_ALL";
    /** Au-dessus de cette puissance, éteindre demande un 2e appui (évite de couper le PC par erreur). */
    static final double CONFIRM_ABOVE_W = 30;
    static final long CONFIRM_WINDOW_MS = 5000;

    @Override
    public void onUpdate(Context ctx, AppWidgetManager mgr, int[] ids) {
        scheduleAlarm(ctx);
        refreshAsync(ctx, ids);
    }

    @Override
    public void onEnabled(Context ctx) {
        scheduleAlarm(ctx);
    }

    @Override
    public void onDisabled(Context ctx) {
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        am.cancel(broadcast(ctx, ACTION_REFRESH_ALL, 0));
    }

    @Override
    public void onDeleted(Context ctx, int[] ids) {
        android.content.SharedPreferences.Editor e = Api.prefs(ctx).edit();
        for (int id : ids) {
            e.remove("device_" + id).remove("on_" + id).remove("power_" + id).remove("confirm_" + id);
        }
        e.apply();
    }

    @Override
    public void onReceive(Context ctx, Intent intent) {
        super.onReceive(ctx, intent);
        String action = intent.getAction();
        int id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        if (ACTION_REFRESH_ALL.equals(action)) {
            refreshAsync(ctx, allIds(ctx));
        } else if (ACTION_REFRESH.equals(action) && id != AppWidgetManager.INVALID_APPWIDGET_ID) {
            showBusy(ctx, id, null);
            refreshAsync(ctx, new int[] {id});
        } else if (ACTION_TOGGLE.equals(action) && id != AppWidgetManager.INVALID_APPWIDGET_ID) {
            toggle(ctx, id);
        }
    }

    // --- Actions -----------------------------------------------------------

    private void toggle(Context ctx, int id) {
        final String device = Api.deviceFor(ctx, id);
        if (device == null) return;
        android.content.SharedPreferences p = Api.prefs(ctx);
        final boolean wasOn = p.getBoolean("on_" + id, false);
        final boolean known = p.contains("on_" + id);
        double power = p.getFloat("power_" + id, 0f);
        long now = System.currentTimeMillis();

        // Éteindre un appareil en train de consommer : demande confirmation
        if (known && wasOn && power >= CONFIRM_ABOVE_W && now > p.getLong("confirm_" + id, 0)) {
            p.edit().putLong("confirm_" + id, now + CONFIRM_WINDOW_MS).apply();
            RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget);
            v.setTextViewText(R.id.toggle, "Appuie encore pour couper (" + Math.round(power) + " W)");
            v.setInt(R.id.toggle, "setBackgroundResource", R.drawable.btn_warn);
            AppWidgetManager.getInstance(ctx).partiallyUpdateAppWidget(id, v);
            // Sans 2e appui, le bouton revient à la normale après le délai
            final PendingResult pr = goAsync();
            final Context app = ctx.getApplicationContext();
            new Thread(() -> {
                SystemClock.sleep(CONFIRM_WINDOW_MS + 200);
                if (System.currentTimeMillis() > Api.prefs(app).getLong("confirm_" + id, 0)) refresh(app, id);
                pr.finish();
            }).start();
            return;
        }
        p.edit().remove("confirm_" + id).apply();

        final String action = !known ? "toggle" : (wasOn ? "off" : "on");
        showBusy(ctx, id, wasOn ? "Extinction…" : "Allumage…");
        final PendingResult pending = goAsync();
        final Context app = ctx.getApplicationContext();
        new Thread(() -> {
            try {
                Api.switchTo(app, device, action);
            } catch (Exception e) {
                showError(app, id, e.getMessage());
            }
            refresh(app, id);
            pending.finish();
        }).start();
    }

    private void refreshAsync(Context ctx, int[] ids) {
        final PendingResult pending = goAsync();
        final Context app = ctx.getApplicationContext();
        new Thread(() -> {
            for (int id : ids) refresh(app, id);
            pending.finish();
        }).start();
    }

    // --- Rendu -------------------------------------------------------------

    static void refresh(Context ctx, int id) {
        String device = Api.deviceFor(ctx, id);
        AppWidgetManager mgr = AppWidgetManager.getInstance(ctx);
        RemoteViews v = baseViews(ctx, id);
        if (device == null) {
            v.setTextViewText(R.id.name, "Widget non configuré");
            v.setTextViewText(R.id.sub, "Appui long → Paramètres pour choisir la prise");
            mgr.updateAppWidget(id, v);
            return;
        }
        try {
            JSONObject w = Api.widget(ctx, device);
            render(ctx, id, v, w);
        } catch (Exception e) {
            v.setTextViewText(R.id.dot, "●");
            v.setTextColor(R.id.dot, 0xFFDC2626);
            v.setTextViewText(R.id.power, "—");
            v.setTextViewText(R.id.sub, "Pi injoignable · Tailscale activé ?");
            v.setTextViewText(R.id.toggle, "Réessayer");
            v.setInt(R.id.toggle, "setBackgroundResource", R.drawable.btn_off);
            v.setOnClickPendingIntent(R.id.toggle, broadcastFor(ctx, ACTION_REFRESH, id));
        }
        mgr.updateAppWidget(id, v);
    }

    private static void render(Context ctx, int id, RemoteViews v, JSONObject w) {
        v.setTextViewText(R.id.name, w.optString("name", "Prise"));
        boolean online = w.optBoolean("online", true);
        String updated = new SimpleDateFormat("HH:mm", Locale.FRANCE).format(new Date());

        if (!online) {
            v.setTextColor(R.id.dot, 0xFFDC2626);
            v.setTextViewText(R.id.power, "—");
            v.setTextViewText(R.id.sub, "Prise hors ligne · " + updated);
            v.setTextViewText(R.id.toggle, "Réessayer");
            v.setInt(R.id.toggle, "setBackgroundResource", R.drawable.btn_off);
            v.setOnClickPendingIntent(R.id.toggle, broadcastFor(ctx, ACTION_REFRESH, id));
        } else {
            boolean on = w.optBoolean("on", false);
            double power = w.isNull("power") ? 0 : w.optDouble("power", 0);
            Api.prefs(ctx).edit()
                    .putBoolean("on_" + id, on)
                    .putFloat("power_" + id, (float) power)
                    .apply();
            v.setTextColor(R.id.dot, on ? 0xFF22C55E : 0xFF6B7280);
            v.setTextViewText(R.id.power, w.isNull("power") ? "— W" : Math.round(power) + " W");
            String today = w.isNull("today_wh") ? "—" : energy(w.optDouble("today_wh", 0));
            v.setTextViewText(R.id.sub, (on ? "Allumée" : "Éteinte") + " · aujourd'hui " + today + " · " + updated);
            v.setTextViewText(R.id.toggle, on ? "Éteindre" : "Allumer");
            v.setInt(R.id.toggle, "setBackgroundResource", on ? R.drawable.btn_off : R.drawable.btn_on);
        }

        JSONObject pending = w.optJSONObject("pending");
        JSONObject next = w.optJSONObject("next");
        String info;
        if (pending != null) {
            info = "⏸ Arrêt de " + pending.optString("rule_time") + " reporté : appareil utilisé";
        } else if (next != null) {
            int days = next.optInt("in_days", 0);
            String when = days == 0 ? "aujourd'hui" : days == 1 ? "demain" : next.optString("day");
            info = "Prochain : " + ("on".equals(next.optString("action")) ? "allumer" : "éteindre")
                    + " " + when + " à " + next.optString("time");
        } else {
            info = "Aucun horaire prévu";
        }
        v.setTextViewText(R.id.info, info);
        v.setImageViewBitmap(R.id.chart, Spark.draw(w.optJSONArray("spark")));
    }

    private static RemoteViews baseViews(Context ctx, int id) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget);
        v.setOnClickPendingIntent(R.id.toggle, broadcastFor(ctx, ACTION_TOGGLE, id));
        v.setOnClickPendingIntent(R.id.refresh, broadcastFor(ctx, ACTION_REFRESH, id));
        Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(Api.server(ctx) + "/"));
        v.setOnClickPendingIntent(R.id.chart, PendingIntent.getActivity(ctx, id, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        return v;
    }

    private static void showBusy(Context ctx, int id, String label) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget);
        if (label != null) v.setTextViewText(R.id.toggle, label);
        v.setTextViewText(R.id.refresh, "…");
        AppWidgetManager.getInstance(ctx).partiallyUpdateAppWidget(id, v);
    }

    private static void showError(Context ctx, int id, String message) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget);
        v.setTextViewText(R.id.info, "⚠ " + (message == null ? "Erreur" : message));
        AppWidgetManager.getInstance(ctx).partiallyUpdateAppWidget(id, v);
    }

    static String energy(double wh) {
        if (wh >= 1000) return String.format(Locale.FRANCE, "%.2f kWh", wh / 1000);
        return String.format(Locale.FRANCE, "%.0f Wh", wh);
    }

    // --- Intents & alarme ------------------------------------------------

    static int[] allIds(Context ctx) {
        return AppWidgetManager.getInstance(ctx).getAppWidgetIds(new ComponentName(ctx, ShellyWidget.class));
    }

    private static PendingIntent broadcastFor(Context ctx, String action, int id) {
        return broadcast(ctx, action, id);
    }

    private static PendingIntent broadcast(Context ctx, String action, int id) {
        Intent i = new Intent(ctx, ShellyWidget.class).setAction(action);
        i.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id);
        // Code de requête distinct par action et par widget
        int code = id * 4 + (ACTION_TOGGLE.equals(action) ? 1 : ACTION_REFRESH.equals(action) ? 2 : 3);
        return PendingIntent.getBroadcast(ctx, code, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static void scheduleAlarm(Context ctx) {
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        am.setInexactRepeating(AlarmManager.ELAPSED_REALTIME,
                SystemClock.elapsedRealtime() + AlarmManager.INTERVAL_FIFTEEN_MINUTES,
                AlarmManager.INTERVAL_FIFTEEN_MINUTES,
                broadcast(ctx, ACTION_REFRESH_ALL, 0));
    }
}
