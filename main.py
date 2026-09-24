import os
import json
import time
import base64
import calendar
import tempfile
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

import rsa as rsa_lib
from pyasn1.codec.der import decoder as der_decoder
from pyasn1_modules.rfc5208 import PrivateKeyInfo

from kivy.lang import Builder
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.properties import NumericProperty, StringProperty, DictProperty

from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.screenmanager import MDScreenManager
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.label import MDLabel
from kivymd.uix.navigationbar import MDNavigationBar, MDNavigationItem, MDNavigationItemIcon, MDNavigationItemLabel


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


def _pem_to_der(pem_text):
    """Убирает PEM-обёртку (-----BEGIN...-----) и декодирует base64 в DER-байты."""
    lines = [line.strip() for line in pem_text.strip().splitlines()]
    body = "".join(line for line in lines if not line.startswith("-----"))
    return base64.b64decode(body)


def load_pkcs8_rsa_private_key(pem_text):
    """Парсит приватный ключ сервис-аккаунта Google (формат PKCS8 PEM)
    и возвращает rsa.PrivateKey — БЕЗ cryptography/OpenSSL, только
    чистый Python (rsa + pyasn1). Так мы полностью обходим Rust-бинарник
    cryptography, который несовместим с Python 3.14 в текущей сборке p4a."""
    der_bytes = _pem_to_der(pem_text)
    private_key_info, _ = der_decoder.decode(der_bytes, asn1Spec=PrivateKeyInfo())
    raw_key_der = bytes(private_key_info.getComponentByName("privateKey"))
    return rsa_lib.PrivateKey.load_pkcs1(raw_key_der, format="DER")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class ServiceAccountJWTAuth:
    """Чисто-Python реализация OAuth2 Service Account JWT-flow (RFC 7523).
    Не использует google-auth/google-api-python-client/cryptography —
    только rsa + pyasn1 (pure Python, без компилируемых расширений)
    и стандартный urllib для HTTP."""

    def __init__(self, creds_path, scopes=None):
        with open(creds_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        self.client_email = info["client_email"]
        self.private_key = load_pkcs8_rsa_private_key(info["private_key"])
        self.token_uri = info.get("token_uri", "https://oauth2.googleapis.com/token")
        self.scope = " ".join(scopes or DEFAULT_SCOPES)
        self._access_token = None
        self._expires_at = 0

    def _mint_jwt(self):
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self.client_email,
            "scope": self.scope,
            "aud": self.token_uri,
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
            + "."
            + _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        )
        signature = rsa_lib.sign(signing_input.encode("ascii"), self.private_key, "SHA-256")
        return signing_input + "." + _b64url(signature)

    def get_access_token(self):
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        assertion = self._mint_jwt()
        data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }).encode("utf-8")

        req = urllib.request.Request(self.token_uri, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))

        self._access_token = token_data["access_token"]
        self._expires_at = time.time() + token_data.get("expires_in", 3600)
        return self._access_token


