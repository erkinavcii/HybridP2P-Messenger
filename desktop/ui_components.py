"""desktop/ui_components.py — Küçük, tekrar kullanılan UI yardımcıları.

client.py'den taşındı (modülerleştirme): log_status, copy_to_clipboard,
send_read_receipt, update_connection_status. Bunlar hemen hemen her diğer
mixin tarafından çağrılan ortak yardımcılardır.
"""

import flet as ft
from desktop.theme import C


class UiComponentsMixin:

    def log_status(self, msg: str):
        def _update():
            self.status_text.value = msg
            try: self.page.update()
            except: pass
        self.run_on_ui(_update)

    def copy_to_clipboard(self, text: str):
        async def do_copy():
            try:
                await ft.Clipboard().set(text)
            except Exception as ex:
                print(f"[Clipboard Error] {ex}")
        self.page.run_task(do_copy)

    def send_read_receipt(self, recipient: str, timestamp: str):
        if self.state.get("is_group", False) or not recipient or not timestamp:
            return
        self.send_ws_message_with_fallback({
            "type": "read_receipt",
            "recipient": recipient,
            "timestamp": timestamp
        })

    def update_connection_status(self, is_connected: bool):
        def _update():
            if is_connected:
                self.status_dot.bgcolor = C.info  # Blue
                self.status_label.value = "Server: Online"
                self.status_label.color = C.info_text
            else:
                self.status_dot.bgcolor = C.danger  # Red
                self.status_label.value = "Server: Offline"
                self.status_label.color = C.danger
            try: self.page.update()
            except: pass
        self.run_on_ui(_update)
