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
version = 0.1

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
# google-api-python-client, google-auth — для синхронизации с Google
# Drive через Service Account (google.oauth2.service_account.Credentials).
# cryptography — обязательная зависимость google-auth для подписи JWT
# при работе с Service Account (google/auth/crypt/es.py её импортирует
# напрямую); без неё падает ModuleNotFoundError при первом же импорте
# google.oauth2.service_account.
# google-auth-oauthlib НЕ нужен — в этой версии используется Service
# Account, а не интерактивный OAuth пользователя.
requirements = python3,kivy,kivymd,materialyoucolor==3.0.3,materialshapes,pycairo,exceptiongroup,asyncgui,asynckivy,pillow,google-api-python-client,google-auth,cryptography

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

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
