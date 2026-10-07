[app]

# (str) Title of your application
title = Трекер здоровья

# (str) Package name
package.name = healthtracker

# (str) Package domain (needed for android packaging)
# ВАЖНО: package.domain + package.name = org.vladimir.healthtracker.
# Это имя пакета нужно указать в Google Cloud Console (OAuth-клиент Android).
package.domain = org.vladimir

# (list) Source files to include (let it include python files, icons, etc.)
source.dir = .
source.include_exts = py,png,jpg,json

# (str) Application versioning
version = 0.3.0
icon.filename = %(source.dir)s/icon.png

# (list) Application requirements
# python3 без явного пина версии (python3 и hostpython3 у p4a должны совпадать).
# materialyoucolor==3.0.3, materialshapes, pycairo, exceptiongroup, asyncgui,
# asynckivy — обязательные зависимости kivymd 2.0.
# pyjnius нужен для вызовов Google Play Services (PlayServicesDriveAuth).
# certifi нужен для HTTPS-запросов к Drive API.
# rsa / pyasn1 / google-auth / cryptography НЕ нужны: авторизация идёт через
# Play Services, а запросы к Drive — через urllib.
requirements = python3,kivy,kivymd,materialyoucolor==3.0.3,materialshapes,pycairo,exceptiongroup,asyncgui,asynckivy,pillow,certifi,pyjnius

# (str) Supported orientations (landscape, sensor, portrait or all)
orientation = portrait

# (int) Fullscreen (1 = fullscreen, 0 = not fullscreen)
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET

# SDK and API
# API 34: свежие версии play-services-auth требуют compileSdk >= 34.
android.api = 34
android.min_api = 23
android.build_tools_version = 34.0.0
android.accept_sdk_license = True
ndk = 25b

# Сборка только под arm64-v8a (при нескольких архитектурах p4a ломает venv).
android.archs = arm64-v8a

# --- виджет рабочего стола ---
android.add_src = widget/java
android.add_resources = widget/res
p4a.hook = p4a/hook.py

# --- авторизация через Google (Play Services Authorization API) ---
# Каждый пользователь входит в свой Google-аккаунт, данные хранятся в его
# собственном скрытом appDataFolder на Google Drive.
# Если Gradle не найдёт эту версию — возьмите актуальную на
# https://maven.google.com (группа com.google.android.gms, play-services-auth).
android.enable_androidx = True
android.gradle_dependencies = com.google.android.gms:play-services-auth:21.3.0

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 1

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
