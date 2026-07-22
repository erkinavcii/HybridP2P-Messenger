"""desktop/chat_logic.py — Sohbet verisi, ephemeral mod ve dosya ekleme mantığı.

client.py'den taşındı (modülerleştirme): _fmt_time, add_message_to_chat,
add_file_to_chat, add_system_event, _on_incoming_message, _on_incoming_file,
load_history_to_chat, toggle_ephemeral, _update_ephemeral_ui, copy_public_key,
_on_ephemeral_toggle_received, toggle_view_once_msg, on_attach_click.

NOT (bug fix, agents.md'nin bağlayıcı UI thread-safety kuralı gereği):
`_update_ephemeral_ui` orijinalinde `page.update()`'i doğrudan çağırıyordu ve
`sync_chat_settings` üzerinden `background_sync` thread'inden (arka plan
thread'i) tetiklenebiliyordu — KNOWN_ISSUES.md'nin belgelediği donma
sınıfının belgelenmemiş canlı bir örneği. Burada `self.run_on_ui(...)` ile
sarmalandı.
"""

import os
import json
from datetime import datetime

import flet as ft

from desktop.net_config import _guess_file_type


class ChatLogicMixin:

    def _fmt_time(self, ts: str) -> str:
        try:
            ts_norm = ts.replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(ts_norm)
            if dt.tzinfo is None:
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%H:%M")
        except: return ts[:5] if ts else ""

    def add_message_to_chat(self, sender: str, text: str, is_mine: bool,
                             time_str: str = "", save: bool = True,
                             view_once: bool = False, encrypted_payload: str = "",
                             is_read: bool = True):
        from datetime import timezone
        if not time_str:
            time_str = datetime.now(timezone.utc).isoformat()

        raw_ts = time_str
        display_ts = self._fmt_time(time_str)

        if view_once:
            bubble = self.create_view_once_bubble(sender, display_ts, is_mine, encrypted_payload, plaintext_fallback=text)
        else:
            bubble = self.create_message_bubble(sender, text, display_ts, is_mine, is_read=is_read)

        self.chat_list.controls.append(bubble)

        if save and self.state["recipient"] and self.state["store"] and not view_once:
            self.state["store"].save_message(
                partner=self.state["recipient"], sender=sender,
                content=text, is_mine=is_mine, timestamp=raw_ts, is_read=(0 if is_mine else 1)
            )
        try: self.page.update()
        except: pass

    def add_file_to_chat(self, sender: str, file_uuid: str, original_name: str,
                          file_type: str, is_mine: bool, time_str: str = "",
                          view_once: bool = False):
        from datetime import timezone
        if not time_str:
            time_str = datetime.now(timezone.utc).isoformat()
        time_str = self._fmt_time(time_str)

        bubble = self.create_file_bubble(sender, file_uuid, original_name,
                                     file_type, time_str, is_mine, view_once)
        self.chat_list.controls.append(bubble)
        try: self.page.update()
        except: pass

    def add_system_event(self, text: str, partner: str = None):
        self.chat_list.controls.append(self.create_system_bubble(text))
        if partner and self.state["store"] and not self.state["ephemeral"]:
            self.state["store"].save_system_event(partner, text)
        try: self.page.update()
        except: pass

    def _on_incoming_message(self, sender: str, plaintext: str, timestamp: str = "",
                              view_once: bool = False, encrypted_payload: str = ""):
        def _update():
            if self.state["recipient"] and sender == self.state["recipient"]:
                self.add_message_to_chat(sender, plaintext, is_mine=False,
                                     time_str=timestamp, save=True,
                                     view_once=view_once,
                                     encrypted_payload=encrypted_payload)
                self.send_read_receipt(sender, timestamp)
            else:
                if self.state["store"] and not view_once:
                    self.state["store"].save_message(
                        partner=sender, sender=sender,
                        content=plaintext, is_mine=False, timestamp=timestamp,
                        is_read=0
                    )
                self.log_status(f"'{sender}' adlisindan yeni mesaj var!")
            self.load_inbox_chats()
        self.run_on_ui(_update)

    def _on_incoming_file(self, sender: str, file_uuid: str, original_name: str,
                           file_type: str, timestamp: str, view_once: bool):
        def _update():
            if self.state["recipient"] and sender == self.state["recipient"]:
                self.add_file_to_chat(sender, file_uuid, original_name,
                                  file_type, is_mine=False, time_str=timestamp,
                                  view_once=view_once)
            else:
                self.log_status(f"'{sender}' adlisindan dosya var! ({original_name})")
            self.load_inbox_chats()
        self.run_on_ui(_update)

    def load_history_to_chat(self):
        if not self.state["recipient"] or not self.state["store"]: return
        self.chat_list.controls.clear()
        for m in self.state["store"].get_messages(self.state["recipient"]):
            if m["msg_type"] == "system":
                self.chat_list.controls.append(self.create_system_bubble(m["content"]))
            else:
                ts = self._fmt_time(m["timestamp"])
                is_read_val = bool(m.get("is_read", 1))
                self.chat_list.controls.append(
                    self.create_message_bubble(m["sender"], m["content"], ts, bool(m["is_mine"]), is_read=is_read_val)
                )
        try: self.page.update()
        except: pass

    def toggle_ephemeral(self, e):
        if not self.state["recipient"]:
            self.log_status("Please select a recipient first!")
            return
        new_val = not self.state["ephemeral"]
        self.state["ephemeral"] = new_val
        self.state["store"].set_ephemeral(self.state["recipient"], new_val, self.state["username"])
        self._update_ephemeral_ui()

        self.send_ws_message_with_fallback({
            "type": "ephemeral_toggle",
            "sender": self.state["username"],
            "recipient": self.state["recipient"],
            "ephemeral": new_val,
        })

        label = ("Ephemeral mode ON — messages are not saved"
                 if new_val else "Message history ON — messages are saved")
        self.add_system_event(label, partner=self.state["recipient"])

    def _update_ephemeral_ui(self):
        def _apply():
            if self.state["ephemeral"]:
                self.ephemeral_btn.icon       = ft.Icons.VISIBILITY_OFF
                self.ephemeral_btn.icon_color = "#ef4444"
                self.ephemeral_btn.tooltip    = "Ephemeral mode ON — disable"
            else:
                self.ephemeral_btn.icon       = ft.Icons.VISIBILITY
                self.ephemeral_btn.icon_color = "#8b5cf6"
                self.ephemeral_btn.tooltip    = "Switch to Ephemeral Chat"
            try: self.page.update()
            except: pass
        self.run_on_ui(_apply)

    def copy_public_key(self, e):
        from crypto_utils import public_key_to_pem_string, get_public_key_fingerprint
        try:
            pem_str = public_key_to_pem_string(self.state["public_key"])
            fingerprint = get_public_key_fingerprint(self.state["public_key"])
            card = {
                "username": self.state["username"],
                "public_key": pem_str,
                "fingerprint": fingerprint
            }
            self.copy_to_clipboard(json.dumps(card, indent=2))
            self.log_status("Contact card copied to clipboard!")
        except Exception as ex:
            self.log_status(f"Kopyalama hatasi: {ex}")

    def _on_ephemeral_toggle_received(self, sender: str, ephemeral: bool, ts: str):
        if self.state["store"]:
            self.state["store"].set_ephemeral(sender, ephemeral, sender)
        if self.state["recipient"] == sender:
            self.state["ephemeral"] = ephemeral
            self._update_ephemeral_ui()
            label = (f"{sender} gecici modu ACTI — kayit durdu"
                     if ephemeral else f"{sender} kayit modunu ACTI")
            self.add_system_event(label, partner=sender)

    def toggle_view_once_msg(self, e):
        """Mesaj bazında tek görünümlü toggle."""
        self.state["view_once_mode"] = not self.state["view_once_mode"]
        if self.state["view_once_mode"]:
            self.view_once_msg_btn.icon       = ft.Icons.VISIBILITY_OFF
            self.view_once_msg_btn.icon_color = "#ef4444"
            self.view_once_msg_btn.tooltip    = "View-once ON — disable"
        else:
            self.view_once_msg_btn.icon       = ft.Icons.VISIBILITY
            self.view_once_msg_btn.icon_color = "#888888"
            self.view_once_msg_btn.tooltip    = "Send as view-once"
        self.page.update()

    async def on_attach_click(self, e):
        if not self.state["recipient"]:
            self.log_status("Please connect to a recipient or group first!")
            return
        files = await self.file_picker.pick_files(allow_multiple=False)
        if not files: return
        f = files[0]

        # Check size limit
        try:
            sz = os.path.getsize(f.path)
            if sz > 10 * 1024 * 1024:  # 10 MB limit
                self.log_status("File too large! Max 10 MB.")
                return
        except:
            pass

        file_type = _guess_file_type(f.name)

        # Stage the file in state
        self.state["staged_file"] = {
            "name": f.name,
            "path": f.path,
            "type": file_type,
        }

        self.staged_file_name_text.value = f.name
        self.staged_file_container.visible = True
        self.page.update()
        self.log_status(f"Staged file: '{f.name}'. Press send button to encrypt & upload.")
