"""desktop/chat_screen.py — Aktif sohbet ekranı: bağlanma, mesaj gönderme, durum.

client.py'den taşındı (modülerleştirme): show_chat_screen, on_connect_recipient,
on_send_click, remove_staged_file, update_recipient_status_ui,
refresh_recipient_status, check_recipient_status_loop.
"""

import threading
import time as time_module
from datetime import datetime, timezone
from pathlib import Path

import flet as ft

from crypto_utils import (
    pem_string_to_public_key,
    public_key_to_pem_string,
    get_public_key_fingerprint,
    encrypt_bytes,
    encrypt_message,
    encrypt_symmetric,
)


class ChatScreenMixin:

    def show_chat_screen(self):
        self.fab.visible = False
        if self.state["recipient"]:
            if self.state.get("is_group", False):
                chat_info = self.state["store"].get_chat_info(self.state["recipient"])
                display_name = chat_info.get("partner", self.state["recipient"])
                self.chat_title_text.value = f"Group: {display_name}"
                self.call_icon_btn.visible = False
                self.video_call_icon_btn.visible = False
            else:
                self.chat_title_text.value = f"Chat: {self.state['recipient']}"
                self.call_icon_btn.visible = True
                self.video_call_icon_btn.visible = True
        else:
            self.chat_title_text.value = "No active chat"
            self.call_icon_btn.visible = False
            self.video_call_icon_btn.visible = False

        self.username_text.value = f"User: {self.state['username']}"
        self.refresh_recipient_status()
        self.page.controls.clear()
        self.page.add(self.chat_view)
        self.page.update()

    def on_connect_recipient(self, e):
        recipient_input = self.recipient_field.value.strip()
        if not recipient_input:
            self.log_status("Recipient name or group ID cannot be empty!")
            return

        def connect_to_recipient_final(rec, pub):
            self.state["recipient"]         = rec
            self.state["is_group"]          = False
            self.state["recipient_pub_key"] = pub
            self.recipient_field.value      = rec
            self.recipient_field.read_only  = True
            self.recipient_field.border_color = "#22c55e"
            self.ephemeral_btn.disabled = False

            if self.state["store"]:
                self.state["store"].mark_as_read(rec)
                history = self.state["store"].get_messages(rec)
                received_msgs = [m for m in history if not m.get("is_mine", False)]
                if received_msgs:
                    latest_ts = received_msgs[-1]["timestamp"]
                    self.send_read_receipt(rec, latest_ts)

            ephemeral = self.state["store"].is_ephemeral(rec)
            self.state["ephemeral"] = ephemeral
            self._update_ephemeral_ui()
            self.load_history_to_chat()
            if ephemeral:
                self.add_system_event("This chat is in EPHEMERAL mode — messages are not saved")
            self.log_status(f"'{rec}' ile sohbet basladi.")
            self.show_chat_screen()
            self.page.update()

        _active_dialog = [None]  # mutable container to hold current open dialog

        def _dismiss_active_dialog():
            """Single shared function to safely close whatever dialog is open."""
            d = _active_dialog[0]
            if d is None:
                return
            _active_dialog[0] = None
            d.open = False
            try:
                self.page.overlay.remove(d)
            except Exception:
                pass
            self.page.update()

        def close_warning_dialog(accept, rec=None, new_pem=None):
            _dismiss_active_dialog()
            if accept and rec and new_pem:
                try:
                    new_pub_key = pem_string_to_public_key(new_pem)
                    fingerprint = get_public_key_fingerprint(new_pub_key)
                    self.state["store"].save_contact(rec, new_pem, fingerprint)
                    self.log_status(f"New key for '{rec}' accepted.")
                    connect_to_recipient_final(rec, new_pub_key)
                except Exception as ex:
                    self.log_status(f"Error: {ex}")
            else:
                self.log_status("Connection rejected for security reasons.")

        def close_tofu_dialog(accept, rec=None, pem=None, pub=None):
            _dismiss_active_dialog()
            if accept and rec and pem and pub:
                try:
                    fingerprint = get_public_key_fingerprint(pub)
                    self.state["store"].save_contact(rec, pem, fingerprint)
                    print(f"[Contact] Saved public key for '{rec}' to local DB")
                    connect_to_recipient_final(rec, pub)
                except Exception as ex:
                    self.log_status(f"Error: {ex}")
            else:
                self.log_status("Connection not approved.")

        # Check if the input is a JSON contact card
        imported_card = None
        if recipient_input.startswith("{") and recipient_input.endswith("}"):
            try:
                import json
                card_data = json.loads(recipient_input)
                if "username" in card_data and "public_key" in card_data:
                    imported_card = card_data
            except Exception as ex:
                print(f"[Import Contact Card Error] {ex}")

        if imported_card:
            recipient = imported_card["username"].lower()
            if recipient == self.state["username"]:
                self.log_status("You cannot add your own contact card!")
                return

            pub_key_pem = imported_card["public_key"]
            try:
                pub_key = pem_string_to_public_key(pub_key_pem)
                fingerprint = get_public_key_fingerprint(pub_key)
                self.state["store"].save_contact(recipient, pub_key_pem, fingerprint)
                self.log_status(f"'{recipient}' kimlik karti basariyla import edildi!")
                self.recipient_field.value = recipient
                recipient_input = recipient
            except Exception as ex:
                self.log_status(f"Kimlik karti yukleme hatasi: {ex}")
                return
        else:
            recipient = recipient_input.lower()
            if recipient == self.state["username"]:
                self.log_status("You cannot send a message to yourself!")
                return

        if recipient.startswith("group_"):
            key = self.state["store"].get_group_key(recipient)
            if not key:
                self.log_status("Error: You don't have the encryption key for this group!")
                return

            self.state["recipient"] = recipient
            self.state["is_group"] = True

            if self.state["store"]:
                self.state["store"].mark_as_read(recipient)

            chat_info = self.state["store"].get_chat_info(recipient)
            gname = chat_info.get("partner", recipient)

            self.recipient_field.value = gname
            self.recipient_field.read_only = True
            self.recipient_field.border_color = "#22c55e"
            self.ephemeral_btn.disabled = True

            self.load_history_to_chat()
            self.log_status(f"'{gname}' grubu ile sohbet basladi.")
            self.show_chat_screen()
            self.page.update()
            return

        local_contact = self.state["store"].get_contact(recipient)
        if local_contact:
            print(f"[Contact] Loaded local public key for '{recipient}'")
            pub_key = pem_string_to_public_key(local_contact["public_key"])
            server_pub_key = self.fetch_recipient_pub_key(recipient)
            if server_pub_key:
                server_pub_pem = public_key_to_pem_string(server_pub_key)
                if server_pub_pem != local_contact["public_key"]:
                    dialog = ft.AlertDialog(
                        modal=False,
                        title=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.WARNING_ROUNDED, color="#ef4444"),
                                ft.Text("SECURITY WARNING!", color="#ef4444", weight=ft.FontWeight.BOLD)
                            ],
                            spacing=8
                        ),
                        content=ft.Text(
                            f"WARNING: The server key for '{recipient}' differs from your local record!\n\n"
                            f"This could indicate a MITM attack or the user has regenerated their key.\n\n"
                            f"Do you want to accept the new key from the server?",
                            color="#ffffff"
                        ),
                        actions=[
                            ft.TextButton("Reject (Safe)",
                                on_click=lambda e: close_warning_dialog(accept=False)),
                            ft.TextButton("Accept New Key",
                                on_click=lambda e: close_warning_dialog(accept=True, rec=recipient, new_pem=server_pub_pem)),
                        ],
                        actions_alignment=ft.MainAxisAlignment.END,
                        bgcolor="#18181b",
                    )
                    _active_dialog[0] = dialog
                    self.page.overlay.append(dialog)
                    dialog.open = True
                    self.page.update()
                    return
        else:
            pub_key = self.fetch_recipient_pub_key(recipient)
            if pub_key:
                pub_key_pem = public_key_to_pem_string(pub_key)
                fingerprint = get_public_key_fingerprint(pub_key)

                # Show TOFU verification dialog
                dialog = ft.AlertDialog(
                    modal=False,
                    title=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SHIELD_OUTLINED, color="#22c55e"),
                            ft.Text("First Connection & Authentication", color="#ffffff", weight=ft.FontWeight.BOLD)
                        ],
                        spacing=8
                    ),
                    content=ft.Column(
                        controls=[
                            ft.Text(f"Connecting to '{recipient}' for the first time.", color="#ffffff"),
                            ft.Text("Identity fingerprint received from server:", color="#aaaaaa", size=12),
                            ft.Container(
                                content=ft.Text(fingerprint, weight=ft.FontWeight.BOLD, color="#22c55e", size=13, selectable=True),
                                bgcolor="#27272a",
                                padding=10,
                                border_radius=8,
                                border=ft.Border(
                                    left=ft.BorderSide(1, "#3f3f46"), top=ft.BorderSide(1, "#3f3f46"),
                                    right=ft.BorderSide(1, "#3f3f46"), bottom=ft.BorderSide(1, "#3f3f46")
                                ),
                            ),
                            ft.Text(
                                "For your security, verify this fingerprint with your contact through a separate channel.",
                                color="#ef4444", size=11
                            ),
                        ],
                        tight=True,
                        spacing=8
                    ),
                    actions=[
                        ft.TextButton("Cancel (Safe)",
                            on_click=lambda e: close_tofu_dialog(accept=False)),
                        ft.TextButton("Approve Key & Connect",
                            on_click=lambda e: close_tofu_dialog(accept=True, rec=recipient, pem=pub_key_pem, pub=pub_key)),
                    ],
                    actions_alignment=ft.MainAxisAlignment.END,
                    bgcolor="#18181b",
                )
                _active_dialog[0] = dialog
                self.page.overlay.append(dialog)
                dialog.open = True
                self.page.update()
                return

        if pub_key:
            connect_to_recipient_final(recipient, pub_key)
        else:
            self.log_status(f"'{recipient}' bulunamadi.")

    def on_send_click(self, e):
        recipient = self.state["recipient"]
        if not recipient:
            self.log_status("Please connect to a recipient or group first!")
            return

        text = self.message_input.value.strip()
        view_once = self.state["view_once_mode"]
        staged = self.state.get("staged_file")

        if not text and not staged:
            return

        # Clear input field immediately
        self.message_input.value = ""
        self.page.update()

        if staged:
            # Show progress bar and disable attach/staged remove controls
            self.upload_progress.visible = True
            self.staged_file_container.disabled = True
            self.attach_btn.disabled = True
            self.upload_progress.value = 0.2
            self.log_status(f"Encrypting '{staged['name']}'...")
            self.page.update()

            def do_file_upload_and_send():
                def _progress(val: float, status: str = None):
                    self.upload_progress.value = val
                    if status:
                        self.status_text.value = status
                    self.page.update()

                try:
                    raw = Path(staged["path"]).read_bytes()
                    file_type = staged["type"]

                    self.run_on_ui(_progress, 0.4, "Uploading encrypted payload...")

                    encrypted = encrypt_bytes(raw, self.state["recipient_pub_key"])
                    self.run_on_ui(_progress, 0.6)

                    resp = self.signed_post(
                        "/api/upload_file",
                        {
                            "sender":         self.state["username"],
                            "recipient":      recipient,
                            "encrypted_data": encrypted,
                            "original_name":  staged["name"],
                            "file_type":      file_type,
                        },
                        timeout=60,
                    )

                    if resp.status_code != 200:
                        def _upload_failed():
                            self.status_text.value = f"Upload failed: {resp.text}"
                            self.upload_progress.visible = False
                            self.staged_file_container.disabled = False
                            self.attach_btn.disabled = False
                            self.page.update()
                        self.run_on_ui(_upload_failed)
                        return

                    self.run_on_ui(_progress, 0.8)

                    file_uuid = resp.json()["uuid"]

                    self.send_ws_message_with_fallback({
                        "type":          "file_message",
                        "sender":        self.state["username"],
                        "recipient":     recipient,
                        "file_uuid":     file_uuid,
                        "original_name": staged["name"],
                        "file_type":     file_type,
                        "view_once":     view_once,
                    })

                    def _upload_success():
                        self.add_file_to_chat(
                            sender=self.state["username"],
                            file_uuid=file_uuid,
                            original_name=staged["name"],
                            file_type=file_type,
                            is_mine=True,
                            view_once=view_once,
                        )
                        self.load_inbox_chats()

                        # Reset view-once toggle
                        if view_once:
                            self.state["view_once_mode"] = False
                            self.view_once_msg_btn.icon       = ft.Icons.VISIBILITY
                            self.view_once_msg_btn.icon_color = "#888888"

                        self.status_text.value = f"Sent: {staged['name']}"

                        # Clear staged state and reset UI
                        self.state["staged_file"] = None
                        self.staged_file_container.visible = False
                        self.staged_file_container.disabled = False
                        self.upload_progress.visible = False
                        self.attach_btn.disabled = False
                        self.page.update()
                    self.run_on_ui(_upload_success)

                except Exception as ex:
                    def _upload_error():
                        self.status_text.value = f"Upload error: {ex}"
                        self.upload_progress.visible = False
                        self.staged_file_container.disabled = False
                        self.attach_btn.disabled = False
                        self.page.update()
                    self.run_on_ui(_upload_error)

            threading.Thread(target=do_file_upload_and_send, daemon=True).start()

        if text:
            is_group = bool(self.state.get("is_group", False))
            if is_group:
                group_id = recipient
                group_key = self.state["store"].get_group_key(group_id)
                if not group_key:
                    self.log_status("Error: Group encryption key not found!")
                    return
                try:
                    encrypted = encrypt_symmetric(text, group_key)
                except Exception as ex:
                    self.log_status(f"Group encryption error: {ex}")
                    return

                timestamp = datetime.now(timezone.utc).isoformat()
                self.send_group_message_via_ws(group_id, encrypted, timestamp=timestamp)
                self.add_message_to_chat(
                    sender=self.state["username"], text=text,
                    is_mine=True, save=True, view_once=False, time_str=timestamp, is_read=False
                )
            else:
                if not self.state["recipient_pub_key"]:
                    self.log_status("Recipient public key not found!")
                    return
                try:
                    encrypted = encrypt_message(text, self.state["recipient_pub_key"])
                except Exception as ex:
                    self.log_status(f"Encryption error: {ex}")
                    return

                timestamp = datetime.now(timezone.utc).isoformat()
                self.send_message_via_ws(recipient, encrypted, view_once, timestamp=timestamp)
                self.add_message_to_chat(
                    sender=self.state["username"], text=text,
                    is_mine=True, save=not view_once, view_once=view_once,
                    encrypted_payload=encrypted, time_str=timestamp, is_read=False
                )
                self.load_inbox_chats()

            if view_once:
                self.state["view_once_mode"] = False
                self.view_once_msg_btn.icon       = ft.Icons.VISIBILITY
                self.view_once_msg_btn.icon_color = "#888888"
                self.page.update()

    def remove_staged_file(self, e):
        self.state["staged_file"] = None
        self.staged_file_container.visible = False
        self.upload_progress.visible = False
        self.page.update()

    def update_recipient_status_ui(self, online):
        def _update():
            if self.state.get("is_group", False) or not self.state["recipient"]:
                self.recipient_status_row.visible = False
            else:
                self.recipient_status_row.visible = True
                if online:
                    self.recipient_status_dot.bgcolor = "#22c55e"
                    self.recipient_status_label.value = "Online"
                    self.recipient_status_label.color = "#22c55e"
                else:
                    self.recipient_status_dot.bgcolor = "#ef4444"
                    self.recipient_status_label.value = "Offline"
                    self.recipient_status_label.color = "#ef4444"
            try: self.page.update()
            except: pass
        self.run_on_ui(_update)

    def refresh_recipient_status(self):
        if not self.state["recipient"] or self.state.get("is_group", False):
            self.update_recipient_status_ui(None)
            return

        def run():
            try:
                r = self.signed_get(f"/api/status/{self.state['recipient']}", timeout=3)
                if r.status_code == 200:
                    online = r.json().get("online", False)
                    self.update_recipient_status_ui(online)
                else:
                    self.update_recipient_status_ui(False)
            except:
                self.update_recipient_status_ui(False)

        threading.Thread(target=run, daemon=True).start()

    def check_recipient_status_loop(self):
        while True:
            self.refresh_recipient_status()
            time_module.sleep(5)
