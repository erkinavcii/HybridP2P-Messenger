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
from desktop.theme import C

from desktop.net_config import _guess_file_type
from desktop.notify import play_notification
from desktop import settings_store

# Tarih ayracı etiketleri için Türkçe ay adları
_TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
              "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


class ChatLogicMixin:

    def _parse_ts(self, ts: str):
        """ISO timestamp'i yerel saat dilimine çevrilmiş datetime'a dönüştürür (yoksa None)."""
        try:
            ts_norm = ts.replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(ts_norm)
            if dt.tzinfo is None:
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone()
        except Exception:
            return None

    def _fmt_time(self, ts: str) -> str:
        dt = self._parse_ts(ts)
        if dt is None:
            return ts[:5] if ts else ""
        return dt.strftime("%H:%M")

    def _day_key(self, ts: str):
        """Mesajın yerel takvim gününü döndürür (ayraç karşılaştırması için)."""
        dt = self._parse_ts(ts)
        return dt.date() if dt else None

    def _fmt_day_label(self, ts: str) -> str:
        """Tarih ayracı etiketi: "Bugün" / "Dün" / "12 Haziran" / "12 Haziran 2025"."""
        dt = self._parse_ts(ts)
        if dt is None:
            return ""
        today = datetime.now().astimezone().date()
        d = dt.date()
        delta = (today - d).days
        if delta == 0:
            return "Bugün"
        if delta == 1:
            return "Dün"
        if d.year == today.year:
            return f"{d.day} {_TR_MONTHS[d.month - 1]}"
        return f"{d.day} {_TR_MONTHS[d.month - 1]} {d.year}"

    def _append_day_separator_if_needed(self, ts: str):
        """Sohbetteki son baloncuktan farklı bir güne geçildiyse tarih ayracı ekler."""
        day = self._day_key(ts)
        if day is None:
            return
        if getattr(self, "_last_chat_day", None) == day:
            return
        self._last_chat_day = day
        self.chat_list.controls.append(self.create_date_separator(self._fmt_day_label(ts)))

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

        self._append_day_separator_if_needed(raw_ts)
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
        raw_ts = time_str
        time_str = self._fmt_time(time_str)

        bubble = self.create_file_bubble(sender, file_uuid, original_name,
                                     file_type, time_str, is_mine, view_once)
        self._append_day_separator_if_needed(raw_ts)
        self.chat_list.controls.append(bubble)
        try: self.page.update()
        except: pass

    def add_system_event(self, text: str, partner: str = None):
        self.chat_list.controls.append(self.create_system_bubble(text))
        if partner and self.state["store"] and not self.state["ephemeral"]:
            self.state["store"].save_system_event(partner, text)
        try: self.page.update()
        except: pass

    def warn_blocked_message(self, sender: str):
        """İmza doğrulamasından geçemeyen bir mesaj engellendiğinde kullanıcıyı uyarır.

        Uyarı hem durum çubuğunda gösterilir hem de ilgili sohbetin geçmişine
        kalıcı olarak yazılır. Kalıcı olması önemli: engelleme çoğu zaman
        kullanıcı başka bir ekrandayken (örn. girişte çevrimdışı mesajlar
        alınırken) gerçekleşir; aksi halde uyarı hiç görülmezdi.
        """
        text = (f"UYARI: '{sender}' adina gelen bir mesaj kimlik dogrulamasindan "
                f"gecemedi ve engellendi!")

        def _apply():
            self.log_status(f"'{sender}' adina gelen dogrulanamayan mesaj engellendi!")
            if self.state.get("store"):
                self.state["store"].save_system_event(sender, text)
            # Canlı baloncuk yalnızca o sohbet o an açıksa eklenir
            if self.state.get("recipient") == sender:
                self.chat_list.controls.append(self.create_system_bubble(text))
                try: self.page.update()
                except: pass

        self.run_on_ui(_apply)

    def _notify_incoming(self):
        """Gelen mesaj/dosya için bildirim sesi. Aktif arama sırasında ve
        kullanıcı ayarlardan kapattıysa çalınmaz."""
        if self.state.get("call_state") in ("ringing", "calling", "connected"):
            return
        if not settings_store.get("sound_enabled"):
            return
        play_notification()

    # ── "Yazıyor…" göstergesi (alım) ───────────────────────────────────
    # Karşı taraf "durdu" sinyalini hiç gönderemezse (bağlantı koptu vb.)
    # gösterge sonsuza kadar kalmasın diye kendiliğinden söner.
    TYPING_EXPIRE_SEC = 6.0

    def _on_typing_received(self, sender: str, is_typing: bool):
        # Yalnızca o an açık olan birebir sohbetin karşı tarafı için göster
        if sender != self.state.get("recipient") or self.state.get("is_group", False):
            return
        old = getattr(self, "_typing_expire_timer", None)
        if old:
            old.cancel()
        self.typing_text.visible = is_typing
        if is_typing:
            import threading
            self._typing_expire_timer = threading.Timer(
                self.TYPING_EXPIRE_SEC, lambda: self.run_on_ui(self._hide_typing))
            self._typing_expire_timer.daemon = True
            self._typing_expire_timer.start()
        try: self.page.update()
        except: pass

    def _hide_typing(self):
        if self.typing_text.visible:
            self.typing_text.visible = False
            try: self.page.update()
            except: pass

    def _on_incoming_message(self, sender: str, plaintext: str, timestamp: str = "",
                              view_once: bool = False, encrypted_payload: str = ""):
        def _update():
            self._notify_incoming()
            # Mesaj geldiyse karşı taraf yazmayı bitirmiştir
            if sender == self.state.get("recipient"):
                self._hide_typing()
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
            self._notify_incoming()
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
        # Sohbet sıfırdan çiziliyor — tarih ayracı takibi de sıfırlanmalı
        self._last_chat_day = None
        for m in self.state["store"].get_messages(self.state["recipient"]):
            self._append_day_separator_if_needed(m["timestamp"])
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
                self.ephemeral_btn.icon_color = C.danger
                self.ephemeral_btn.tooltip    = "Ephemeral mode ON — disable"
            else:
                self.ephemeral_btn.icon       = ft.Icons.VISIBILITY
                self.ephemeral_btn.icon_color = C.accent
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
            self.view_once_msg_btn.icon_color = C.danger
            self.view_once_msg_btn.tooltip    = "View-once ON — disable"
        else:
            self.view_once_msg_btn.icon       = ft.Icons.VISIBILITY
            self.view_once_msg_btn.icon_color = C.text_muted
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
