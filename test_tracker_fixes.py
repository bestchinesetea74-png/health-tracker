"""
Тесты для исправлений из bug-fix pass:
1. Safe download — ошибка/невалидный JSON не портит локальный файл.
2. Восстановление UI-настроек (app_palette, app_theme_style, water_icon_name).
3. Debounce для облачной синхронизации.

Тесты работают с чистыми функциями/классами (merge_settings_with_defaults,
validate_remote_json, atomic_write_json, Debouncer, GoogleDriveSync с
замоканным service) и не требуют запуска Kivy-приложения или реальной сети.
"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from main import (
    merge_settings_with_defaults,
    validate_remote_json,
    atomic_write_json,
    Debouncer,
    GoogleDriveSync,
    DEFAULT_SETTINGS,
)


class TestValidateRemoteJson(unittest.TestCase):
    def test_valid_json_bytes(self):
        raw = json.dumps({"a": 1}).encode("utf-8")
        self.assertEqual(validate_remote_json(raw), {"a": 1})

    def test_valid_json_str(self):
        raw = json.dumps({"a": 1})
        self.assertEqual(validate_remote_json(raw), {"a": 1})

    def test_invalid_json_returns_none(self):
        self.assertIsNone(validate_remote_json(b"{not valid json"))

    def test_non_dict_json_returns_none(self):
        # Валидный JSON, но не объект (например список) — тоже отклоняем.
        self.assertIsNone(validate_remote_json(json.dumps([1, 2, 3])))


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "tracker_history.json")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rmdir(self.tmpdir)

    def test_write_creates_file_with_content(self):
        ok = atomic_write_json(self.path, {"hello": "world"})
        self.assertTrue(ok)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"hello": "world"})

    def test_write_replaces_existing_file_atomically(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"old": True}, f)

        ok = atomic_write_json(self.path, {"new": True})
        self.assertTrue(ok)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"new": True})

    def test_no_leftover_temp_files_after_success(self):
        atomic_write_json(self.path, {"a": 1})
        leftovers = [
            f for f in os.listdir(self.tmpdir)
            if f != os.path.basename(self.path)
        ]
        self.assertEqual(leftovers, [])


class FakeHTTPResponse:
    """Имитирует объект, возвращаемый urllib.request.urlopen (поддержка
    контекстного менеджера и .read())."""

    def __init__(self, payload_bytes):
        self._payload = payload_bytes

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestSafeDownloadKeepsLocalFileIntact(unittest.TestCase):
    """Ключевой сценарий из ТЗ: ошибка скачивания или невалидный JSON
    не должны затирать существующий локальный файл."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.local_path = os.path.join(self.tmpdir, "tracker_history.json")
        self.original_content = {"2026-01-01": {"water_ml": 500}}
        with open(self.local_path, "w", encoding="utf-8") as f:
            json.dump(self.original_content, f)

    def tearDown(self):
        if os.path.exists(self.local_path):
            os.remove(self.local_path)
        os.rmdir(self.tmpdir)

    def _read_local(self):
        with open(self.local_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _make_sync_with_fake_auth(self):
        sync = GoogleDriveSync.__new__(GoogleDriveSync)  # без реального __init__/сети
        sync.auth = MagicMock()
        sync.auth.get_access_token.return_value = "fake-token"
        sync.file_id = "fake-id"
        sync.remote_filename = "tracker_history.json"
        return sync

    def test_network_exception_keeps_local_file_unchanged(self):
        sync = self._make_sync_with_fake_auth()
        sync._authed_request = MagicMock(side_effect=Exception("network timeout"))

        success, data = sync.download_file_safe()

        self.assertFalse(success)
        self.assertIsNone(data)
        self.assertEqual(self._read_local(), self.original_content)

    def test_invalid_remote_json_keeps_local_file_unchanged(self):
        sync = self._make_sync_with_fake_auth()
        sync._authed_request = MagicMock(return_value=FakeHTTPResponse(b"{not valid json"))

        success, data = sync.download_file_safe()

        self.assertFalse(success)
        self.assertIsNone(data)
        self.assertEqual(self._read_local(), self.original_content)

    def test_valid_remote_json_can_replace_local_file(self):
        sync = self._make_sync_with_fake_auth()
        new_remote_data = {"2026-06-01": {"water_ml": 1500}}
        sync._authed_request = MagicMock(
            return_value=FakeHTTPResponse(json.dumps(new_remote_data).encode("utf-8"))
        )

        success, data = sync.download_file_safe()
        self.assertTrue(success)
        self.assertEqual(data, new_remote_data)

        # Применение — отдельным шагом, как в _safe_download_and_apply().
        ok = atomic_write_json(self.local_path, data)
        self.assertTrue(ok)
        self.assertEqual(self._read_local(), new_remote_data)

    def test_no_auth_keeps_local_file_unchanged(self):
        # Например, credentials.json отсутствует — auth не создан.
        sync = GoogleDriveSync.__new__(GoogleDriveSync)
        sync.auth = None
        sync.file_id = None

        success, data = sync.download_file_safe()

        self.assertFalse(success)
        self.assertIsNone(data)
        self.assertEqual(self._read_local(), self.original_content)


class TestSettingsRestoration(unittest.TestCase):
    def test_full_settings_restored(self):
        saved = {
            "glass_volume": 300,
            "visible_rows_count": 4,
            "water_target_val": 2500,
            "app_palette": "purple",
            "app_theme_style": "AMOLED",
            "water_icon_name": "cup-water",
            "vitamin_names": {"vit0": "Omega-3"},
        }
        result = merge_settings_with_defaults(saved)
        self.assertEqual(result, saved)

    def test_old_json_without_theme_fields_uses_safe_defaults(self):
        # Старый JSON — только то, что сохранялось до этого патча.
        old_settings = {
            "glass_volume": 300,
            "visible_rows_count": 4,
            "water_target_val": 2500,
        }
        result = merge_settings_with_defaults(old_settings)

        self.assertEqual(result["app_palette"], DEFAULT_SETTINGS["app_palette"])
        self.assertEqual(result["app_theme_style"], DEFAULT_SETTINGS["app_theme_style"])
        self.assertEqual(result["water_icon_name"], DEFAULT_SETTINGS["water_icon_name"])
        # Существующие поля не теряются.
        self.assertEqual(result["glass_volume"], 300)

    def test_empty_settings_does_not_raise(self):
        result = merge_settings_with_defaults({})
        self.assertEqual(result, DEFAULT_SETTINGS | {"vitamin_names": {}})

    def test_none_settings_does_not_raise(self):
        result = merge_settings_with_defaults(None)
        self.assertEqual(result["app_palette"], DEFAULT_SETTINGS["app_palette"])


class TestDebouncer(unittest.TestCase):
    def test_multiple_rapid_triggers_call_callback_once(self):
        calls = []
        deb = Debouncer(delay=0.05, callback=lambda: calls.append(1))

        for _ in range(10):
            deb.trigger()
            time.sleep(0.005)  # быстрые повторные вызовы внутри окна debounce

        time.sleep(0.2)  # ждём срабатывания таймера
        self.assertEqual(len(calls), 1)

    def test_trigger_after_delay_calls_callback_again(self):
        calls = []
        deb = Debouncer(delay=0.05, callback=lambda: calls.append(1))

        deb.trigger()
        time.sleep(0.15)
        deb.trigger()
        time.sleep(0.15)

        self.assertEqual(len(calls), 2)

    def test_cancel_prevents_callback(self):
        calls = []
        deb = Debouncer(delay=0.05, callback=lambda: calls.append(1))

        deb.trigger()
        deb.cancel()
        time.sleep(0.15)

        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main()
