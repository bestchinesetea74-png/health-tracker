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

# (list) Application requirements
# Убедитесь, что здесь указан kivy и те библиотеки, которые вы импортируете в main.py
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
