package fr.zeytox.shellywidget;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/**
 * Ouvre l'appli web sur l'adresse du Pi qui répond (maison ou Tailscale).
 * Activité transparente : elle teste les adresses en arrière-plan puis se ferme.
 */
public class LinkActivity extends Activity {
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        final android.content.Context app = getApplicationContext();
        new Thread(() -> {
            String base = Api.reachableServer(app);
            runOnUiThread(() -> {
                Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(base + "/"));
                open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                try {
                    startActivity(open);
                } catch (Exception ignored) {
                }
                finish();
                overridePendingTransition(0, 0);
            });
        }).start();
    }
}
