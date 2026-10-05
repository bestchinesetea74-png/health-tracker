import os
import json
import time
import base64
import calendar
import tempfile
import shutil
import threading
import urllib.request
import urllib.parse
import urllib.error
import ssl
from datetime import datetime

import rsa as rsa_lib


def _make_ssl_context():
    """SSL-контекст с корневыми сертификатами из certifi.

    На Android у встроенного Python нет системного набора CA, и любой
    HTTPS-запрос падает с CERTIFICATE_VERIFY_FAILED (см. logcat:
    'unable to get local issuer certificate'). certifi приносит свой
    cacert.pem. Если certifi недоступен — стандартный контекст."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CTX = _make_ssl_context()


def _err_details(e):
    """Для HTTPError добавляет тело ответа Google (там написана причина
    403/404: storageQuotaExceeded, insufficientFilePermissions и т.д.)."""
    try:
        if isinstance(e, urllib.error.HTTPError):
            body = e.read().decode("utf-8", "replace").replace("\n", " ")
            return f"{e} | {body[:400]}"
    except Exception:
        pass
    return str(e)
from pyasn1.codec.der import decoder as der_decoder
from pyasn1_modules.rfc5208 import PrivateKeyInfo

from kivy.uix.floatlayout import FloatLayout
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.uix.screenmanager import NoTransition
from kivy.core.window import Window

# На Android клавиатура иногда открывается только один раз: после того как
# она закрылась (свайп/кнопка "назад"), Kivy иногда не понимает, что фокус
# снят, и повторный тап по тому же MDTextField не вызывает клавиатуру снова
# (помогает пересоздание виджета — отсюда и "помогает смена вкладки").
# 'below_target' — стандартное решение этой проблемы в Kivy на Android.
Window.softinput_mode = "below_target"
from kivy.graphics import Color, RoundedRectangle
from kivy.properties import NumericProperty, StringProperty, DictProperty

from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.screenmanager import MDScreenManager
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.label import MDLabel, MDIcon


# ---------------------------------------------------------------------------
# Чистые вспомогательные функции — вынесены отдельно от классов Kivy/Drive,
# чтобы их можно было тестировать без запуска приложения или сети.
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "glass_volume": 250,
    "visible_rows_count": 3,
    "water_target_val": 2000,
    "app_palette": "teal",
    "app_theme_style": "Dark",
    "water_icon_name": "water",
    "vitamin_names": {},
}


def merge_settings_with_defaults(settings):
    """Возвращает словарь настроек с безопасными дефолтами для отсутствующих
    ключей. Обеспечивает обратную совместимость со старыми JSON-файлами,
    где часть полей (например app_palette) ещё не сохранялась."""
    if not isinstance(settings, dict):
        settings = {}
    return {
        "glass_volume": settings.get("glass_volume", DEFAULT_SETTINGS["glass_volume"]),
        "visible_rows_count": settings.get("visible_rows_count", DEFAULT_SETTINGS["visible_rows_count"]),
        "water_target_val": settings.get("water_target_val", DEFAULT_SETTINGS["water_target_val"]),
        "app_palette": settings.get("app_palette", DEFAULT_SETTINGS["app_palette"]),
        "app_theme_style": settings.get("app_theme_style", DEFAULT_SETTINGS["app_theme_style"]),
        "water_icon_name": settings.get("water_icon_name", DEFAULT_SETTINGS["water_icon_name"]),
        "vitamin_names": dict(settings.get("vitamin_names", {}) or {}),
    }


def validate_remote_json(raw):
    """Проверяет, что raw (bytes или str) — валидный JSON-объект (dict).
    Возвращает распарсенный dict либо None, если данные повреждены/не dict."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def atomic_write_json(path, data):
    """Пишет data в path атомарно: сначала во временный файл в той же
    директории, затем os.replace(). Не трогает существующий файл при
    ошибке записи. Возвращает True/False."""
    try:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, temp_path = tempfile.mkstemp(suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
            return True
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    except Exception as e:
        print(f"[Storage] Error writing '{path}': {e}")
        return False


class Debouncer:
    """Простой debounce-помощник поверх threading.Timer. Несколько быстрых
    вызовов trigger() приводят к одному вызову callback после delay секунд
    молчания. Не зависит от Kivy — тестируется изолированно."""

    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self._timer = None
        self._lock = threading.Lock()

    def trigger(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self.callback)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None


# ---------------------------------------------------------------------------
# Google Drive sync
# ---------------------------------------------------------------------------

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Персональная синхронизация: scope ограничен только скрытой папкой
# приложения в Drive САМОГО пользователя (appDataFolder) — не полный
# доступ к его Drive, и не наш общий сервисный аккаунт.
SCOPE_DRIVE_APPDATA = "https://www.googleapis.com/auth/drive.appdata"


class PlayServicesDriveAuth:
    """Авторизация в Drive через официальный Authorization API из Google
    Play Services (pyjnius), вместо сервисного аккаунта на всех.

    Каждый вызов get_access_token() запрашивает токен у Play Services
    заново: если согласие уже когда-то было дано, Play Services отдаёт
    его МГНОВЕННО и без какого-либо экрана — сам кэширует и обновляет
    токен внутри себя, нам не нужно хранить refresh token вообще.
    Если согласия ещё не было — get_access_token() вернёт None, ничего
    не показывая пользователю; показать системный экран согласия может
    только sign_in_interactive(), и то лишь по явному нажатию кнопки.

    ВАЖНО: get_access_token() блокирующий (внутри Tasks.await) — вызывать
    только из фонового потока, никогда из UI-потока Kivy.
    """

    REQUEST_CODE_AUTHORIZE = 7001

    def __init__(self):
        self._last_error = None

    def _classes(self):
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        Identity = autoclass("com.google.android.gms.auth.api.identity.Identity")
        AuthorizationRequest = autoclass(
            "com.google.android.gms.auth.api.identity.AuthorizationRequest")
        Scope = autoclass("com.google.android.gms.common.api.Scope")
        return activity, Identity, AuthorizationRequest, Scope

    def _build_request(self, AuthorizationRequest, Scope):
        from jnius import autoclass
        ArrayList = autoclass("java.util.ArrayList")
        scopes = ArrayList()
        scopes.add(Scope(SCOPE_DRIVE_APPDATA))
        return AuthorizationRequest.Builder().setRequestedScopes(scopes).build()

    def _authorize_sync(self, timeout_sec):
        """Блокирующий вызов authorize() + ожидание результата. Работает
        и для уже выданного согласия (мгновенно), и для случая, когда
        согласия ещё не было (тогда result.hasResolution() == True,
        и дальше нужен sign_in_interactive, а не этот метод)."""
        from jnius import autoclass
        activity, Identity, AuthorizationRequest, Scope = self._classes()
        Tasks = autoclass("com.google.android.gms.tasks.Tasks")
        TimeUnit = autoclass("java.util.concurrent.TimeUnit")
        client = Identity.getAuthorizationClient(activity)
        request = self._build_request(AuthorizationRequest, Scope)
        task = client.authorize(request)
        # "await" — зарезервированное слово в Python, Tasks.await(...)
        # через точку был бы синтаксической ошибкой. Обходим через getattr.
        task_await = getattr(Tasks, "await")
        result = task_await(task, timeout_sec, TimeUnit.SECONDS)
        return client, result

    def get_access_token(self, timeout_sec=20):
        """Тихий (silent) запрос токена, без UI. None, если согласия
        ещё не было — тогда нужен sign_in_interactive()."""
        try:
            _client, result = self._authorize_sync(timeout_sec)
            if result.hasResolution():
                self._last_error = "needs_consent"
                return None
            self._last_error = None
            return result.getAccessToken()
        except Exception as e:
            self._last_error = str(e)
            print(f"[DriveSync] silent authorize failed: {e}")
            return None

    def needs_consent(self):
        return self._last_error == "needs_consent"

    def sign_in_interactive(self, on_done, timeout_sec=30):
        """Запускает системный экран выбора Google-аккаунта/согласия,
        если он ещё нужен (если согласие уже есть — просто подтвердит
        без UI). on_done(True/False) вызывается из ФОНОВОГО потока —
        вызывающая сторона сама должна перейти в UI-поток Kivy
        (Clock.schedule_once), если планирует трогать виджеты интерфейса.
        """
        def _worker():
            try:
                client, result = self._authorize_sync(timeout_sec)
                if not result.hasResolution():
                    self._last_error = None
                    on_done(True)
                    return

                from jnius import autoclass
                from android import activity as android_activity
                from android.runnable import run_on_ui_thread
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                activity = PythonActivity.mActivity

                done_event = threading.Event()
                outcome = {"ok": False}

                def _on_activity_result(requestCode, resultCode, data):
                    if requestCode != self.REQUEST_CODE_AUTHORIZE:
                        return
                    android_activity.unbind(on_activity_result=_on_activity_result)
                    try:
                        client.getAuthorizationResultFromIntent(data)
                        outcome["ok"] = True
                    except Exception as e:
                        print(f"[DriveSync] getAuthorizationResultFromIntent failed: {e}")
                        outcome["ok"] = False
                    done_event.set()

                android_activity.bind(on_activity_result=_on_activity_result)

                @run_on_ui_thread
                def _launch():
                    try:
                        pending_intent = result.getPendingIntent()
                        sender = pending_intent.getIntentSender()
                        activity.startIntentSenderForResult(
                            sender, self.REQUEST_CODE_AUTHORIZE, None, 0, 0, 0, None
                        )
                    except Exception as e:
                        print(f"[DriveSync] startIntentSenderForResult failed: {e}")
                        android_activity.unbind(on_activity_result=_on_activity_result)
                        done_event.set()

                _launch()
                if not done_event.wait(timeout_sec + 15):
                    print("[DriveSync] sign-in timed out waiting for activity result")
                self._last_error = None if outcome["ok"] else "consent_failed"
                on_done(outcome["ok"])
            except Exception as e:
                self._last_error = str(e)
                print(f"[DriveSync] sign_in_interactive failed: {e}")
                on_done(False)

        threading.Thread(target=_worker, daemon=True).start()


class GoogleDriveSync:
    """Синхронизация с Google Drive через прямые REST-вызовы Drive API v3
    (без googleapiclient) и Service Account JWT-аутентификацию на чистом
    Python (без google-auth/cryptography)."""

    def __init__(self, remote_filename="tracker_history.json"):
        self.remote_filename = remote_filename
        self.auth = PlayServicesDriveAuth()
        self.file_id = None  # кэшируем id после первого резолва — фикс дублей

    def sign_in_interactive(self, on_done):
        """Проброс к PlayServicesDriveAuth — вызывать из обработчика
        нажатия кнопки «Войти через Google»."""
        self.auth.sign_in_interactive(on_done)

    def _authed_request(self, method, url, headers=None, data=None, timeout=15):
        token = self.auth.get_access_token()
        if not token:
            raise RuntimeError(
                "No Google access token (not signed in yet or consent revoked)")
        req_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)

    def _resolve_file_id(self):
        """Детерминированный поиск файла: при дублях берём самый старый
        по createdTime и логируем ситуацию, вместо тихого выбора files[0]
        в непредсказуемом порядке. Кэширует найденный id в self.file_id."""
        if not self.auth:
            return None
        try:
            query = urllib.parse.urlencode({
                "q": f"name = '{self.remote_filename}' and trashed = false",
                "spaces": "appDataFolder",
                "fields": "files(id, name, createdTime, size)",
            })
            with self._authed_request("GET", f"{DRIVE_API}/files?{query}") as resp:
                result = json.loads(resp.read().decode("utf-8"))

            files = result.get("files", [])
            for f in files:
                print(f"[DriveSync] Found '{f.get('name')}' id={f.get('id')} "
                      f"size={f.get('size')} created={f.get('createdTime')}")
            if len(files) > 1:
                # Это личный appDataFolder самого пользователя — дубль
                # возможен только при гонке между двумя его устройствами.
                # Берём самый старый, чтобы оба устройства сошлись к
                # одному и тому же файлу.
                print(f"[DriveSync] Warning: found {len(files)} files "
                      f"named '{self.remote_filename}'. Taking the oldest.")

            files.sort(key=lambda f: f.get("createdTime", ""))
            self.file_id = files[0]["id"] if files else None
            return self.file_id
        except Exception as e:
            print(f"[DriveSync] Error resolving file id: {_err_details(e)}")
            return None

    def upload_file(self, local_filepath):
        """Синхронная выгрузка. Вызывающая сторона отвечает за то, чтобы
        это не выполнялось в основном UI-потоке (см. Debouncer в App)."""
        if not self.auth or not os.path.exists(local_filepath):
            return
        try:
            if not self.file_id:
                self._resolve_file_id()

            with open(local_filepath, "rb") as f:
                content = f.read()

            if self.file_id:
                url = f"{DRIVE_UPLOAD_API}/{self.file_id}?uploadType=media"
                with self._authed_request(
                    "PATCH", url, headers={"Content-Type": "application/json"}, data=content
                ):
                    pass
                print("[DriveSync] Android: DB updated on Drive.")
            else:
                # Двухшаговое создание: сначала метаданные (имя файла),
                # затем загрузка содержимого в уже созданный файл.
                meta = json.dumps({
                    "name": self.remote_filename,
                    "parents": ["appDataFolder"],
                }).encode("utf-8")
                with self._authed_request(
                    "POST", f"{DRIVE_API}/files",
                    headers={"Content-Type": "application/json"}, data=meta
                ) as resp:
                    created = json.loads(resp.read().decode("utf-8"))
                self.file_id = created["id"]

                url = f"{DRIVE_UPLOAD_API}/{self.file_id}?uploadType=media"
                with self._authed_request(
                    "PATCH", url, headers={"Content-Type": "application/json"}, data=content
                ):
                    pass
                print("[DriveSync] Android: DB created on Drive.")
        except Exception as e:
            print(f"[DriveSync] Upload Error: {_err_details(e)}")

    def download_file_safe(self):
        """Безопасное скачивание: НЕ трогает локальный файл. Скачивает во
        временный буфер в памяти, проверяет валидность JSON и возвращает
        (True, data) при успехе или (False, None) при любой ошибке —
        сетевой, HTTP или JSON-парсинга."""
        if not self.auth:
            return False, None
        try:
            if not self.file_id:
                self._resolve_file_id()
            if not self.file_id:
                return False, None

            with self._authed_request("GET", f"{DRIVE_API}/files/{self.file_id}?alt=media") as resp:
                raw = resp.read()

            data = validate_remote_json(raw)
            if data is None:
                print("[DriveSync] Download Error: remote file is not valid JSON, "
                      "local file kept unchanged.")
                return False, None

            return True, data
        except Exception as e:
            print(f"[DriveSync] Download Error: {_err_details(e)}. Local file kept unchanged.")
            return False, None


