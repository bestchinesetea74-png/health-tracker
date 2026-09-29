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
        int accent = accentColor(s.palette);
        float density = c.getResources().getDisplayMetrics().density;
