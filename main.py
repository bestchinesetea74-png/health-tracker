import os
import json
import calendar
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from kivy.lang import Builder
from kivy.clock import Clock
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


class GoogleDriveSync:
    def __init__(self, creds_path="credentials.json", remote_filename="tracker_history.json"):
        self.creds_path = creds_path
        self.remote_filename = remote_filename
        self.service = None
        self.init_service()

    def init_service(self):
        if os.path.exists(self.creds_path):
            try:
                scopes = ['https://www.googleapis.com/auth/drive']
                creds = Credentials.from_service_account_file(self.creds_path, scopes=scopes)
                self.service = build('drive', 'v3', credentials=creds)
                print("[DriveSync] Android: Drive Service Initialized.")
            except Exception as e:
                print(f"[DriveSync] Error initializing Drive Service: {e}")

    def upload_file(self, local_filepath):
        if not self.service or not os.path.exists(local_filepath):
            return
        try:
            query = f"name = '{self.remote_filename}' and trashed = false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])
            media = MediaFileUpload(local_filepath, mimetype='application/json')

            if files:
                self.service.files().update(fileId=files[0]['id'], media_body=media).execute()
                print("[DriveSync] Android: DB updated on Drive.")
            else:
                self.service.files().create(body={'name': self.remote_filename}, media_body=media, fields='id').execute()
                print("[DriveSync] Android: DB created on Drive.")
        except Exception as e:
            print(f"[DriveSync] Upload Error: {e}")

    def download_file(self, local_filepath):
        if not self.service:
            return False
        try:
            query = f"name = '{self.remote_filename}' and trashed = false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])

            if files:
                request = self.service.files().get_media(fileId=files[0]['id'])
                with open(local_filepath, 'wb') as f:
                    f.write(request.execute())
                print("[DriveSync] Android: DB downloaded from Drive.")
                return True
        except Exception as e:
            print(f"[DriveSync] Download Error: {e}")
        return False


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

    def build(self):
        self.drive_sync = GoogleDriveSync()
        self.drive_sync.download_file(self.json_path)
        self.load_database()
        self.apply_visual_theme()
        return Builder.load_string(KV)

    def on_start(self):
        Clock.schedule_once(lambda dt: self.build_tracker_screen(), 0.1)
        Clock.schedule_once(lambda dt: self.build_calendar_screen(), 0.1)
        Clock.schedule_once(lambda dt: self.build_settings_screen(), 0.1)

    def manual_sync(self):
        print("[Android] Manual sync requested...")
        if hasattr(self, 'drive_sync'):
            self.drive_sync.download_file(self.json_path)
        self.load_database()
        self.build_tracker_screen()
        print("[Android] Manual sync complete!")

    def switch_screen(self, screen_name):
        sm = self.root.ids.screen_manager
        if screen_name == "tracker_screen":
            self.build_tracker_screen()
        elif screen_name == "calendar_screen":
            self.build_calendar_screen()
        elif screen_name == "settings_screen":
            self.build_settings_screen()
        sm.current = screen_name

    def get_today_str(self):
        return datetime.now().strftime("%Y-%m-%d")

    def load_database(self):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.db_data = json.load(f)
            except Exception as e:
                self.db_data = {}
        else:
            self.db_data = {}

        settings = self.db_data.get("settings", {})
        self.glass_volume = settings.get("glass_volume", 250)
        self.visible_rows_count = settings.get("visible_rows_count", 3)
        self.water_target_val = settings.get("water_target_val", 2000)

        saved_names = settings.get("vitamin_names", {})
        for key, val in saved_names.items():
            self.vitamin_names[key] = val

        today_key = self.get_today_str()
        today_data = self.db_data.get(today_key, {})

        self.water_ml = today_data.get("water_ml", 0)
        for i in range(5):
            k = f"vit{i}"
            self.vitamin_counts[k] = today_data.get(k, 0)

    def save_database(self):
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

            if hasattr(self, 'drive_sync'):
                self.drive_sync.upload_file(self.json_path)
        except Exception as e:
            print(f"Error saving DB: {e}")

    def apply_visual_theme(self):
        self.theme_cls.primary_palette = self.app_palette
        if self.app_theme_style == "AMOLED":
            self.theme_cls.theme_style = "Dark"
        else:
            self.theme_cls.theme_style = self.app_theme_style

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

        btn_grid = MDBoxLayout(
            orientation="horizontal",
            spacing="8dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )

        btn_m50 = MDButton(style="outlined", on_release=lambda x: self.change_glass_volume(-50))
        btn_m50.add_widget(MDButtonText(text="-50ml"))

        btn_p50 = MDButton(style="outlined", on_release=lambda x: self.change_glass_volume(50))
        btn_p50.add_widget(MDButtonText(text="+50ml"))

        btn_mgl = MDButton(style="outlined", on_release=lambda x: self.change_water_glass(-1))
        btn_mgl.add_widget(MDButtonText(text="-1 gl"))

        btn_pgl = MDButton(style="outlined", on_release=lambda x: self.change_water_glass(1))
        btn_pgl.add_widget(MDButtonText(text="+1 gl"))

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

        for week in month_calendar:
            for day in week:
                if day == 0:
                    grid.add_widget(MDBoxLayout(size_hint_y=None, height="60dp"))
                else:
                    date_str = f"{year}-{month:02d}-{day:02d}"
                    status_color, label_text = self.evaluate_day_status(date_str, day)

                    day_btn = MDButton(
                        style="filled",
                        theme_bg_color="Custom",
                        md_bg_color=status_color,
                        size_hint_y=None,
                        height="60dp"
                    )
                    btn_txt = MDButtonText(
                        text=label_text,
                        halign="center"
                    )
                    day_btn.add_widget(btn_txt)
                    grid.add_widget(day_btn)

        layout.add_widget(grid)
        scroll.add_widget(layout)
        container.add_widget(scroll)

    def evaluate_day_status(self, date_str, day_num):
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

        bg_color = (0, 0, 0, 1) if self.app_theme_style == "AMOLED" else (0.12, 0.12, 0.12, 1)

        palette_box = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True,
            theme_bg_color="Custom",
            md_bg_color=bg_color,
            padding="16dp"
        )
        palette_box.add_widget(MDLabel(text=f"Primary Color Accent: {self.app_palette.capitalize()}", halign="center", adaptive_height=True))

        palette_grid = MDBoxLayout(
            orientation="horizontal",
            spacing="8dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )
        palettes = [
            ("teal", "Teal"),
            ("indigo", "Indigo"),
            ("blue", "Blue"),
            ("orange", "Orange"),
            ("purple", "Purple"),
            ("red", "Red")
        ]
        for pal_key, pal_label in palettes:
            btn_style = "filled" if self.app_palette == pal_key else "outlined"
            btn = MDButton(style=btn_style, on_release=lambda x, pal=pal_key: self.set_app_palette(pal))
            btn.add_widget(MDButtonText(text=pal_label))
            palette_grid.add_widget(btn)

        palette_box.add_widget(palette_grid)
        layout.add_widget(palette_box)

        theme_box = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True,
            theme_bg_color="Custom",
            md_bg_color=bg_color,
            padding="16dp"
        )
        theme_box.add_widget(MDLabel(text=f"Background Theme: {self.app_theme_style}", halign="center", adaptive_height=True))

        theme_grid = MDBoxLayout(
            orientation="horizontal",
            spacing="8dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )

        for t_mode in ["Dark", "Light", "AMOLED"]:
            t_style = "filled" if self.app_theme_style == t_mode else "outlined"
            b = MDButton(style=t_style, on_release=lambda x, m=t_mode: self.set_theme_style(m))
            b.add_widget(MDButtonText(text=t_mode))
            theme_grid.add_widget(b)

        theme_box.add_widget(theme_grid)
        layout.add_widget(theme_box)

        icon_box = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True,
            theme_bg_color="Custom",
            md_bg_color=bg_color,
            padding="16dp"
        )
        icon_box.add_widget(MDLabel(text="Water Icon Style", halign="center", adaptive_height=True))

        icon_grid = MDBoxLayout(
            orientation="horizontal",
            spacing="16dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )
        icons = [
            ("water", "Drop"),
            ("bottle-soda-classic", "Bottle"),
            ("cup-water", "Cup")
        ]
        for ic_name, ic_label in icons:
            ic_style = "tonal" if self.water_icon_name == ic_name else "standard"
            ic_btn = MDIconButton(
                icon=ic_name,
                style=ic_style,
                on_release=lambda x, ic=ic_name: self.set_water_icon(ic)
            )
            icon_grid.add_widget(ic_btn)

        icon_box.add_widget(icon_grid)
        layout.add_widget(icon_box)

        rows_box = MDBoxLayout(
            orientation="vertical",
            spacing="12dp",
            adaptive_height=True,
            theme_bg_color="Custom",
            md_bg_color=bg_color,
            padding="16dp"
        )
        rows_lbl = MDLabel(
            text=f"Visible Vitamin Rows: {self.visible_rows_count} (Range: 2-5)",
            halign="center",
            adaptive_height=True
        )
        rows_box.add_widget(rows_lbl)

        btn_row_layout = MDBoxLayout(
            orientation="horizontal",
            spacing="16dp",
            adaptive_height=True,
            pos_hint={"center_x": 0.5}
        )
        btn_minus_row = MDButton(style="outlined", on_release=lambda x: self.change_visible_rows(-1))
        btn_minus_row.add_widget(MDButtonText(text="- Row"))

        btn_plus_row = MDButton(style="filled", on_release=lambda x: self.change_visible_rows(1))
        btn_plus_row.add_widget(MDButtonText(text="+ Row"))

        btn_row_layout.add_widget(btn_minus_row)
        btn_row_layout.add_widget(btn_plus_row)
        rows_box.add_widget(btn_row_layout)

        layout.add_widget(rows_box)

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

        scroll.add_widget(layout)
        container.add_widget(scroll)

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
                self.save_database()
                self.build_tracker_screen()


if __name__ == "__main__":
    HealthTrackerApp().run()
