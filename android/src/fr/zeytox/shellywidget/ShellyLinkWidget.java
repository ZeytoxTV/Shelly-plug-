package fr.zeytox.shellywidget;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.widget.RemoteViews;

/** Raccourci 1×1 : ouvre l'appli web du Pi dans le navigateur. */
public class ShellyLinkWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context ctx, AppWidgetManager mgr, int[] ids) {
        for (int id : ids) mgr.updateAppWidget(id, views(ctx));
    }

    static RemoteViews views(Context ctx) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_link);
        Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(Api.server(ctx) + "/"));
        v.setOnClickPendingIntent(R.id.link, PendingIntent.getActivity(ctx, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        return v;
    }

    /** À appeler quand l'adresse du Pi change. */
    static void updateAll(Context ctx) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(ctx);
        for (int id : mgr.getAppWidgetIds(new ComponentName(ctx, ShellyLinkWidget.class))) {
            mgr.updateAppWidget(id, views(ctx));
        }
    }
}
