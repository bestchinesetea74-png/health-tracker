package org.vladimir.healthtracker;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Build;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

/**
 * Виджет "Вода": показывает выпитое за сегодня, объём стакана и прогресс
 * к дневной цели. Кнопки: -50 / +50 (объём стакана), - / + (стаканы).
 *
 * Данные читаются и пишутся прямо в tracker_history.json (тот же файл, что
 * и у Python-приложения). Виджет меняет ТОЛЬКО:
 *   - <сегодня>.water_ml
 *   - settings.glass_volume
 * Все остальные ключи файла сохраняются как есть.
 */
public class WaterWidgetProvider extends AppWidgetProvider {

    static final String ACTION_GLASS_MINUS = "org.vladimir.healthtracker.WIDGET_GLASS_MINUS";
    static final String ACTION_GLASS_PLUS = "org.vladimir.healthtracker.WIDGET_GLASS_PLUS";
    static final String ACTION_WATER_MINUS = "org.vladimir.healthtracker.WIDGET_WATER_MINUS";
    static final String ACTION_WATER_PLUS = "org.vladimir.healthtracker.WIDGET_WATER_PLUS";
    static final String ACTION_REFRESH = "org.vladimir.healthtracker.WIDGET_REFRESH";
    static final String ACTION_MIDNIGHT = "org.vladimir.healthtracker.WIDGET_MIDNIGHT";

    private static final String FILE_NAME = "tracker_history.json";
    private static final int GLASS_STEP = 50;
    private static final int GLASS_MIN = 50;
    private static final Object LOCK = new Object();

