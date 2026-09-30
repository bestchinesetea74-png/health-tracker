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
import android.graphics.Path;
import android.graphics.PathMeasure;
import android.graphics.RectF;
import android.graphics.Typeface;
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
 * Виджет "Вода".
 *
 * Слева 2/3 ширины — четыре одинаковые кнопки (-50 / +50 объём стакана,
 * - / + стакан воды), поровну распределённые по этому пространству.
 * Справа 1/3 — капсула ("stadium"): по её контуру бежит дуга прогресса
 * к дневной цели, а внутри капсулы — цифры "выпито / цель".
 *
 * Кнопки и капсула рисуются через Canvas в отдельные Bitmap (а не через
 * setColorFilter поверх общей белой фигуры), поэтому цвет текста и фона
 * всегда подбираются под тему явно и не бьются друг с другом на "тёмных"
 * темах — раньше именно смешивание двух одинаковых белых цветов гасило
 * подписи на кнопках в Dark/AMOLED.
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
    // Состояние и тема
    // ------------------------------------------------------------------

    private static final class State {
        int water = 0;
        int glass = 250;
        int target = 2000;
        String palette = "teal";
        String theme = "Dark";
        String accentHex = ""; // реальный theme_cls.primaryColor из приложения, "#RRGGBB"
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
                    s.accentHex = settings.optString("widget_accent_hex", "");
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

    /** Предпочитаем реальный цвет, который приложение сохранило из
     *  theme_cls.primaryColor (см. widget_accent_hex в settings) — так
     *  оттенок в виджете гарантированно совпадает с приложением, вместо
     *  приблизительной таблицы по имени палитры. Таблица остаётся только
     *  как запасной вариант на случай самого первого запуска, когда
     *  приложение ещё ни разу не сохраняло базу. */
    private static int resolveAccent(String accentHex, String palette) {
        if (accentHex != null && accentHex.length() >= 7 && accentHex.charAt(0) == '#') {
            try {
                return 0xFF000000 | (Integer.parseInt(accentHex.substring(1, 7), 16) & 0xFFFFFF);
            } catch (NumberFormatException ignored) {
                // упадём на запасной вариант ниже
            }
        }
        return accentColor(palette);
    }

    /** Цвета, зависящие от темы. Подбираются так, чтобы фон и текст ВСЕГДА
     *  были контрастны — никакого смешивания "белого с белым". */
    private static final class Theme {
        int cardBg;
        int neutralBtnBg;   // полупрозрачный, накладывается поверх cardBg
        int neutralBtnText;
        int primaryBtnText; // текст на акцентной (+вода) кнопке — всегда белый
        int trackColor;     // фон дорожки прогресса в капсуле
        int textMain;
        int textSub;
    }

    private static Theme themeFor(String themeName) {
        Theme t = new Theme();
        boolean light = "Light".equals(themeName);
        boolean amoled = "AMOLED".equals(themeName);
        t.primaryBtnText = 0xFFFFFFFF;
        if (light) {
            t.cardBg = 0xF2F2F2F2;
            t.neutralBtnBg = 0x14000000;   // ~8% чёрного поверх светлой карточки
            t.neutralBtnText = 0xFF1B1B1B;
            t.trackColor = 0x1F000000;
            t.textMain = 0xFF1B1B1B;
            t.textSub = 0xFF555555;
        } else {
            t.cardBg = amoled ? 0xF2000000 : 0xE61E1E1E;
            t.neutralBtnBg = 0x33FFFFFF;   // ~20% белого — теперь рисуется на
                                             // ПРОЗРАЧНОМ bitmap, а не поверх
                                             // белой фигуры, поэтому реально
                                             // полупрозрачный, а не "белый на белом"
            t.neutralBtnText = 0xFFFFFFFF;
            t.trackColor = 0x33FFFFFF;
            t.textMain = 0xFFFFFFFF;
            t.textSub = 0xFFBBBBBB;
        }
        return t;
    }

    // ------------------------------------------------------------------
    // Отрисовка: кнопки
    // ------------------------------------------------------------------

    /** Рисует одну кнопку (скруглённый прямоугольник + подпись) в bitmap.
     *  bgColor/textColor приходят уже готовыми под тему, так что кнопка
     *  всегда читаема. */
    private static Bitmap buttonBitmap(String label, int bgColor, int textColor, float density) {
        // Квадратная форма: с прямоугольным bitmap (было 64x76) кнопка
        // никогда не выглядела квадратной при scaleType="fitCenter",
        // какой бы ни была реальная ячейка — форма всегда повторяла
        // пропорции самой картинки.
        int w = Math.round(62 * density);
        int h = Math.round(62 * density);
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bmp);

        Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
        bg.setColor(bgColor);
        float r = Math.min(w, h) * 0.30f;
        cv.drawRoundRect(new RectF(0, 0, w, h), r, r, bg);

        Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        text.setColor(textColor);
        text.setTypeface(Typeface.DEFAULT);
        text.setTextAlign(Paint.Align.CENTER);
        text.setTextSize(h * 0.22f);
        float maxLabelWidth = w * 0.82f;
        fitTextSize(text, label, maxLabelWidth, 7f * density);
        Paint.FontMetrics fm = text.getFontMetrics();
        float cy = h / 2f - (fm.ascent + fm.descent) / 2f;
        cv.drawText(label, w / 2f, cy, text);

        return bmp;
    }

    // ------------------------------------------------------------------
    // Отрисовка: капсула-индикатор ("stadium")
    // ------------------------------------------------------------------

    /** Уменьшает textSize у paint, пока строка не влезет в maxWidth.
     *  Нужно, потому что капсула теперь узкая, а числа "выпито/цель" могут
     *  быть как двух-, так и четырёхзначными в зависимости от настроек
     *  пользователя. */
    private static void fitTextSize(Paint paint, String text, float maxWidth, float minSize) {
        float size = paint.getTextSize();
        while (size > minSize && paint.measureText(text) > maxWidth) {
            size -= 1f;
            paint.setTextSize(size);
        }
    }

    /** Капсула с дугой прогресса по контуру и цифрами "выпито/цель" внутри. */
    private static Bitmap stadiumBitmap(int water, int target, int accent, int trackColor,
                                         int textMain, float density) {
        // Табло с целью — это, по сути, пятая кнопка (тоже открывает
        // приложение по тапу), поэтому у неё теперь ровно тот же размер,
        // что и у остальных (62dp). На квадратном holste радиус в половину
        // стороны сам по себе даёт идеальный круг — отдельная "stadium"
        // форма (вытянутый овал) больше не нужна.
        int w = Math.round(62 * density);
        int h = Math.round(62 * density);
        float stroke = 3f * density;

        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bmp);

        float inset = stroke / 2f + 0.5f * density;
        RectF rect = new RectF(inset, inset, w - inset, h - inset);
        float radius = Math.min(rect.width(), rect.height()) / 2f; // на квадрате = круг

        Path full = new Path();
        full.addRoundRect(rect, radius, radius, Path.Direction.CW);

        Paint track = new Paint(Paint.ANTI_ALIAS_FLAG);
        track.setStyle(Paint.Style.STROKE);
        track.setStrokeWidth(stroke);
        track.setStrokeCap(Paint.Cap.ROUND);
        track.setColor(trackColor);
        cv.drawPath(full, track);

        float fraction = target > 0 ? Math.max(0f, Math.min(1f, (float) water / target)) : 0f;
        boolean done = water >= target;
        if (fraction > 0.003f) {
            PathMeasure pm = new PathMeasure(full, true);
            float len = pm.getLength();
            Path progress = new Path();
            pm.getSegment(0, len * fraction, progress, true);
            // Известный обходной путь: без rLineTo(0,0) сегмент от
            // PathMeasure иногда не отрисовывается на некоторых прошивках.
            progress.rLineTo(0, 0);

            Paint progPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
            progPaint.setStyle(Paint.Style.STROKE);
            progPaint.setStrokeWidth(stroke);
            progPaint.setStrokeCap(Paint.Cap.ROUND);
            progPaint.setColor(done ? 0xFF2ECC71 : accent);
            cv.drawPath(progress, progPaint);
        }

        // Цифры одной строкой: "выпито/цель". Шрифт без жирности — как и
        // на остальных кнопках.
        float maxTextWidth = w - stroke * 3.4f; // не залезать на дорожку прогресса

        String combined = water + "/" + target;
        Paint main = new Paint(Paint.ANTI_ALIAS_FLAG);
        main.setColor(textMain);
        main.setTypeface(Typeface.DEFAULT);
        main.setTextAlign(Paint.Align.CENTER);
        main.setTextSize(h * 0.26f);
        fitTextSize(main, combined, maxTextWidth, 7f * density);

        float cx = w / 2f;
        Paint.FontMetrics fm = main.getFontMetrics();
        float cy = h / 2f - (fm.ascent + fm.descent) / 2f;
        cv.drawText(combined, cx, cy, main);

        return bmp;
    }

    // ------------------------------------------------------------------
    // Сборка RemoteViews
    // ------------------------------------------------------------------

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
        Theme th = themeFor(s.theme);
        int accent = resolveAccent(s.accentHex, s.palette);
        float density = c.getResources().getDisplayMetrics().density;

        RemoteViews rv = new RemoteViews(c.getPackageName(), R.layout.widget_water);
        rv.setInt(R.id.bg, "setColorFilter", th.cardBg);

        if (!s.ok) {
            // Файл базы повреждён/недоступен: не рискуем рисовать случайные
            // числа, показываем максимально нейтральный минимум.
            s.water = 0;
        }

        rv.setImageViewBitmap(R.id.btn_glass_minus_img,
                buttonBitmap("\u221250", th.neutralBtnBg, th.neutralBtnText, density));
        rv.setImageViewBitmap(R.id.btn_glass_plus_img,
                buttonBitmap("+50", th.neutralBtnBg, th.neutralBtnText, density));
        rv.setImageViewBitmap(R.id.btn_water_minus_img,
                buttonBitmap("\u2212" + s.glass, th.neutralBtnBg, th.neutralBtnText, density));
        rv.setImageViewBitmap(R.id.btn_water_plus_img,
                buttonBitmap("+" + s.glass, th.neutralBtnBg, th.neutralBtnText, density));

        rv.setImageViewBitmap(R.id.stadium_img,
                stadiumBitmap(s.water, s.target, accent, th.trackColor, th.textMain, density));

        rv.setOnClickPendingIntent(R.id.btn_glass_minus_img, actionIntent(c, ACTION_GLASS_MINUS, 1));
        rv.setOnClickPendingIntent(R.id.btn_glass_plus_img, actionIntent(c, ACTION_GLASS_PLUS, 2));
        rv.setOnClickPendingIntent(R.id.btn_water_minus_img, actionIntent(c, ACTION_WATER_MINUS, 3));
        rv.setOnClickPendingIntent(R.id.btn_water_plus_img, actionIntent(c, ACTION_WATER_PLUS, 4));

        // Тап по капсуле открывает приложение
        Intent launch = c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());
        if (launch != null) {
            PendingIntent open = PendingIntent.getActivity(c, 5, launch, pendingFlags());
            rv.setOnClickPendingIntent(R.id.stadium_img, open);
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
