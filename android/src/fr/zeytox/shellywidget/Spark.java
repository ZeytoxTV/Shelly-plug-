package fr.zeytox.shellywidget;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;

import org.json.JSONArray;

/** Petite courbe de puissance sur 24 h, dessinée en bitmap pour le widget. */
final class Spark {
    private static final int W = 640, H = 200, PAD = 6;
    private static final int LINE = Color.parseColor("#1FAE55");
    private static final int BASE = Color.parseColor("#3A404C");

    private Spark() {}

    static Bitmap draw(JSONArray values) {
        Bitmap bmp = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bmp);
        int n = values == null ? 0 : values.length();

        Paint base = new Paint(Paint.ANTI_ALIAS_FLAG);
        base.setColor(BASE);
        base.setStrokeWidth(2f);
        canvas.drawLine(0, H - PAD, W, H - PAD, base);
        if (n < 2) return bmp;

        double max = 1;
        for (int i = 0; i < n; i++) {
            if (!values.isNull(i)) max = Math.max(max, values.optDouble(i, 0));
        }
        max *= 1.1;

        Paint line = new Paint(Paint.ANTI_ALIAS_FLAG);
        line.setColor(LINE);
        line.setStyle(Paint.Style.STROKE);
        line.setStrokeWidth(5f);
        line.setStrokeJoin(Paint.Join.ROUND);
        line.setStrokeCap(Paint.Cap.ROUND);
        Paint area = new Paint(Paint.ANTI_ALIAS_FLAG);
        area.setColor(LINE);
        area.setAlpha(46);
        area.setStyle(Paint.Style.FILL);

        float baseY = H - PAD;
        Path stroke = new Path(), fill = new Path();
        boolean open = false;
        float firstX = 0, lastX = 0;
        for (int i = 0; i < n; i++) {
            float x = (float) i / (n - 1) * W;
            if (values.isNull(i)) {
                if (open) {
                    fill.lineTo(lastX, baseY);
                    fill.lineTo(firstX, baseY);
                    fill.close();
                    open = false;
                }
                continue;
            }
            float y = (float) (baseY - values.optDouble(i, 0) / max * (H - 2 * PAD));
            if (!open) {
                stroke.moveTo(x, y);
                fill.moveTo(x, y);
                firstX = x;
                open = true;
            } else {
                stroke.lineTo(x, y);
                fill.lineTo(x, y);
            }
            lastX = x;
        }
        if (open) {
            fill.lineTo(lastX, baseY);
            fill.lineTo(firstX, baseY);
            fill.close();
        }
        canvas.drawPath(fill, area);
        canvas.drawPath(stroke, line);
        return bmp;
    }
}
