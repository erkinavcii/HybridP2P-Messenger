"""desktop/chat_screen.py — Aktif sohbet ekranı: bağlanma, mesaj gönderme, durum.

client.py'den taşındı (modülerleştirme): show_chat_screen, on_connect_recipient,
on_send_click, remove_staged_file, update_recipient_status_ui,
refresh_recipient_status, check_recipient_status_loop.
"""

import json
import threading
import time as time_module
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import flet as ft
from desktop.theme import C
from desktop import linkpreview, settings_store

from crypto_utils import (
    pem_string_to_public_key,
    public_key_to_pem_string,
    get_public_key_fingerprint,
    encrypt_bytes,
    encrypt_message,
    encrypt_symmetric,
)


class ChatScreenMixin:

    @property
    def _dm_sender(self):
        """Birebir metin gönderimleri için tek işçili kuyruk (ilk kullanımda kurulur)."""
        ex = self.__dict__.get("_dm_sender_pool")
        if ex is None:
            ex = self.__dict__["_dm_sender_pool"] = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="dm-send")
        return ex

    def show_chat_screen(self):
        self.fab.visible = False
        if self.state["recipient"]:
            if self.state.get("is_group", False):
                chat_info = self.state["store"].get_chat_info(self.state["recipient"])
                display_name = chat_info.get("partner", self.state["recipient"])
                self.chat_title_text.value = f"Group: {display_name}"
                self.call_icon_btn.visible = False
                self.video_call_icon_btn.visible = False
                self.voice_btn.visible = False
            else:
                self.chat_title_text.value = f"Chat: {self.state['recipient']}"
                self.call_icon_btn.visible = True
                self.video_call_icon_btn.visible = True
                self.voice_btn.visible = True
        else:
            self.chat_title_text.value = "No active chat"
            self.call_icon_btn.visible = False
            self.video_call_icon_btn.visible = False

        self.update_chat_header_avatar()
        self.username_text.value = f"User: {self.state['username']}"
        # Önceki sohbetten kalmış bir "yazıyor…" yeni sohbette görünmesin
        self.typing_text.visible = False
        self.refresh_recipient_status()
        self.page.controls.clear()
        self.page.add(self.chat_view)
        self.page.update()

    def update_chat_header_avatar(self):
        rec = self.state.get("recipient")
        is_group = self.state.get("is_group", False)
        if rec:
            self.chat_avatar.content = self.make_avatar(rec, is_group, radius=16)
            self.chat_avatar.visible = True
        else:
            self.chat_avatar.visible = False
        try: self.page.update()
        except: pass

    # ── Mesaj düzenleme / silme menüsü ─────────────────────────────────

    def open_message_actions(self, msg_uid: str, current_text: str):
        """Kendi mesajımız için: düzenle veya herkesten sil."""
        recipient = self.state.get("recipient")
        me = self.state["username"]
        if not recipient or not msg_uid:
            return

        edit_field = ft.TextField(value=current_text, multiline=True, min_lines=1, max_lines=5,
                                  max_length=8000, counter="", autofocus=True,
                                  border_color=C.border, focused_border_color=C.accent,
                                  cursor_color=C.accent)

        def close(e=None):
            dialog.open = False
            self.page.update()

        def do_edit(e):
            new_text = (edit_field.value or "").strip()
            if not new_text or new_text == current_text:
                close(); return
            if not self.state["store"].edit_message(recipient, msg_uid, me, new_text):
                self.log_status("Mesaj düzenlenemedi."); close(); return
            close()
            threading.Thread(target=self.send_message_change,
                             args=("message_edit", recipient, msg_uid, new_text), daemon=True).start()
            self.load_history_to_chat()
            self.load_inbox_chats()

        def do_delete(e):
            if not self.state["store"].delete_message(recipient, msg_uid, me):
                self.log_status("Mesaj silinemedi."); close(); return
            close()
            threading.Thread(target=self.send_message_change,
                             args=("message_delete", recipient, msg_uid), daemon=True).start()
            self.load_history_to_chat()
            self.load_inbox_chats()
            self.log_status("Mesaj herkesten silindi.")

        dialog = ft.AlertDialog(
            title=ft.Text("Mesaj", size=16, color=C.text, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column([
                    edit_field,
                    ft.Text("Düzenleme ve silme karşı tarafa da uygulanır (uçtan uca şifreli, imzalı).",
                            size=10, color=C.text_muted),
                ], tight=True, spacing=6),
                width=340,
            ),
            actions=[
                ft.TextButton("Herkesten sil", on_click=do_delete,
                              style=ft.ButtonStyle(color=C.danger)),
                ft.TextButton("İptal", on_click=close, style=ft.ButtonStyle(color=C.text_muted)),
                ft.TextButton("Kaydet", on_click=do_edit, style=ft.ButtonStyle(color=C.accent)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=C.surface,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    # ── Sesli mesaj kaydı ──────────────────────────────────────────────

    def _voice_recorder(self):
        if getattr(self, "_recorder", None) is None:
            from desktop import voice
            self._recorder = voice.VoiceRecorder(
                on_limit=lambda: self.run_on_ui(self.toggle_voice_recording, None))
        return self._recorder

    def _set_recording_ui(self, on: bool):
        self.recording_bar.visible = on
        self.voice_btn.icon = ft.Icons.STOP_CIRCLE if on else ft.Icons.MIC_NONE
        self.voice_btn.icon_color = C.danger if on else C.text_muted
        self.voice_btn.tooltip = "Kaydı bitir ve gönder" if on else "Sesli mesaj kaydet"
        self.message_input.disabled = on
        try: self.page.update()
        except: pass

    def toggle_voice_recording(self, e):
        """1. tık: kaydı başlat. 2. tık (veya "Gönder"): durdur, şifrele, gönder."""
        from desktop import voice
        rec = self._voice_recorder()

        if rec.recording:
            pcm = rec.stop()
            self._set_recording_ui(False)
            target = getattr(self, "_voice_target", None)
            threading.Thread(target=self._send_voice, args=(pcm, target), daemon=True).start()
            return

        recipient = self.state.get("recipient")
        if not recipient or self.state.get("is_group", False):
            self.log_status("Sesli mesaj yalnızca birebir sohbetlerde gönderilebilir.")
            return
        if not self.state.get("recipient_pub_key"):
            self.log_status("Alıcının anahtarı yok, sesli mesaj gönderilemez.")
            return
        if self.state.get("call_state") in ("ringing", "calling", "connected"):
            self.log_status("Arama sırasında sesli mesaj kaydedilemez.")
            return
        try:
            rec.start()
        except Exception as ex:
            print(f"[Voice] Mikrofon acilamadi: {ex}")
            self.log_status("Mikrofon açılamadı (bağlı değil ya da başka uygulama kullanıyor).")
            return

        # Kayıt sürerken sohbet değişse bile ses doğru kişiye gitsin
        self._voice_target = (recipient, self.state["recipient_pub_key"])
        self._stop_typing_signal()
        self._set_recording_ui(True)

        def _tick():
            while rec.recording:
                elapsed = rec.elapsed
                def _upd(t=elapsed):
                    self.recording_label.value = (f"Kaydediliyor {voice.fmt_duration(t)}"
                                                  f" / {voice.fmt_duration(voice.MAX_SECONDS)}")
                    try: self.page.update()
                    except: pass
                self.run_on_ui(_upd)
                time_module.sleep(0.5)
        threading.Thread(target=_tick, daemon=True).start()

    def cancel_voice_recording(self, e):
        self._voice_recorder().cancel()
        self._set_recording_ui(False)
        self.log_status("Sesli mesaj iptal edildi.")

    def _send_voice(self, pcm, target):
        """Arka planda: Opus'a kodla → E2EE şifrele → yükle → file_message → yerelde sakla."""
        from desktop import voice
        if not target:
            return
        recipient, pub = target
        duration = len(pcm) / voice.SAMPLE_RATE
        if duration < voice.MIN_SECONDS:
            self.log_status("Sesli mesaj çok kısa, gönderilmedi.")
            return
        try:
            self.log_status("Sesli mesaj şifreleniyor...")
            data = voice.encode_opus(pcm)
            original_name = voice.voice_filename()
            resp = self.signed_post("/api/upload_file", {
                "sender":         self.state["username"],
                "recipient":      recipient,
                "encrypted_data": encrypt_bytes(data, pub),
                "original_name":  original_name,
                "file_type":      "audio",
            }, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(f"yükleme başarısız ({resp.status_code}): {resp.text[:120]}")
            file_uuid = resp.json()["uuid"]
            ts = datetime.now(timezone.utc).isoformat()
            self.send_ws_message_with_fallback({
                "type":          "file_message",
                "sender":        self.state["username"],
                "recipient":     recipient,
                "file_uuid":     file_uuid,
                "original_name": original_name,
                "file_type":     "audio",
                "view_once":     False,
                "timestamp":     ts,
            })
        except Exception as ex:
            print(f"[Voice] Gonderim hatasi: {ex}")
            self.log_status(f"Sesli mesaj gönderilemedi: {ex}")
            return

        self.store_voice(recipient, self.state["username"], True, ts, file_uuid, data, duration)

        def _show():
            if self.state.get("recipient") == recipient:
                self.add_voice_to_chat(self.state["username"], True, ts, duration, lambda d=data: d)
            self.log_status(f"Sesli mesaj gönderildi ({voice.fmt_duration(duration)}).")
            self.load_inbox_chats()
        self.run_on_ui(_show)

    # ── "Yazıyor…" sinyali gönderme ─────────────────────────────────────
    # Debounce: yazarken en fazla TYPING_RESEND_SEC'de bir "true"; son tuştan
    # TYPING_IDLE_SEC sonra veya mesaj gönderilince "false". Yalnızca birebir
    # sohbetlerde ve WebSocket açıkken gönderilir (REST fallback'e düşmez).
    TYPING_RESEND_SEC = 3.0
    TYPING_IDLE_SEC = 4.0

    def _send_typing(self, is_typing: bool):
        recipient = self.state.get("recipient")
        if not recipient or self.state.get("is_group", False) or not self.is_ws_connected():
            return
        import json
        self._ws_send_raw(json.dumps({"type": "typing", "recipient": recipient,
                                      "is_typing": is_typing}))

    def on_message_input_change(self, e):
        text = (self.message_input.value or "").strip()
        timer = getattr(self, "_typing_idle_timer", None)
        if timer:
            timer.cancel()

        if not text:
            self._stop_typing_signal()
            return

        now = time_module.monotonic()
        last = getattr(self, "_typing_last_sent", 0.0)
        if not getattr(self, "_typing_active", False) or now - last >= self.TYPING_RESEND_SEC:
            self._typing_active = True
            self._typing_last_sent = now
            self._send_typing(True)

        self._typing_idle_timer = threading.Timer(self.TYPING_IDLE_SEC, self._stop_typing_signal)
        self._typing_idle_timer.daemon = True
        self._typing_idle_timer.start()

    def _stop_typing_signal(self):
        timer = getattr(self, "_typing_idle_timer", None)
        if timer:
            timer.cancel()
            self._typing_idle_timer = None
        if getattr(self, "_typing_active", False):
            self._typing_active = False
            self._send_typing(False)

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
            self.recipient_field.border_color = C.success
            self.ephemeral_btn.disabled = False

            if self.state["store"]:
                self.state["store"].mark_as_read(rec)
                history = self.state["store"].get_messages(rec)
                received_msgs = [m for m in history if not m.get("is_mine", False)]
                if received_msgs:
                    latest_ts = received_msgs[-1]["timestamp"]
                    self.send_read_receipt(rec, latest_ts)

            # Bu kişi avatarımızın güncel sürümünü almadıysa şifreli gönder
            self.maybe_send_avatar(rec)

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
            self.recipient_field.border_color = C.success
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
                                ft.Icon(ft.Icons.WARNING_ROUNDED, color=C.danger),
                                ft.Text("SECURITY WARNING!", color=C.danger, weight=ft.FontWeight.BOLD)
                            ],
                            spacing=8
                        ),
                        content=ft.Text(
                            f"WARNING: The server key for '{recipient}' differs from your local record!\n\n"
                            f"This could indicate a MITM attack or the user has regenerated their key.\n\n"
                            f"Do you want to accept the new key from the server?",
                            color=C.text
                        ),
                        actions=[
                            ft.TextButton("Reject (Safe)",
                                on_click=lambda e: close_warning_dialog(accept=False)),
                            ft.TextButton("Accept New Key",
                                on_click=lambda e: close_warning_dialog(accept=True, rec=recipient, new_pem=server_pub_pem)),
                        ],
                        actions_alignment=ft.MainAxisAlignment.END,
                        bgcolor=C.surface,
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
                            ft.Icon(ft.Icons.SHIELD_OUTLINED, color=C.success),
                            ft.Text("First Connection & Authentication", color=C.text, weight=ft.FontWeight.BOLD)
                        ],
                        spacing=8
                    ),
                    content=ft.Column(
                        controls=[
                            ft.Text(f"Connecting to '{recipient}' for the first time.", color=C.text),
                            ft.Text("Identity fingerprint received from server:", color=C.text_system, size=12),
                            ft.Container(
                                content=ft.Text(fingerprint, weight=ft.FontWeight.BOLD, color=C.success, size=13, selectable=True),
                                bgcolor=C.surface_alt,
                                padding=10,
                                border_radius=8,
                                border=ft.Border(
                                    left=ft.BorderSide(1, C.border), top=ft.BorderSide(1, C.border),
                                    right=ft.BorderSide(1, C.border), bottom=ft.BorderSide(1, C.border)
                                ),
                            ),
                            ft.Text(
                                "For your security, verify this fingerprint with your contact through a separate channel.",
                                color=C.danger, size=11
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
                    bgcolor=C.surface,
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
        # Mesaj gitti — karşı tarafta "yazıyor…" hemen kapansın
        self._stop_typing_signal()

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
                            self.view_once_msg_btn.icon_color = C.text_muted

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
                    def _upload_error(ex=ex):   # ex except bitince silinir: değeri şimdi yakala
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
                # Kalıcı birebir mesajlara düzenleme/silme için kararlı kimlik
                msg_uid = None if view_once else uuid.uuid4().hex
                link = (None if view_once or not settings_store.get("link_previews")
                        else linkpreview.find_first_url(text))
                self.add_message_to_chat(
                    sender=self.state["username"], text=text,
                    is_mine=True, save=not view_once, view_once=view_once,
                    encrypted_payload=encrypted, time_str=timestamp, is_read=False,
                    msg_uid=msg_uid,
                )
                self.load_inbox_chats()
                # Önizleme ağ isteği UI'ı ve diğer mesajları bekletmesin: tüm birebir
                # gönderimler tek işçili kuyruktan sırayla çıkar (sıra korunur);
                # önizleme alınamazsa mesaj önizlemesiz gider.
                self._dm_sender.submit(self._send_dm_with_preview, recipient, encrypted,
                                       view_once, timestamp, msg_uid, link)

            if view_once:
                self.state["view_once_mode"] = False
                self.view_once_msg_btn.icon       = ft.Icons.VISIBILITY
                self.view_once_msg_btn.icon_color = C.text_muted
                self.page.update()

    def _send_dm_with_preview(self, recipient, encrypted, view_once, timestamp, msg_uid, link):
        preview = linkpreview.fetch_preview(link) if link else None
        try:
            self.send_message_via_ws(recipient, encrypted, view_once, timestamp=timestamp,
                                     msg_uid=msg_uid, preview=preview)
        except Exception as ex:
            print(f"[Send] mesaj gonderilemedi: {ex}")
            self.run_on_ui(lambda ex=ex: self.log_status(f"Gönderim hatası: {ex}"))
            return
        if not preview or not msg_uid:
            return
        store = self.state.get("store")
        if store and store.set_message_preview(recipient, msg_uid, json.dumps(preview, ensure_ascii=False)):
            def _redraw():
                if self.state.get("recipient") == recipient and not self.state.get("ephemeral"):
                    self.load_history_to_chat()
            self.run_on_ui(_redraw)

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
                    self.recipient_status_dot.bgcolor = C.success
                    self.recipient_status_label.value = "Online"
                    self.recipient_status_label.color = C.success
                else:
                    self.recipient_status_dot.bgcolor = C.danger
                    self.recipient_status_label.value = "Offline"
                    self.recipient_status_label.color = C.danger
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