    // ------------------------------------------------------------------
    // Жизненный цикл виджета
    // ------------------------------------------------------------------

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        updateAll(context);
    }

    @Override
    public void onEnabled(Context context) {
        scheduleMidnight(context);
    }

    @Override
    public void onDisabled(Context context) {
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am != null) {
            am.cancel(midnightIntent(context));
        }
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (ACTION_GLASS_MINUS.equals(action) || ACTION_GLASS_PLUS.equals(action)
                || ACTION_WATER_MINUS.equals(action) || ACTION_WATER_PLUS.equals(action)) {
            try {
                applyAction(context, action);
            } catch (Exception e) {
                // Файл повреждён или недоступен: ничего не пишем, чтобы не
                // затереть данные пользователя.
                android.util.Log.e("WaterWidget", "action failed: " + e);
            }
            updateAll(context);
        } else if (ACTION_REFRESH.equals(action) || ACTION_MIDNIGHT.equals(action)) {
            updateAll(context);
        } else {
            super.onReceive(context, intent);
        }
    }

    // ------------------------------------------------------------------
    // Работа с данными
    // ------------------------------------------------------------------

    private static File dataFile(Context c) {
        return new File(c.getFilesDir(), FILE_NAME);
    }

    private static String todayKey() {
        return new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
    }

    /** Возвращает содержимое файла как JSON. Нет файла -> пустой объект.
     *  Битый файл -> исключение (вызывающий код ничего не перезаписывает). */
    private static JSONObject readRoot(Context c) throws Exception {
        File f = dataFile(c);
        if (!f.exists() || f.length() == 0) {
            return new JSONObject();
        }
        byte[] buf = new byte[(int) f.length()];
        FileInputStream in = new FileInputStream(f);
        try {
            int off = 0;
            while (off < buf.length) {
                int n = in.read(buf, off, buf.length - off);
                if (n < 0) break;
                off += n;
            }
        } finally {
            in.close();
        }
        return new JSONObject(new String(buf, "UTF-8"));
    }

    private static void writeAtomic(File target, String content) throws IOException {
        File tmp = new File(target.getParentFile(), FILE_NAME + ".widget.tmp");
        FileOutputStream out = new FileOutputStream(tmp);
        try {
            out.write(content.getBytes("UTF-8"));
            out.getFD().sync();
        } finally {
            out.close();
        }
        if (!tmp.renameTo(target)) {
            throw new IOException("rename failed");
        }
    }

    private static void applyAction(Context c, String action) throws Exception {
        synchronized (LOCK) {
            JSONObject root = readRoot(c);

            JSONObject settings = root.optJSONObject("settings");
            if (settings == null) settings = new JSONObject();
            int glass = settings.optInt("glass_volume", 250);

            String today = todayKey();
            JSONObject day = root.optJSONObject(today);
            if (day == null) day = new JSONObject();
            int water = day.optInt("water_ml", 0);

            if (ACTION_GLASS_MINUS.equals(action)) {
                glass = Math.max(GLASS_MIN, glass - GLASS_STEP);
                settings.put("glass_volume", glass);
                root.put("settings", settings);
            } else if (ACTION_GLASS_PLUS.equals(action)) {
                glass = glass + GLASS_STEP;
                settings.put("glass_volume", glass);
                root.put("settings", settings);
            } else if (ACTION_WATER_MINUS.equals(action)) {
                water = Math.max(0, water - glass);
                day.put("water_ml", water);
                root.put(today, day);
            } else if (ACTION_WATER_PLUS.equals(action)) {
                water = water + glass;
                day.put("water_ml", water);
                root.put(today, day);
            }

            writeAtomic(dataFile(c), root.toString(2));
        }
    }

    // ------------------------------------------------------------------
    // Отрисовка
    // ------------------------------------------------------------------

    private static final class State {
        int water = 0;
        int glass = 250;
        int target = 2000;
        String palette = "teal";
        String theme = "Dark";
        boolean ok = true;
    }

    private static State readState(Context c) {
        State s = new State();
        try {
            synchronized (LOCK) {
                JSONObject root = readRoot(c);
                JSONObject settings = root.optJSONObject("settings");
                if (settings != null) {
                    s.glass = settings.optInt("glass_volume", 250);
                    s.target = settings.optInt("water_target_val", 2000);
                    s.palette = settings.optString("app_palette", "teal");
                    s.theme = settings.optString("app_theme_style", "Dark");
                }
                JSONObject day = root.optJSONObject(todayKey());
                if (day != null) {
                    s.water = day.optInt("water_ml", 0);
                }
            }
        } catch (Exception e) {
            s.ok = false;
        }
        if (s.target <= 0) s.target = 2000;
        return s;
    }

    private static int accentColor(String palette) {
        if ("indigo".equals(palette)) return 0xFF3F51B5;
        if ("blue".equals(palette)) return 0xFF2196F3;
        if ("orange".equals(palette)) return 0xFFFF9800;
        if ("purple".equals(palette)) return 0xFF9C27B0;
        if ("red".equals(palette)) return 0xFFF44336;
        return 0xFF009688; // teal
    }

    private static Bitmap progressBitmap(float fraction, int trackColor, int fillColor) {
        int w = 400, h = 12;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bmp);
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        float r = h / 2f;
        p.setColor(trackColor);
        cv.drawRoundRect(new RectF(0, 0, w, h), r, r, p);
        float fw = Math.max(0f, Math.min(1f, fraction)) * w;
        if (fw > 0) {
            p.setColor(fillColor);
            cv.drawRoundRect(new RectF(0, 0, Math.max(fw, h), h), r, r, p);
        }
        return bmp;
    }

    private static int pendingFlags() {
        int f = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) f |= PendingIntent.FLAG_IMMUTABLE;
        return f;
    }

    private static PendingIntent actionIntent(Context c, String action, int requestCode) {
        Intent i = new Intent(c, WaterWidgetProvider.class);
        i.setAction(action);
        return PendingIntent.getBroadcast(c, requestCode, i, pendingFlags());
    }

    private static PendingIntent midnightIntent(Context c) {
        return actionIntent(c, ACTION_MIDNIGHT, 99);
    }

    private static void scheduleMidnight(Context c) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        Calendar cal = Calendar.getInstance();
        cal.add(Calendar.DAY_OF_YEAR, 1);
        cal.set(Calendar.HOUR_OF_DAY, 0);
        cal.set(Calendar.MINUTE, 0);
        cal.set(Calendar.SECOND, 5);
        cal.set(Calendar.MILLISECOND, 0);
        // Неточный будильник: разрешений не требует, может чуть сдвигаться.
        am.set(AlarmManager.RTC, cal.getTimeInMillis(), midnightIntent(c));
    }

    private static RemoteViews buildViews(Context c) {
        State s = readState(c);
        RemoteViews rv = new RemoteViews(c.getPackageName(), R.layout.widget_water);

        boolean light = "Light".equals(s.theme);
        boolean amoled = "AMOLED".equals(s.theme);
        int bg = light ? 0xF2F2F2F2 : (amoled ? 0xF2000000 : 0xE61E1E1E);
        int textMain = light ? 0xFF1B1B1B : 0xFFFFFFFF;
        int textSub = light ? 0xFF555555 : 0xFFBBBBBB;
        int btnNeutral = light ? 0x22000000 : 0x33FFFFFF;
        int track = light ? 0x22000000 : 0x33FFFFFF;
        int accent = accentColor(s.palette);
        boolean done = s.water >= s.target;
        int fill = done ? 0xFF2ECC71 : accent;

        rv.setInt(R.id.bg, "setColorFilter", bg);

        if (s.ok) {
            rv.setTextViewText(R.id.water_text, s.water + " / " + s.target);
            rv.setTextViewText(R.id.glass_text, "мл, стакан " + s.glass);
            rv.setImageViewBitmap(R.id.progress,
                    progressBitmap((float) s.water / s.target, track, fill));
        } else {
            rv.setTextViewText(R.id.water_text, "—");
            rv.setTextViewText(R.id.glass_text, "нет данных");
            rv.setImageViewBitmap(R.id.progress, progressBitmap(0f, track, fill));
        }
        rv.setTextColor(R.id.water_text, textMain);
        rv.setTextColor(R.id.glass_text, textSub);

        // Кнопки: подписи и цвета
        rv.setTextViewText(R.id.btn_water_minus_txt, "\u2212" + s.glass);
        rv.setTextViewText(R.id.btn_water_plus_txt, "+" + s.glass);
        rv.setTextColor(R.id.btn_glass_minus_txt, textMain);
        rv.setTextColor(R.id.btn_glass_plus_txt, textMain);
        rv.setTextColor(R.id.btn_water_minus_txt, textMain);
        rv.setTextColor(R.id.btn_water_plus_txt, 0xFFFFFFFF);

        rv.setInt(R.id.btn_glass_minus_bg, "setColorFilter", btnNeutral);
        rv.setInt(R.id.btn_glass_plus_bg, "setColorFilter", btnNeutral);
        rv.setInt(R.id.btn_water_minus_bg, "setColorFilter", btnNeutral);
        rv.setInt(R.id.btn_water_plus_bg, "setColorFilter", accent);

        rv.setOnClickPendingIntent(R.id.btn_glass_minus, actionIntent(c, ACTION_GLASS_MINUS, 1));
        rv.setOnClickPendingIntent(R.id.btn_glass_plus, actionIntent(c, ACTION_GLASS_PLUS, 2));
        rv.setOnClickPendingIntent(R.id.btn_water_minus, actionIntent(c, ACTION_WATER_MINUS, 3));
        rv.setOnClickPendingIntent(R.id.btn_water_plus, actionIntent(c, ACTION_WATER_PLUS, 4));

        // Тап по тексту слева открывает приложение
        Intent launch = c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());
        if (launch != null) {
            PendingIntent open = PendingIntent.getActivity(c, 5, launch, pendingFlags());
            rv.setOnClickPendingIntent(R.id.info, open);
        }
        return rv;
    }

    static void updateAll(Context c) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(c);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(c, WaterWidgetProvider.class));
        if (ids == null || ids.length == 0) return;
        RemoteViews rv = buildViews(c);
        for (int id : ids) {
            mgr.updateAppWidget(id, rv);
        }
        scheduleMidnight(c);
    }
}
