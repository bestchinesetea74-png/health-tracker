[app]

# (str) Title of your application
title = Трекер здоровья

# (str) Package name
package.name = healthtracker

# (str) Package domain (needed for android packaging)
package.domain = org.vladimir

# (list) Source files to include (let it include python files, icons, etc.)
source.dir = .
source.include_exts = py,png,jpg,json

# (str) Application versioning
version = 0.2.3
icon.filename = %(source.dir)s/icon.png

# (list) Application requirements
# python3 без явного пина версии — так python3 и hostpython3 у p4a
# гарантированно совпадают (пин на 3.11.9 вызывал ошибку
# "python3 should have same version as hostpython3, 3.11.9 != 3.14.2").
#
# materialyoucolor==3.0.3, materialshapes, pycairo, exceptiongroup,
# asyncgui, asynckivy — обязательные зависимости kivymd 2.0 согласно
# официальному README на PyPI. Версия materialyoucolor ЗАФИКСИРОВАНА
# явно: без номера версии pip подтягивал более старую сборку без
# модуля materialyoucolor.dynamiccolor.color_spec.
#
# google-api-python-client, google-auth, google-auth-oauthlib и
# cryptography — ПОЛНОСТЬЮ УБРАНЫ. Причина: google-auth при импорте
# безусловно тянет за собой google/auth/crypt/es.py (поддержка ES256),
# который требует cryptography — Rust-скомпилированный пакет. У p4a
# есть свой recipe для cryptography с зашитой версией (игнорирует наш
# пин в requirements), и он несовместим с Python 3.14: скомпилированный
# _rust.abi3.so ссылается на символы CPython C-API (PyExc_TypeError),
# убранные из стабильного ABI в 3.14 — падает с dlopen failed.
#
# Вместо этого используется персональная авторизация через официальный
# Authorization API из Google Play Services (см. PlayServicesDriveAuth
# в main.py, вызывается через pyjnius) + прямые REST-вызовы к Drive API v3
# через urllib. Сервисный аккаунт и самописный OAuth2 JWT-flow (rsa,
# pyasn1, pyasn1_modules) больше не нужны и убраны из requirements —
# каждый пользователь входит в свой собственный Google-аккаунт, данные
# хранятся в его личном appDataFolder.
requirements = python3,kivy,kivymd,materialyoucolor==3.0.3,materialshapes,pycairo,exceptiongroup,asyncgui,asynckivy,pillow,certifi

# (str) Supported orientations (landscape, sensor, portrait or all)
orientation = portrait

# (int) Fullscreen (1 = fullscreen, 0 = not fullscreen)
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET

# SDK and API fixes
android.api = 33
android.min_api = 21
android.build_tools_version = 33.0.2
android.accept_sdk_license = True
ndk = 25b

# Сборка только под arm64-v8a — критично важно.
# При сборке под несколько архитектур сразу (arm64-v8a + armeabi-v7a)
# python-for-android переиспользует общую папку venv между архитектурами
# без очистки, из-за чего pip внутри venv оказывается "битым"
# (ImportError: cannot import name 'BuildDependencyInstallError').
# Одна архитектура полностью убирает эту проблему.
android.archs = arm64-v8a
# --- виджет рабочего стола ---
android.add_src = widget/java
android.add_resources = widget/res
p4a.hook = p4a/hook.py

# --- авторизация через Google (Play Services Authorization API) ---
# Нужна для персональной синхронизации: каждый пользователь входит в свой
# Google-аккаунт, данные хранятся в его собственном скрытом appDataFolder,
# вместо общего сервисного аккаунта на всех.
android.enable_androidx = True
android.gradle_dependencies = com.google.android.gms:play-services-auth:22.0.0

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 1

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