class DayCell(MDBoxLayout):
    """Лёгкая ячейка календаря БЕЗ тяжёлой M3-графики MDButton (ripple,
    тени, несколько canvas-инструкций на кнопку). Просто цветной
    прямоугольник + текст через kivy.graphics напрямую.

    Причина: полноценные MDButton x42 (сетка 7x6), создаваемые все разом
    за один кадр, вызывали нативный краш GPU-драйвера Adreno
    (SIGSEGV, null pointer dereference внутри libGLESv2_adreno.so,
    через kivy/graphics/vbo.so). Лёгкая замена снимает нагрузку на VBO."""

    def __init__(self, color, text, on_press=None, **kwargs):
        super().__init__(**kwargs)
        self._on_press = on_press
        with self.canvas.before:
            Color(*color)
            self._rect = RoundedRectangle(radius=[6], pos=self.pos, size=self.size)
        self.bind(pos=self._update_rect, size=self._update_rect)

        self.add_widget(MDLabel(
            text=text,
            halign="center",
            valign="middle",
        ))

    def _update_rect(self, *args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self._on_press:
            self._on_press()
            return True
        return super().on_touch_down(touch)



_PALETTE_ACCENT_RGB = {
    # Постоянный насыщенный акцент на каждую палитру, одинаковый в любой
    # теме (Light/Dark/AMOLED). Значения — классическая Material "500":
    # раньше акцент брался из theme_cls.primaryColor, но в тёмной теме
    # эта M3-роль намеренно светлая/приглушённая (так и задумано в
    # спецификации ради контраста на тёмном фоне) — красный превращался
    # в бронзовый, синий в лавандовый и т.п. Здесь цвет фиксированный и
    # не зависит от темы. Эти же значения (в hex) использует виджет как
    # запасной вариант, пока приложение ещё ни разу не сохраняло базу.
    "teal":   (0.000, 0.588, 0.533),
    "indigo": (0.247, 0.318, 0.710),
    "blue":   (0.129, 0.588, 0.953),
    "orange": (1.000, 0.596, 0.000),
    "purple": (0.612, 0.153, 0.690),
    "red":    (0.957, 0.263, 0.212),
}


def _theme_accent(alpha=1.0):
    """Фиксированный акцентный цвет по имени палитры (см. _PALETTE_ACCENT_RGB)."""
    try:
        palette = MDApp.get_running_app().app_palette
    except Exception:
        palette = "teal"
    r, g, b = _PALETTE_ACCENT_RGB.get(palette, _PALETTE_ACCENT_RGB["teal"])
    return (r, g, b, alpha)


def _theme_is_light():
    try:
        return MDApp.get_running_app().theme_cls.theme_style == "Light"
    except Exception:
        return False


def _refocus_keyboard(instance, value):
    """При закрытии системной клавиатуры не через потерю фокуса виджетом
    (например, кнопкой 'назад' на Android) Kivy иногда не сбрасывает своё
    внутреннее состояние — повторный тап ставит focus=True, но саму
    клавиатуру заново не показывает (известный баг, см. kivy/kivy#5550
    и #7698). Форсируем show_keyboard() при каждом получении фокуса;
    try/except — на случай, если метод недоступен у конкретной версии
    MDTextField, чтобы это не могло уронить приложение."""
    if value:
        def _show(dt):
            try:
                instance.show_keyboard()
            except Exception:
                pass
        Clock.schedule_once(_show, 0.05)


class LightButton(MDBoxLayout):
    """Лёгкая кнопка без MDButton (ripple, тени, state-layer). Один
    RoundedRectangle + MDLabel — минимум нагрузки на VBO/драйвер Adreno."""

    def __init__(self, text="", filled=False, color=None, on_release=None,
                 **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", "48dp")
        super().__init__(**kwargs)
        self._on_release = on_release

        if filled:
            base = color or _theme_accent(1.0)
            text_color = (1, 1, 1, 1)
        elif _theme_is_light():
            base = color or (0.86, 0.86, 0.89, 1)
            text_color = (0.13, 0.13, 0.13, 1)
        else:
            base = color or (0.22, 0.22, 0.24, 1)
            text_color = (1, 1, 1, 1)

        with self.canvas.before:
            self._col = Color(*base)
            self._rect = RoundedRectangle(radius=[12], pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)
        self.add_widget(MDLabel(text=text, halign="center", valign="middle",
                                 theme_text_color="Custom", text_color=text_color))

    def _upd(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos) and self._on_release:
                self._on_release(self)
            return True
        return super().on_touch_up(touch)


class LightIconButton(FloatLayout):
    """Лёгкая icon-only кнопка без MDIconButton.

    В KivyMD 2.0 M3-кнопки (включая MDIconButton) используют общий набор
    поведений — ripple, state-layer, а для стилей с фоном (tonal/filled)
    ещё и elevation/тень через Fbo/RenderContext. Замена MDButton и
    MDNavigationBar на лёгкие аналоги не убрала краш (см. crash_log) —
    значит источник тот же самый общий M3-механизм, просто через
    MDIconButton. Здесь только круглая подложка (RoundedRectangle) и
    иконка (MDIcon) — Fbo нет вообще.

    База — FloatLayout, а не MDBoxLayout: BoxLayout применяет pos_hint
    только по "поперечной" оси, поэтому иконка внутри него была смещена
    от центра кнопки по главной оси. FloatLayout центрирует по обеим.
    """

    def __init__(self, icon="", tonal=False, on_release=None, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", ("40dp", "40dp"))
        super().__init__(**kwargs)
        self._on_release = on_release
        base = _theme_accent(0.35) if tonal else (0, 0, 0, 0)
        with self.canvas.before:
            self._col = Color(*base)
            self._circle = RoundedRectangle(radius=[20], pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)
        self.add_widget(MDIcon(icon=icon, halign="center", valign="middle",
                                pos_hint={"center_x": 0.5, "center_y": 0.5}))

    def _upd(self, *a):
        self._circle.pos = self.pos
        self._circle.size = self.size

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos) and self._on_release:
                self._on_release(self)
            return True
        return super().on_touch_up(touch)


class LightTabItem(MDBoxLayout):
    """Лёгкая вкладка нижней навигации без MDNavigationBar/MDNavigationItem.

    У MDNavigationBar в KivyMD 2.0 переключение активного "pill"-индикатора
    рисуется через Fbo/RenderContext (морфинг подложки между вкладками).
    Именно Fbo-код был на стеке краша (fbo.so, кадр внутри самой функции,
    а не просто загруженная библиотека) при КАЖДОМ тапе по вкладке — даже
    после того как экранный переход стал NoTransition. Здесь никакого Fbo
    нет вообще: обычный RoundedRectangle-подсвет + иконка + подпись.
    """

    def __init__(self, icon="", text="", selected=False, on_release=None,
                 **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", "56dp")
        kwargs.setdefault("padding", ("4dp", "6dp"))
        kwargs.setdefault("spacing", "2dp")
        super().__init__(**kwargs)
        self._on_release = on_release
        self.selected = selected

        with self.canvas.before:
            self._col = Color(0, 0, 0, 0)
            self._pill = RoundedRectangle(radius=[16], pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)

        self._icon = MDIcon(icon=icon, halign="center",
                             pos_hint={"center_x": 0.5})
        self._label = MDLabel(text=text, halign="center",
                               font_style="Label", role="small",
                               size_hint_y=None, height="16dp")
        self.add_widget(self._icon)
        self.add_widget(self._label)
        self._apply_selected()

    def _upd(self, *a):
        self._pill.pos = self.pos
        self._pill.size = self.size

    def _apply_selected(self):
        self._col.rgba = _theme_accent(0.35) if self.selected else (0, 0, 0, 0)

    def set_selected(self, value):
        self.selected = value
        self._apply_selected()

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos) and self._on_release:
                self._on_release(self)
            return True
        return super().on_touch_up(touch)


KV = '''
MDScreen:
    md_bg_color: self.theme_cls.backgroundColor

    MDBoxLayout:
        orientation: "vertical"

        MDScreenManager:
            id: screen_manager

            MDScreen:
                name: "tracker_screen"
                MDBoxLayout:
                    id: tracker_container
                    orientation: "vertical"
                    padding: "16dp"
                    spacing: "16dp"

            MDScreen:
                name: "calendar_screen"
                MDBoxLayout:
                    id: calendar_container
                    orientation: "vertical"
                    padding: "16dp"
                    spacing: "16dp"

            MDScreen:
                name: "settings_screen"
                MDBoxLayout:
                    id: settings_container
                    orientation: "vertical"
                    padding: "16dp"
                    spacing: "16dp"

        MDBoxLayout:
            id: nav_bar
            orientation: "horizontal"
            size_hint_y: None
            height: "64dp"
            padding: "8dp", "4dp"
'''


class HealthTrackerApp(MDApp):
    water_ml = NumericProperty(0)
    glass_volume = NumericProperty(250)
    water_target_val = NumericProperty(2000)
    visible_rows_count = NumericProperty(3)

    app_palette = StringProperty("teal")
    app_theme_style = StringProperty("Dark")
    water_icon_name = StringProperty("water")

    vitamin_names = DictProperty({
        "vit0": "Vitamin 1",
        "vit1": "Vitamin 2",
        "vit2": "Vitamin 3",
        "vit3": "Vitamin 4",
        "vit4": "Vitamin 5"
    })

    vitamin_counts = DictProperty({
        "vit0": 0,
        "vit1": 0,
        "vit2": 0,
        "vit3": 0,
        "vit4": 0
    })

    db_data = DictProperty({})
    json_path = StringProperty("tracker_history.json")

    # --- lifecycle ---------------------------------------------------

    def _resolve_json_path(self):
        """Единый путь к базе: <getFilesDir()>/tracker_history.json на Android.
        Тот же путь использует виджет (Java). Если файл лежал по старому
        относительному пути, он один раз копируется на новое место."""
        name = "tracker_history.json"
        old = os.path.abspath(name)
        base = self.user_data_dir
        try:
            from kivy.utils import platform as _platform
            if _platform == "android":
                from jnius import autoclass
                ctx = autoclass("org.kivy.android.PythonActivity").mActivity
                base = ctx.getFilesDir().getAbsolutePath()
        except Exception as e:
            print(f"[Storage] getFilesDir() failed, using user_data_dir: {e}")
        os.makedirs(base, exist_ok=True)
        new = os.path.join(base, name)
        if not os.path.exists(new) and os.path.exists(old) and old != new:
            try:
                shutil.copy2(old, new)
                print(f"[Storage] Migrated DB {old} -> {new}")
            except Exception as e:
                print(f"[Storage] Migration failed: {e}")
        return new

    def build(self):
        self.json_path = self._resolve_json_path()
        self.drive_sync = GoogleDriveSync()
        # Debounce на аплоад в облако (0.5–1.5с по спеке); сам вызов идёт
        # внутри threading.Timer, то есть уже не в основном UI-потоке.
        self._upload_debouncer = Debouncer(1.0, self._perform_cloud_upload)

        # Локальная загрузка — быстрая операция с диском, безопасна в build().
        self.load_database()
        self.apply_visual_theme()

        root = Builder.load_string(KV)
        # NoTransition вместо дефолтного (у MDScreenManager он рендерит оба
        # экрана через FBO/RenderContext). Именно это FBO ловило момент
        # пересборки тяжёлого дерева виджетов на следующем кадре после
        # sm.current = ... и роняло Adreno-драйвер null pointer dereference
        # в vbo.so/compiler.so/instructions.so (см. crash_log). Полный
        # переход длится дольше одного кадра, поэтому Clock.schedule_once(0)
        # ниже не успевал дождаться его завершения. Без FBO-перехода
        # sm.current меняется мгновенно и без промежуточного GL-состояния —
        # безопасно рекомпилировать canvas сразу.
        root.ids.screen_manager.transition = NoTransition()
        self._build_nav_bar(root)
        return root

    def _build_nav_bar(self, root):
        """Три LightTabItem вместо MDNavigationBar (см. LightTabItem)."""
        nav = root.ids.nav_bar
        self._tab_items = {}
        specs = [
            ("tracker_screen", self.water_icon_name, "Tracker"),
            ("calendar_screen", "calendar-month", "Calendar"),
            ("settings_screen", "cog", "Settings"),
        ]
        for screen_name, icon, label in specs:
            item = LightTabItem(
                icon=icon, text=label,
                selected=(screen_name == "tracker_screen"),
                on_release=lambda w, s=screen_name: self.switch_screen(s),
            )
            self._tab_items[screen_name] = item
            nav.add_widget(item)

    def on_start(self):
        Clock.schedule_once(lambda dt: self.build_tracker_screen(), 0.1)
        Clock.schedule_once(lambda dt: self.build_calendar_screen(), 0.2)
        Clock.schedule_once(lambda dt: self.build_settings_screen(), 0.3)

        # Первичная синхронизация с Google Drive — в фоновом потоке,
        # чтобы не блокировать отрисовку UI при старте.
        threading.Thread(target=self._background_initial_sync, daemon=True).start()

    def _file_mtime(self):
        try:
            return os.path.getmtime(self.json_path)
        except OSError:
            return 0

    def on_resume(self):
        """Если файл изменил виджет (пока приложение было свёрнуто),
        подхватываем изменения и отправляем их в облако."""
        if self._file_mtime() == getattr(self, "_json_mtime", 0):
            return
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = validate_remote_json(f.read())
        except Exception:
            data = None
        if data is None:
            return
        self._json_mtime = self._file_mtime()
        self._apply_remote_data(data)
        if hasattr(self, "_upload_debouncer"):
            self._upload_debouncer.trigger()

    def _notify_widget(self):
        """Просит виджет на рабочем столе перерисоваться (только Android)."""
        try:
            from kivy.utils import platform as _platform
            if _platform != "android":
                return
            from jnius import autoclass
            ctx = autoclass("org.kivy.android.PythonActivity").mActivity
            Intent = autoclass("android.content.Intent")
            ComponentName = autoclass("android.content.ComponentName")
            pkg = ctx.getPackageName()
            intent = Intent("org.vladimir.healthtracker.WIDGET_REFRESH")
            intent.setComponent(ComponentName(pkg, "org.vladimir.healthtracker.WaterWidgetProvider"))
            ctx.sendBroadcast(intent)
        except Exception as e:
            print(f"[Widget] notify failed: {e}")

    def on_stop(self):
        # Если приложение закрывается сразу после правки — не теряем
        # отложенный аплоад молча, но и не блокируем закрытие надолго.
        if hasattr(self, "_upload_debouncer"):
            self._upload_debouncer.cancel()
            self._perform_cloud_upload()

    # --- Google Drive sync (фоновые операции) -------------------------

    def _background_initial_sync(self):
        self._safe_download_and_apply()

    def manual_sync(self):
        print("[Android] Manual sync requested...")
        threading.Thread(target=self._perform_manual_sync, daemon=True).start()

    def sign_in_google(self, button=None):
        """Обработчик кнопки «Войти через Google». Показывает системный
        экран согласия (если нужно) и, если удачно, сразу запускает
        синхронизацию."""
        label = button.children[0] if button and button.children else None
        if label:
            label.text = "Входим..."

        def _on_done(ok):
            def _update(_dt):
                if label:
                    label.text = "✓ Подключено" if ok else "Не удалось, нажмите ещё раз"
                if ok:
                    print("[Android] Google sign-in OK, syncing...")
                    self.manual_sync()
                else:
                    print("[Android] Google sign-in failed or cancelled.")
            Clock.schedule_once(_update, 0)

        self.drive_sync.sign_in_interactive(_on_done)

    def _perform_manual_sync(self):
        self._safe_download_and_apply()
        print("[Android] Manual sync complete!")

    def _safe_download_and_apply(self):
        """Качает данные с Drive без риска повредить локальный файл.
        При любой ошибке (сеть, HTTP, невалидный JSON) локальный файл
        и текущее состояние приложения остаются без изменений."""
        if not hasattr(self, "drive_sync"):
            return

        success, remote_data = self.drive_sync.download_file_safe()
        if not success:
            # Ошибка уже залогирована внутри download_file_safe.
            return

        if not atomic_write_json(self.json_path, remote_data):
            print("[DriveSync] Failed to persist downloaded data locally; "
                  "keeping previous local file.")
            return

        self._json_mtime = self._file_mtime()
        print("[DriveSync] Safe download completed and applied.")
        Clock.schedule_once(lambda dt: self._apply_remote_data(remote_data))

    def _apply_remote_data(self, remote_data):
        """Выполняется в основном потоке (через Clock.schedule_once).

        Три тяжёлые пересборки экранов разнесены по отдельным кадрам
        (а не вызываются подряд в одном), по той же причине, что и в
        switch_screen: массовая рекомпиляция canvas-дерева в один присест
        — надёжный способ поймать нативный краш GPU-драйвера."""
        self.db_data = remote_data
        self._load_settings_from_db()
        self._load_today_from_db()
        self.apply_visual_theme()
        Clock.schedule_once(lambda dt: self.build_tracker_screen(), 0)
        Clock.schedule_once(lambda dt: self.build_calendar_screen(), 0.1)
        Clock.schedule_once(lambda dt: self.build_settings_screen(), 0.2)

    def _perform_cloud_upload(self):
        """Вызывается из Debouncer (уже в отдельном потоке от threading.Timer),
        поэтому не блокирует UI."""
        if hasattr(self, 'drive_sync'):
            self.drive_sync.upload_file(self.json_path)

    # --- локальное хранилище ------------------------------------------

    def switch_screen(self, screen_name):
        sm = self.root.ids.screen_manager
        sm.current = screen_name
        if hasattr(self, "_tab_items"):
            for name, item in self._tab_items.items():
                item.set_selected(name == screen_name)
        # Тяжёлая пересборка виджетов экрана откладывается на СЛЕДУЮЩИЙ
        # кадр через Clock.schedule_once, а не вызывается синхронно прямо
        # внутри обработчика on_release кнопки навигации. Синхронный вызов
        # clear_widgets() + массовое добавление новых виджетов в том же
        # кадре, где ещё может идти обработка графики текущего touch-события,
        # приводил к детерминированному нативному крашу GPU-драйвера
        # (SIGSEGV в libGLESv2_adreno.so через recompile canvas-дерева).
        Clock.schedule_once(lambda dt: self._build_screen_content(screen_name), 0)

    def _build_screen_content(self, screen_name):
        if screen_name == "tracker_screen":
            self.build_tracker_screen()
        elif screen_name == "calendar_screen":
            self.build_calendar_screen()
        elif screen_name == "settings_screen":
            self.build_settings_screen()

    def get_today_str(self):
        return datetime.now().strftime("%Y-%m-%d")

    def load_database(self):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.db_data = json.load(f)
            except Exception as e:
                print(f"[Error] Failed to load local JSON: {e}")
                self.db_data = {}
        else:
            self.db_data = {}

        self._json_mtime = self._file_mtime()
        self._load_settings_from_db()
        self._load_today_from_db()

    def _load_settings_from_db(self):
        """Восстанавливает ВСЕ настройки, включая app_palette/app_theme_style/
        water_icon_name (раньше они сохранялись, но не читались обратно).
        Безопасные дефолты для отсутствующих ключей — обратная совместимость
        со старыми JSON."""
        settings = merge_settings_with_defaults(self.db_data.get("settings", {}))

        self.glass_volume = settings["glass_volume"]
        self.visible_rows_count = settings["visible_rows_count"]
        self.water_target_val = settings["water_target_val"]
        self.app_palette = settings["app_palette"]
        self.app_theme_style = settings["app_theme_style"]
        self.water_icon_name = settings["water_icon_name"]

        for key, val in settings["vitamin_names"].items():
            self.vitamin_names[key] = val

    def _load_today_from_db(self):
        today_key = self.get_today_str()
        today_data = self.db_data.get(today_key, {})

        self.water_ml = today_data.get("water_ml", 0)
        for i in range(5):
            k = f"vit{i}"
            self.vitamin_counts[k] = today_data.get(k, 0)

    def save_database(self, upload=True):
        """Локальное сохранение — всегда немедленное и синхронное (это
        просто запись на диск, не сеть). Облачный аплоад — по умолчанию
        включён, но выполняется с debounce и в фоновом потоке."""
        today_key = self.get_today_str()

        if today_key not in self.db_data:
            self.db_data[today_key] = {}

        self.db_data[today_key]["water_ml"] = self.water_ml
        for i in range(5):
            k = f"vit{i}"
            self.db_data[today_key][k] = self.vitamin_counts.get(k, 0)

        self.db_data["settings"] = {
            "glass_volume": self.glass_volume,
            "visible_rows_count": self.visible_rows_count,
            "water_target_val": self.water_target_val,
            "app_palette": self.app_palette,
            "app_theme_style": self.app_theme_style,
            "water_icon_name": self.water_icon_name,
            "vitamin_names": dict(self.vitamin_names),
            # Фиксированный акцент по палитре (см. _theme_accent) как
            # "#RRGGBB". Виджет читает именно это значение, а не
            # пересчитывает цвет по имени палитры сам — так оттенок
            # гарантированно совпадает с тем, что видно в приложении.
            "widget_accent_hex": self._accent_hex_for_widget(),
        }

        if not atomic_write_json(self.json_path, dict(self.db_data)):
            return
        self._json_mtime = self._file_mtime()
        self._notify_widget()

        if upload and hasattr(self, "_upload_debouncer"):
            self._upload_debouncer.trigger()

    def _accent_hex_for_widget(self):
        """Тот же фиксированный акцент, что и во всём приложении
        (см. _theme_accent), как "#RRGGBB" — для виджета на рабочем столе."""
        try:
            r, g, b, *_ = _theme_accent(1.0)
            return "#%02X%02X%02X" % (
                max(0, min(255, round(r * 255))),
                max(0, min(255, round(g * 255))),
                max(0, min(255, round(b * 255))),
            )
        except Exception as e:
            print(f"[Widget] accent color export failed: {e}")
            return ""

    def apply_visual_theme(self):
        self.theme_cls.primary_palette = self.app_palette
        if self.app_theme_style == "AMOLED":
            self.theme_cls.theme_style = "Dark"
        else:
            self.theme_cls.theme_style = self.app_theme_style

    # --- UI: tracker screen ---------------------------------------------

    def build_tracker_screen(self):
        container = self.root.ids.tracker_container
        container.clear_widgets()

        scroll = MDScrollView()
        layout = MDBoxLayout(
            orientation="vertical",
            spacing="16dp",
            adaptive_height=True,
            padding="4dp"
        )

        bg_color = (
            (0, 0, 0, 1) if self.app_theme_style == "AMOLED" else
            (0.93, 0.93, 0.95, 1) if self.app_theme_style == "Light" else
            (0.12, 0.12, 0.12, 1)
        )

        water_box = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True,
            theme_bg_color="Custom",
            md_bg_color=bg_color,
            padding="16dp"
        )

        glasses_count = int(self.water_ml // self.glass_volume) if self.glass_volume > 0 else 0

        header_row = MDBoxLayout(
            orientation="horizontal",
            spacing="8dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )
        water_icon = LightIconButton(icon=self.water_icon_name)

        water_label = MDLabel(
            text=f"Water: {self.water_ml} ml / {self.water_target_val} ml\n"
                 f"Glasses: {glasses_count} (Glass size: {self.glass_volume} ml)",
            halign="center",
            adaptive_height=True
        )
        header_row.add_widget(water_icon)
        header_row.add_widget(water_label)
        water_box.add_widget(header_row)

        btn_grid = MDGridLayout(
            cols=2,
            spacing="8dp",
            adaptive_height=True
        )

        btn_m50 = LightButton(text="-50ml", filled=False, on_release=lambda x: self.change_glass_volume(-50))

        btn_p50 = LightButton(text="+50ml", filled=False, on_release=lambda x: self.change_glass_volume(50))

        btn_mgl = LightButton(text="-1 glass", filled=False, on_release=lambda x: self.change_water_glass(-1))

        btn_pgl = LightButton(text="+1 glass", filled=False, on_release=lambda x: self.change_water_glass(1))

        btn_grid.add_widget(btn_m50)
        btn_grid.add_widget(btn_p50)
        btn_grid.add_widget(btn_mgl)
        btn_grid.add_widget(btn_pgl)
        water_box.add_widget(btn_grid)

        layout.add_widget(water_box)

        vit_box = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True
        )

        for i in range(self.visible_rows_count):
            key = f"vit{i}"
            row = MDBoxLayout(
                orientation="horizontal",
                spacing="12dp",
                adaptive_height=True
            )

            field = MDTextField(
                mode="outlined",
                text=self.vitamin_names.get(key, f"Vit {i+1}"),
                size_hint_x=0.55
            )
            field.bind(text=lambda instance, val, k=key: self.update_vit_name(k, val))
            field.bind(focus=_refocus_keyboard)

            count_label = MDLabel(
                text=f"{self.vitamin_counts.get(key, 0)} pcs",
                halign="center",
                size_hint_x=0.2,
                adaptive_height=True
            )

            btn_sub = LightButton(text="-", filled=False, on_release=lambda x, k=key: self.change_vitamin(k, -1), size_hint_x=0.125)

            btn_add = LightButton(text="+", filled=False, on_release=lambda x, k=key: self.change_vitamin(k, 1), size_hint_x=0.125)

            row.add_widget(field)
            row.add_widget(count_label)
            row.add_widget(btn_sub)
            row.add_widget(btn_add)

            vit_box.add_widget(row)

        layout.add_widget(vit_box)

        actions_layout = MDBoxLayout(
            orientation="horizontal",
            spacing="12dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )

        reset_btn = LightButton(text="Reset Today", filled=False, on_release=lambda x: self.reset_today())
        actions_layout.add_widget(reset_btn)

        sync_btn = LightButton(text="Sync", filled=True, color=(0.15, 0.68, 0.37, 1), on_release=lambda x: self.manual_sync())
        actions_layout.add_widget(sync_btn)

        signin_btn = LightButton(text="Войти через Google", filled=False,
                                  on_release=lambda x: self.sign_in_google(x))
        actions_layout.add_widget(signin_btn)

        layout.add_widget(actions_layout)

        scroll.add_widget(layout)
        container.add_widget(scroll)

    def change_glass_volume(self, delta):
        self.glass_volume = max(50, self.glass_volume + delta)
        self.save_database()
        self.build_tracker_screen()

    def change_water_glass(self, count):
        delta = count * self.glass_volume
        self.water_ml = max(0, self.water_ml + delta)
        self.save_database()
        self.build_tracker_screen()

    def update_vit_name(self, key, value):
        self.vitamin_names[key] = value
        # Каждая нажатая клавиша: локально сохраняется сразу, аплоад в
        # облако — debounce внутри save_database (не на каждый символ).
        self.save_database()

    def change_vitamin(self, key, delta):
        current = self.vitamin_counts.get(key, 0)
        self.vitamin_counts[key] = max(0, current + delta)
        self.save_database()
        self.build_tracker_screen()

    def reset_today(self):
        self.water_ml = 0
        for i in range(5):
            self.vitamin_counts[f"vit{i}"] = 0
        self.save_database()
        self.build_tracker_screen()

    # --- UI: calendar screen ---------------------------------------------

    def build_calendar_screen(self):
        # ВРЕМЕННО УПРОЩЕНО ДЛЯ ДИАГНОСТИКИ КРАША. Если этот минимальный
        # экран тоже крашится при переходе — проблема не в содержимом
        # (гриде/кнопках), а в самом переключении MDScreenManager.
        container = self.root.ids.calendar_container
        container.clear_widgets()

        scroll = MDScrollView()
        layout = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True,
            padding="4dp"
        )

        now = datetime.now()
        year = now.year
        month = now.month
        month_name = now.strftime("%B %Y")

        header = MDLabel(
            text=f"Calendar: {month_name}",
            halign="center",
            font_style="Headline",
            role="small",
            adaptive_height=True
        )
        layout.add_widget(header)

        grid = MDGridLayout(
            cols=7,
            spacing="8dp",
            adaptive_height=True
        )

        day_headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for dh in day_headers:
            lbl = MDLabel(
                text=dh,
                halign="center",
                bold=True,
                adaptive_height=True
            )
            grid.add_widget(lbl)

        month_calendar = calendar.monthcalendar(year, month)

        # Строки добавляются с небольшой задержкой между собой (Clock.schedule_once),
        # а не все 6 строк x 7 ячеек одновременно за один кадр — это и было
        # причиной нативного краша GPU-драйвера (см. DayCell выше).
        def add_week(week_index):
            if week_index >= len(month_calendar):
                return
            week = month_calendar[week_index]
            for day in week:
                if day == 0:
                    grid.add_widget(MDBoxLayout(size_hint_y=None, height="60dp"))
                else:
                    date_str = f"{year}-{month:02d}-{day:02d}"
                    status_color, label_text = self.evaluate_day_status(date_str, day)
                    cell = DayCell(
                        color=status_color,
                        text=label_text,
                        size_hint_y=None,
                        height="60dp",
                    )
                    grid.add_widget(cell)
            Clock.schedule_once(lambda dt: add_week(week_index + 1), 0.02)

        add_week(0)

        layout.add_widget(grid)
        scroll.add_widget(layout)
        container.add_widget(scroll)

    def evaluate_day_status(self, date_str, day_num):
        # ПРИМЕЧАНИЕ (follow-up, не в рамках этого патча): статус
        # исторических дней сейчас считается относительно ТЕКУЩЕГО
        # water_target_val/настроек, а не тех, что были актуальны в тот
        # день. Это отдельная задача, зафиксирована как известный риск.
        day_data = self.db_data.get(date_str)

        if not day_data:
            return (0.25, 0.25, 0.25, 1), f"{day_num}\n0.0L"

        water_ml = day_data.get("water_ml", 0)
        water_liters = round(water_ml / 1000.0, 1)

        water_passed = (water_ml >= self.water_target_val)

        vitamins_passed = True
        for i in range(self.visible_rows_count):
            vit_val = day_data.get(f"vit{i}", 0)
            if vit_val < 1:
                vitamins_passed = False
                break

        if water_passed and vitamins_passed:
            return (0.18, 0.49, 0.20, 1), f"{day_num}\n{water_liters}L"
        else:
            return (0.6, 0.15, 0.15, 1), f"{day_num}\n{water_liters}L"

    # --- UI: settings screen ---------------------------------------------

    def build_settings_screen(self):
        # ВРЕМЕННО УПРОЩЕНО ДЛЯ ДИАГНОСТИКИ КРАША — см. комментарий
        # в build_calendar_screen выше.
        container = self.root.ids.settings_container
        container.clear_widgets()

        scroll = MDScrollView()
        layout = MDBoxLayout(
            orientation="vertical",
            spacing="16dp",
            adaptive_height=True,
            padding="4dp"
        )
        scroll.add_widget(layout)
        container.add_widget(scroll)

        bg_color = (
            (0, 0, 0, 1) if self.app_theme_style == "AMOLED" else
            (0.93, 0.93, 0.95, 1) if self.app_theme_style == "Light" else
            (0.12, 0.12, 0.12, 1)
        )

        # Секции добавляются по одной с небольшой задержкой между собой
        # (Clock.schedule_once), а не все сразу за один кадр. Это снижает
        # число одновременно создаваемых MDButton/графических инструкций
        # за кадр — та же причина краха GPU-драйвера, что и на экране
        # календаря (см. DayCell и build_calendar_screen).

        def add_palette_section(_dt=None):
            palette_box = MDBoxLayout(
                orientation="vertical",
                spacing="12dp",
                adaptive_height=True,
                theme_bg_color="Custom",
                md_bg_color=bg_color,
                padding="16dp"
            )
            palette_box.add_widget(MDLabel(
                text=f"Primary Color Accent: {self.app_palette.capitalize()}",
                halign="center", adaptive_height=True
            ))

            palette_grid = MDGridLayout(cols=3, spacing="8dp", adaptive_height=True)
            palettes = [
                ("teal", "Teal"), ("indigo", "Indigo"), ("blue", "Blue"),
                ("orange", "Orange"), ("purple", "Purple"), ("red", "Red"),
            ]
            for pal_key, pal_label in palettes:
                btn_style = "filled" if self.app_palette == pal_key else "outlined"
                btn = LightButton(text=pal_label, filled=(self.app_palette == pal_key), on_release=lambda x, pal=pal_key: self.set_app_palette(pal))
                palette_grid.add_widget(btn)

            palette_box.add_widget(palette_grid)
            layout.add_widget(palette_box)
            Clock.schedule_once(add_theme_section, 0.03)

        def add_theme_section(_dt=None):
            theme_box = MDBoxLayout(
                orientation="vertical",
                spacing="12dp",
                adaptive_height=True,
                theme_bg_color="Custom",
                md_bg_color=bg_color,
                padding="16dp"
            )
            theme_box.add_widget(MDLabel(
                text=f"Background Theme: {self.app_theme_style}",
                halign="center", adaptive_height=True
            ))

            theme_grid = MDGridLayout(cols=3, spacing="8dp", adaptive_height=True)
            for t_mode in ["Dark", "Light", "AMOLED"]:
                t_style = "filled" if self.app_theme_style == t_mode else "outlined"
                b = LightButton(text=t_mode, filled=(self.app_theme_style == t_mode), on_release=lambda x, m=t_mode: self.set_theme_style(m))
                theme_grid.add_widget(b)

            theme_box.add_widget(theme_grid)
            layout.add_widget(theme_box)
            Clock.schedule_once(add_icon_section, 0.03)

        def add_icon_section(_dt=None):
            icon_box = MDBoxLayout(
                orientation="vertical",
                spacing="12dp",
                adaptive_height=True,
                theme_bg_color="Custom",
                md_bg_color=bg_color,
                padding="16dp"
            )
            icon_box.add_widget(MDLabel(text="Water Icon Style", halign="center", adaptive_height=True))

            icon_grid = MDGridLayout(cols=3, spacing="16dp", adaptive_height=True)
            icons = [
                ("water", "Drop"), ("glass-mug-variant", "Mug"), ("cup-water", "Cup"),
            ]
            for ic_name, ic_label in icons:
                ic_style = "tonal" if self.water_icon_name == ic_name else "standard"
                ic_btn = LightIconButton(
                    icon=ic_name, tonal=(ic_style == "tonal"),
                    on_release=lambda x, ic=ic_name: self.set_water_icon(ic)
                )
                icon_grid.add_widget(ic_btn)

            icon_box.add_widget(icon_grid)
            layout.add_widget(icon_box)
            Clock.schedule_once(add_rows_section, 0.03)

        def add_rows_section(_dt=None):
            rows_box = MDBoxLayout(
                orientation="vertical",
                spacing="12dp",
                adaptive_height=True,
                theme_bg_color="Custom",
                md_bg_color=bg_color,
                padding="16dp"
            )
            rows_box.add_widget(MDLabel(
                text=f"Visible Rows: {self.visible_rows_count} (Range: 2-5)",
                halign="center", adaptive_height=True
            ))

            btn_row_layout = MDGridLayout(cols=2, spacing="16dp", adaptive_height=True)
            btn_minus_row = LightButton(text="- Row", filled=False, on_release=lambda x: self.change_visible_rows(-1))

            btn_plus_row = LightButton(text="+ Row", filled=True, on_release=lambda x: self.change_visible_rows(1))

            btn_row_layout.add_widget(btn_minus_row)
            btn_row_layout.add_widget(btn_plus_row)
            rows_box.add_widget(btn_row_layout)

            layout.add_widget(rows_box)
            Clock.schedule_once(add_target_section, 0.03)

        def add_target_section(_dt=None):
            target_box = MDBoxLayout(
                orientation="vertical",
                spacing="12dp",
                adaptive_height=True,
                theme_bg_color="Custom",
                md_bg_color=bg_color,
                padding="16dp"
            )
            target_field = MDTextField(
                mode="outlined",
                text=str(self.water_target_val),
                pos_hint={"center_x": 0.5}
            )
            target_field.add_widget(MDTextFieldHintText(text="Daily Water Target (ml)"))
            target_field.bind(text=self.update_water_target)
            target_field.bind(focus=_refocus_keyboard)

            target_box.add_widget(target_field)
            layout.add_widget(target_box)

        add_palette_section()

    def set_app_palette(self, palette_name):
        self.app_palette = palette_name
        self.apply_visual_theme()
        self.save_database()
        self.build_settings_screen()
        Clock.schedule_once(lambda dt: self.build_tracker_screen(), 0.1)
        Clock.schedule_once(lambda dt: self._refresh_nav_bar_colors(), 0.2)

    def set_theme_style(self, style_name):
        self.app_theme_style = style_name
        self.apply_visual_theme()
        self.save_database()
        self.build_settings_screen()
        Clock.schedule_once(lambda dt: self.build_tracker_screen(), 0.1)
        Clock.schedule_once(lambda dt: self._refresh_nav_bar_colors(), 0.2)

    def _refresh_nav_bar_colors(self):
        """Пересчитывает подсветку вкладок под новый акцент/тему без
        полной пересборки MDNavigationBar-замены (LightTabItem)."""
        if hasattr(self, "_tab_items"):
            for item in self._tab_items.values():
                item._apply_selected()

    def set_water_icon(self, icon_name):
        self.water_icon_name = icon_name
        if hasattr(self, "_tab_items"):
            self._tab_items["tracker_screen"]._icon.icon = icon_name
        self.save_database()
        self.build_tracker_screen()
        self.build_settings_screen()

    def change_visible_rows(self, delta):
        new_val = self.visible_rows_count + delta
        if 2 <= new_val <= 5:
            self.visible_rows_count = new_val
            self.save_database()
            self.build_tracker_screen()
            self.build_settings_screen()

    def update_water_target(self, instance, value):
        if value.isdigit():
            val = int(value)
            if val > 0:
                self.water_target_val = val
                # Печать в текстовое поле — тоже debounce внутри save_database,
                # чтобы не улетать в облако на каждую введённую цифру.
                self.save_database()
                self.build_tracker_screen()


if __name__ == "__main__":
    HealthTrackerApp().run()
