# -*- coding: utf-8 -*-
"""
p4a hook (buildozer: p4a.hook = p4a/hook.py).

buildozer.spec не умеет добавлять дочерние XML-теги (вроде <receiver>) внутрь
<application> через простые ключи: android.extra_manifest_application_arguments
вставляет только XML-АТРИБУТЫ на сам тег <application ...>, а
android.extra_manifest_xml пишет содержимое внутрь <manifest>, а не
<application> (и вдобавок известен баг с экранированием строк, см.
kivy/python-for-android issue #2905).

Поэтому регистрация AppWidgetProvider для виджета "Вода" делается patch'ем
уже сгенерированного AndroidManifest.xml перед сборкой APK. Хук идемпотентен:
при повторных сборках не добавляет receiver дважды.
"""
from pathlib import Path

# --- Обход недоступного download.savannah.gnu.org для recipe freetype ---
# python-for-android качает freetype с download.savannah.gnu.org, который
# по факту (подтверждено независимо чужим проектом в те же дни) бывает
# недоступен ЧАСАМИ с раннеров GitHub Actions — "Connection timed out"
# после всех повторных попыток, это не наша сеть и не блокировка IP.
# FreeType официально зеркалируется на SourceForge — подменяем url прямо
# на уже созданном объекте recipe (а не переписываем/наследуем класс
# целиком), чтобы не трогать логику сборки самой библиотеки.
# Код ниже выполняется СРАЗУ при импорте этого файла — то есть раньше,
# чем toolchain начинает что-либо скачивать (--hook передаётся и в
# команду create, не только в aab/apk).
try:
    from pythonforandroid.recipes.freetype import recipe as _freetype_recipe
    _OLD_URL = _freetype_recipe.url
    _freetype_recipe.url = (
        "https://downloads.sourceforge.net/project/freetype/freetype2/"
        "{version}/freetype-{version}.tar.gz"
    )
    print(f"[hook] freetype url patched: {_OLD_URL} -> {_freetype_recipe.url}")
except Exception as e:
    print(f"[hook] WARNING: could not patch freetype recipe url: {e}")

RECEIVER_XML = """
    <receiver
        android:name="org.vladimir.healthtracker.WaterWidgetProvider"
        android:exported="true"
        android:label="Вода">
        <intent-filter>
            <action android:name="android.appwidget.action.APPWIDGET_UPDATE" />
        </intent-filter>
        <meta-data
            android:name="android.appwidget.provider"
            android:resource="@xml/water_widget_info" />
    </receiver>
"""


def after_apk_build(toolchain):
    manifest_path = Path(toolchain._dist.dist_dir) / "src" / "main" / "AndroidManifest.xml"

    if not manifest_path.exists():
        print(f"[hook] AndroidManifest.xml not found at {manifest_path}, skipping.")
        return

    manifest = manifest_path.read_text(encoding="utf-8")

    if "WaterWidgetProvider" in manifest:
        print("[hook] WaterWidgetProvider receiver already present, skipping.")
        return

    if "</application>" not in manifest:
        print("[hook] WARNING: </application> tag not found, receiver NOT inserted!")
        return

    manifest = manifest.replace("</application>", RECEIVER_XML + "    </application>")
    manifest_path.write_text(manifest, encoding="utf-8")
    print("[hook] WaterWidgetProvider receiver inserted into AndroidManifest.xml")
