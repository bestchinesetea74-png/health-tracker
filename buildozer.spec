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
version = 0.1.4

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
# Вместо этого используется САМОПИСНАЯ реализация OAuth2 Service Account
# JWT-flow на чистом Python (см. ServiceAccountJWTAuth в main.py) +
# прямые REST-вызовы к Drive API v3 через urllib. Единственные
# зависимости для этого — rsa, pyasn1, pyasn1_modules: все три являются
# чистыми Python-пакетами (py3-none-any.whl) БЕЗ единой строчки C/Rust
# кода, поэтому полностью исключают весь класс проблем с ABI
# несовместимостью компилируемых расширений под Python 3.14.
requirements = python3,kivy,kivymd,materialyoucolor==3.0.3,materialshapes,pycairo,exceptiongroup,asyncgui,asynckivy,pillow,rsa,pyasn1,pyasn1_modules

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
log_level = 1

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