class GoogleDriveSync:
    """Синхронизация с Google Drive через прямые REST-вызовы Drive API v3
    (без googleapiclient) и Service Account JWT-аутентификацию на чистом
    Python (без google-auth/cryptography)."""

    def __init__(self, creds_path="credentials.json", remote_filename="tracker_history.json"):
        self.creds_path = creds_path
        self.remote_filename = remote_filename
        self.auth = None
        self.file_id = None  # кэшируем id после первого резолва — фикс дублей
        self.init_service()

    def init_service(self):
        if os.path.exists(self.creds_path):
            try:
                self.auth = ServiceAccountJWTAuth(self.creds_path)
                print("[DriveSync] Android: Drive auth initialized (pure Python).")
            except Exception as e:
                print(f"[DriveSync] Error initializing Drive auth: {e}")
                self.auth = None

    def _authed_request(self, method, url, headers=None, data=None, timeout=15):
        req_headers = {"Authorization": f"Bearer {self.auth.get_access_token()}"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        return urllib.request.urlopen(req, timeout=timeout)

    def _resolve_file_id(self):
        """Детерминированный поиск файла: при дублях берём самый старый
        по createdTime и логируем ситуацию, вместо тихого выбора files[0]
        в непредсказуемом порядке. Кэширует найденный id в self.file_id."""
        if not self.auth:
            return None
        try:
            query = urllib.parse.urlencode({
                "q": f"name = '{self.remote_filename}' and trashed = false",
                "fields": "files(id, name, createdTime)",
            })
            with self._authed_request("GET", f"{DRIVE_API}/files?{query}") as resp:
                result = json.loads(resp.read().decode("utf-8"))

            files = result.get("files", [])
            if len(files) > 1:
                print(f"[DriveSync] Warning: found {len(files)} duplicate files "
                      f"named '{self.remote_filename}'. Using the oldest deterministically.")
                files.sort(key=lambda x: x.get("createdTime", ""))

            self.file_id = files[0]["id"] if files else None
            return self.file_id
        except Exception as e:
            print(f"[DriveSync] Error resolving file id: {e}")
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
                meta = json.dumps({"name": self.remote_filename}).encode("utf-8")
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
            print(f"[DriveSync] Upload Error: {e}")

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
            print(f"[DriveSync] Download Error: {e}. Local file kept unchanged.")
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

        MDNavigationBar:
            MDNavigationItem:
                name: "tracker"
                active: True
                on_release: app.switch_screen("tracker_screen")
                MDNavigationItemIcon:
                    icon: app.water_icon_name
                MDNavigationItemLabel:
                    text: "Tracker"

            MDNavigationItem:
                name: "calendar"
                on_release: app.switch_screen("calendar_screen")
                MDNavigationItemIcon:
                    icon: "calendar-month"
                MDNavigationItemLabel:
                    text: "Calendar"

            MDNavigationItem:
                name: "settings"
                on_release: app.switch_screen("settings_screen")
                MDNavigationItemIcon:
                    icon: "cog"
                MDNavigationItemLabel:
                    text: "Settings"
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

    def build(self):
        self.drive_sync = GoogleDriveSync()
        # Debounce на аплоад в облако (0.5–1.5с по спеке); сам вызов идёт
        # внутри threading.Timer, то есть уже не в основном UI-потоке.
        self._upload_debouncer = Debouncer(1.0, self._perform_cloud_upload)

        # Локальная загрузка — быстрая операция с диском, безопасна в build().
        self.load_database()
        self.apply_visual_theme()

        return Builder.load_string(KV)

    def on_start(self):
        Clock.schedule_once(lambda dt: self.build_tracker_screen(), 0.1)
        Clock.schedule_once(lambda dt: self.build_calendar_screen(), 0.1)
        Clock.schedule_once(lambda dt: self.build_settings_screen(), 0.1)

        # Первичная синхронизация с Google Drive — в фоновом потоке,
        # чтобы не блокировать отрисовку UI при старте.
        threading.Thread(target=self._background_initial_sync, daemon=True).start()

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

        print("[DriveSync] Safe download completed and applied.")
        Clock.schedule_once(lambda dt: self._apply_remote_data(remote_data))

    def _apply_remote_data(self, remote_data):
        """Выполняется в основном потоке (через Clock.schedule_once)."""
        self.db_data = remote_data
        self._load_settings_from_db()
        self._load_today_from_db()
        self.apply_visual_theme()
        self.build_tracker_screen()
        self.build_calendar_screen()
        self.build_settings_screen()

    def _perform_cloud_upload(self):
        """Вызывается из Debouncer (уже в отдельном потоке от threading.Timer),
        поэтому не блокирует UI."""
        if hasattr(self, 'drive_sync'):
            self.drive_sync.upload_file(self.json_path)

    # --- локальное хранилище ------------------------------------------

    def switch_screen(self, screen_name):
        sm = self.root.ids.screen_manager
        sm.current = screen_name
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
            "vitamin_names": dict(self.vitamin_names)
        }

        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self.db_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving DB: {e}")
            return

        if upload and hasattr(self, "_upload_debouncer"):
            self._upload_debouncer.trigger()

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

        bg_color = (0, 0, 0, 1) if self.app_theme_style == "AMOLED" else (0.12, 0.12, 0.12, 1)

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
        water_icon = MDIconButton(icon=self.water_icon_name, style="standard")

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

        btn_m50 = MDButton(style="outlined", on_release=lambda x: self.change_glass_volume(-50))
        btn_m50.add_widget(MDButtonText(text="-50ml", halign="center"))

        btn_p50 = MDButton(style="outlined", on_release=lambda x: self.change_glass_volume(50))
        btn_p50.add_widget(MDButtonText(text="+50ml", halign="center"))

        btn_mgl = MDButton(style="outlined", on_release=lambda x: self.change_water_glass(-1))
        btn_mgl.add_widget(MDButtonText(text="-1 glass", halign="center"))

        btn_pgl = MDButton(style="outlined", on_release=lambda x: self.change_water_glass(1))
        btn_pgl.add_widget(MDButtonText(text="+1 glass", halign="center"))

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
            field.add_widget(MDTextFieldHintText(text=f"Vitamin #{i+1}"))
            field.bind(text=lambda instance, val, k=key: self.update_vit_name(k, val))

            count_label = MDLabel(
                text=f"{self.vitamin_counts.get(key, 0)} pcs",
                halign="center",
                size_hint_x=0.2,
                adaptive_height=True
            )

            btn_sub = MDButton(
                style="outlined",
                on_release=lambda x, k=key: self.change_vitamin(k, -1),
                size_hint_x=0.125
            )
            btn_sub.add_widget(MDButtonText(text="-"))

            btn_add = MDButton(
                style="outlined",
                on_release=lambda x, k=key: self.change_vitamin(k, 1),
                size_hint_x=0.125
            )
            btn_add.add_widget(MDButtonText(text="+"))

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

        reset_btn = MDButton(
            style="outlined",
            on_release=lambda x: self.reset_today()
        )
        reset_btn.add_widget(MDButtonText(text="Reset Today"))
        actions_layout.add_widget(reset_btn)

        sync_btn = MDButton(
            style="filled",
            theme_bg_color="Custom",
            md_bg_color=(0.15, 0.68, 0.37, 1),
            on_release=lambda x: self.manual_sync()
        )
        sync_btn.add_widget(MDButtonText(text="[ Sync Data ]"))
        actions_layout.add_widget(sync_btn)

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

        bg_color = (0, 0, 0, 1) if self.app_theme_style == "AMOLED" else (0.12, 0.12, 0.12, 1)

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
                btn = MDButton(style=btn_style, on_release=lambda x, pal=pal_key: self.set_app_palette(pal))
                btn.add_widget(MDButtonText(text=pal_label, halign="center"))
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
                b = MDButton(style=t_style, on_release=lambda x, m=t_mode: self.set_theme_style(m))
                b.add_widget(MDButtonText(text=t_mode, halign="center"))
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
                ("water", "Drop"), ("bottle-soda-classic", "Bottle"), ("cup-water", "Cup"),
            ]
            for ic_name, ic_label in icons:
                ic_style = "tonal" if self.water_icon_name == ic_name else "standard"
                ic_btn = MDIconButton(
                    icon=ic_name, style=ic_style,
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
                text=f"Visible Vitamin Rows: {self.visible_rows_count} (Range: 2-5)",
                halign="center", adaptive_height=True
            ))

            btn_row_layout = MDGridLayout(cols=2, spacing="16dp", adaptive_height=True)
            btn_minus_row = MDButton(style="outlined", on_release=lambda x: self.change_visible_rows(-1))
            btn_minus_row.add_widget(MDButtonText(text="- Row", halign="center"))

            btn_plus_row = MDButton(style="filled", on_release=lambda x: self.change_visible_rows(1))
            btn_plus_row.add_widget(MDButtonText(text="+ Row", halign="center"))

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

            target_box.add_widget(target_field)
            layout.add_widget(target_box)

        add_palette_section()

    def set_app_palette(self, palette_name):
        self.app_palette = palette_name
        self.apply_visual_theme()
        self.save_database()
        self.build_settings_screen()

    def set_theme_style(self, style_name):
        self.app_theme_style = style_name
        self.apply_visual_theme()
        self.save_database()
        self.build_settings_screen()

    def set_water_icon(self, icon_name):
        self.water_icon_name = icon_name
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
