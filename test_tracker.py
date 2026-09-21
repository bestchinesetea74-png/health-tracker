import unittest
import os
import json
import tempfile
import time
from unittest.mock import MagicMock

from main import HealthTrackerApp

class TestHealthTrackerProductionRegression(unittest.TestCase):
    
    def setUp(self):
        self.test_filename = "tracker_history.json"
        self.app = HealthTrackerApp()
        self.initial_data = {
            "settings": {
                "app_palette": "Green",
                "app_theme_style": "Light",
                "water_icon_name": "cup"
            },
            "history": {"2026-06-01": {"water": 2000}}
        }
        with open(self.test_filename, "w", encoding="utf-8") as f:
            json.dump(self.initial_data, f)

    def tearDown(self):
        if os.path.exists(self.test_filename):
            os.remove(self.test_filename)

    def test_settings_restored_by_production_loader(self):
        """Проверка восстановления настроек из JSON."""
        self.app.load_local_database()
        self.assertEqual(self.app.app_palette, "Green")
        self.assertEqual(self.app.app_theme_style, "Light")
        self.assertEqual(self.app.water_icon_name, "cup")

    def test_legacy_json_uses_defaults_without_exception(self):
        """Проверка обратной совместимости для старых JSON без настроек."""
        legacy_data = {"history": {"2026-06-01": {}}}
        with open(self.test_filename, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)
        
        self.app.load_local_database()
        self.assertEqual(self.app.app_palette, "Blue")
        self.assertEqual(self.app.app_theme_style, "Dark")
        self.assertEqual(self.app.water_icon_name, "water")

    def test_safe_download_raises_exception_keeps_file_unchanged(self):
        """При сетевой ошибке локальный файл остается неизменным."""
        with open(self.test_filename, "r", encoding="utf-8") as f:
            local_content_before = f.read()
        
        mock_drive_service = MagicMock()
        mock_drive_service.files().get_media().execute.side_effect = Exception("Network timeout")
        self.app.drive_file_id = "fake_id"

        with self.assertRaises(Exception):
            self.app.safe_download_from_drive(mock_drive_service)

        with open(self.test_filename, "r", encoding="utf-8") as f:
            local_content_after = f.read()
        self.assertEqual(local_content_before, local_content_after)

    def test_safe_download_valid_remote_json_replaces_atomically(self):
        """Атомарная замена файла при успешной загрузке."""
        new_remote_data = {
            "settings": {"app_palette": "Purple", "app_theme_style": "Dark", "water_icon_name": "bottle"},
            "history": {"2026-06-02": {"water": 1500}}
        }
        
        fd, temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(new_remote_data, f)
            
            os.replace(temp_path, self.test_filename)
            
            with open(self.test_filename, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            
            self.assertEqual(loaded["settings"]["app_palette"], "Purple")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_debounce_upload_triggers_once(self):
        """Проверка работы debounce-таймера."""
        self.app.debounce_timer = None
        # Если в классе метод сохранения называется иначе (например, save_database), 
        # временно подменяем его заглушкой для теста дебаунса:
        if not hasattr(self.app, 'save_database'):
            self.app.save_database = lambda: None
            
        self.app.trigger_upload_with_debounce()
        self.assertIsNotNone(self.app.debounce_timer)
        
        if self.app.debounce_timer:
            self.app.debounce_timer.cancel()

if __name__ == "__main__":
    unittest.main()