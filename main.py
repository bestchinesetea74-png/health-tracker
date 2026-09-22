from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.button import MDRectangleFlatButton
from kivymd.uix.label import MDLabel


class HealthTrackerApp(MDApp):
    """ДИАГНОСТИЧЕСКАЯ ВЕРСИЯ: полностью убраны googleapiclient,
    работа с файлами и весь HealthTrackerData — только чистый UI.
    Цель: проверить, запускается ли вообще голый kivymd на этом телефоне,
    и не является ли googleapiclient причиной краха."""

    def build(self):
        screen = MDScreen()

        self.label = MDLabel(
            text="Диагностика: UI работает!",
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
        self.label.text = "Кнопка нажата! Значит UI точно работает."


if __name__ == "__main__":
    HealthTrackerApp().run()
