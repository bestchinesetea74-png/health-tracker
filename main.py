import json
import os
import tempfile
import threading

from kivy.clock import Clock
from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.button import MDRectangleFlatButton
from kivymd.uix.label import MDLabel

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


class HealthTrackerData:
    """Слой данных: хранение, чтение и синхронизация с Google Drive.
    Не наследуется от MDApp — это чисто логика, отделённая от интерфейса,
    чтобы не было конфликта имён с классом приложения HealthTrackerApp(MDApp).
    """

    def __init__(self):
        self.drive_file_id = None
        self.debounce_timer = None
        # Безопасные дефолты настроек с обратной совместимостью
        self.app_palette = "Blue"
        self.app_theme_style = "Dark"
        self.water_icon_name = "water"

    def load_local_database(self):
        """Загрузка локального JSON с восстановлением UI-настроек."""
        if not os.path.exists("tracker_history.json"):
            return {}
        try:
            with open("tracker_history.json", "r", encoding="utf-8") as f:
                data = json.load(f)

            settings = data.get("settings", {})
            self.app_palette = settings.get("app_palette", "Blue")
            self.app_theme_style = settings.get("app_theme_style", "Dark")
            self.water_icon_name = settings.get("water_icon_name", "water")

            return data
        except Exception as e:
            print(f"[Error] Failed to load local JSON: {e}")
            return {}

    def save_local_database(self, history=None):
        """Локальное сохранение текущего состояния базы данных и настроек."""
        data = {
            "settings": {
                "app_palette": self.app_palette,
                "app_theme_style": self.app_theme_style,
                "water_icon_name": self.water_icon_name,
            },
            "history": history if history is not None else {},
        }
        if history is None and os.path.exists("tracker_history.json"):
            try:
                with open("tracker_history.json", "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if isinstance(existing, dict) and "history" in existing:
                        data["history"] = existing["history"]
            except Exception:
                pass
        with open("tracker_history.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    def safe_download_from_drive(self, drive_service):
        """Безопасное скачивание во временный файл с атомарной заменой."""
        temp_path = None
        try:
            if not self.drive_file_id:
                self.drive_file_id = self._resolve_file_id(drive_service)

            if not self.drive_file_id:
                return

            request = drive_service.files().get_media(fileId=self.drive_file_id)
            fd, temp_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)

            with open(temp_path, "wb") as temp_file:
                downloader = MediaIoBaseDownload(temp_file, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

            # Проверка валидности скачанного JSON перед заменой
            with open(temp_path, "r", encoding="utf-8") as f:
                remote_data = json.load(f)

            # Атомарная замена только после успешной проверки
            os.replace(temp_path, "tracker_history.json")
            print("[Sync] Safe download completed and applied.")

            Clock.schedule_once(lambda dt: self.apply_loaded_settings(remote_data))

        except Exception as e:
            print(f"[Sync Error] Download failed or invalid JSON, keeping local file intact: {e}")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def _resolve_file_id(self, service):
        """Детерминированный поиск файла на Google Диске во избежание дубликатов."""
        results = service.files().list(
            q="name='tracker_history.json' and trashed=false",
            spaces="drive",
            fields="files(id, name, createdTime)",
        ).execute()
        files = results.get("files", [])

        if len(files) > 1:
            print(f"[Warning] Found {len(files)} duplicate files. Using the oldest deterministically.")
            files.sort(key=lambda x: x.get("createdTime", ""))

        if files:
            return files[0]["id"]
        return None

    def trigger_upload_with_debounce(self, history=None):
        """Запуск отложенной синхронизации (debounce на 1 секунду) при изменении полей."""
        if self.debounce_timer:
            self.debounce_timer.cancel()

        self.save_local_database(history)
        self.debounce_timer = threading.Timer(1.0, self.background_upload)
        self.debounce_timer.start()

    def background_upload(self):
        threading.Thread(target=self._perform_drive_upload, daemon=True).start()

    def _perform_drive_upload(self):
        """Заглушка — реальная выгрузка требует настроенного drive_service
        и авторизации (OAuth Device Flow под Android — отдельная задача)."""
        if not os.path.exists("tracker_history.json"):
            return
        media = MediaFileUpload("tracker_history.json", mimetype="application/json")
        print("[Sync] Локальный файл готов к выгрузке (drive_service ещё не подключён).")

    def apply_loaded_settings(self, data):
        settings = data.get("settings", {})
        self.app_palette = settings.get("app_palette", "Blue")
        self.app_theme_style = settings.get("app_theme_style", "Dark")
        self.water_icon_name = settings.get("water_icon_name", "water")


class HealthTrackerApp(MDApp):
    """UI-приложение. Имя класса намеренно НЕ совпадает по смыслу
    с HealthTrackerData — оба класса сосуществуют без конфликта,
    т.к. называются по-разному."""

    def build(self):
        self.data = HealthTrackerData()
        self.data.load_local_database()

        self.theme_cls.theme_style = self.data.app_theme_style
        self.theme_cls.primary_palette = self.data.app_palette

        screen = MDScreen()

        self.label = MDLabel(
            text="Трекер здоровья",
            halign="center",
            font_style="H4",
            pos_hint={"center_y": 0.7},
        )

        self.button = MDRectangleFlatButton(
            text="Нажми меня",
            pos_hint={"center_x": 0.5, "center_y": 0.4},
            on_release=self.on_button_press,
        )

        screen.add_widget(self.label)
        screen.add_widget(self.button)

        return screen

    def on_button_press(self, instance):
        self.label.text = "Кнопка нажата!"


if __name__ == "__main__":
    HealthTrackerApp().run()
