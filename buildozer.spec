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
requirements = python3,kivy,pillow,google-api-python-client,google-auth,google-auth-oauthlib

# (str) Supported orientations (landscape, sensor, portrait or all)
orientation = portrait

# (int) Fullscreen (1 = fullscreen, 0 = not fullscreen)
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1

# Указываем стабильную версию Android API
android.api = 33

# Минимальная поддерживаемая версия Android
android.min_api = 21

# Жестко фиксируем проверенную версию build-tools, чтобы avoid проблемы с 'Aidl not found'
android.build_tools_version = 33.0.2
