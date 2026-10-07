"""desktop/p2p_chat.py — Sunucusuz ("telsiz") modda doğrudan yazılı sohbet penceresi.

Bağlantıyı desktop/pure_p2p.py kurar; burada açık veri kanalı üzerinden mesaj
gönderilip alınır (desktop/p2p_core.py protokolü). Kanal DTLS ile uçtan uca
şifrelidir ve karşı tarafın kimliği bağlantı kodundaki imzayla doğrulanmıştır.

Geçmiş: yalnızca kimliği rehberle DOĞRULANMIŞ kişiyle yapılan sohbet, normal
sohbet geçmişine (aynı kişinin sohbetine) kaydedilir. Yeni kişi rehbere
eklenirse o andan itibaren kaydedilir. Diğer durumlarda sohbet yalnızca
bellektedir ve pencere kapanınca kaybolur.

İş parçacıkları: veri kanalı olayları P2P loop'unda (p2p_core.get_loop) gelir;
arayüz yalnızca run_on_ui ile güncellenir. Gönderim de o loop'a devredilir.
"""

import flet as ft

from desktop import p2p_core
from desktop.theme import C


class P2PChatMixin:

    def open_p2p_chat(self, pc, channel, ident: "p2p_core.PeerIdentity"):
        me = self.state.get("username") or "ben"
        peer = ident.username or "karşı taraf"
        session = {"save": ident.can_save_history, "open": True, "pending": {}}
        loop = p2p_core.get_loop()

        messages = ft.ListView(expand=True, spacing=6, auto_scroll=True)
        status_text = ft.Text("", size=11, color=C.text_muted)
        input_field = ft.TextField(hint_text="Mesaj yazın…", expand=True, multiline=False,
                                   max_length=p2p_core.MAX_TEXT_CHARS, counter="",
                                   border_color=C.surface_alt, focused_border_color=C.accent,
                                   text_size=13, on_submit=lambda e: send())
        send_btn = ft.IconButton(icon=ft.Icons.SEND, icon_color=C.accent, on_click=lambda e: send())

        # ── kimlik rozeti ──
        add_btn = ft.TextButton("Rehbere ekle", visible=False,
                                style=ft.ButtonStyle(color=C.accent))

        def badge_controls():
            if session["save"]:
                return [ft.Icon(ft.Icons.VERIFIED, color=C.success, size=16),
                        ft.Text("Kimlik doğrulandı · geçmişe kaydediliyor", size=11, color=C.success)]
            if ident.status == p2p_core.NEW:
                add_btn.visible = True
                return [ft.Icon(ft.Icons.HELP_OUTLINE, color=C.info, size=16),
                        ft.Column([
                            ft.Text("Yeni kişi — parmak izini karşı tarafla karşılaştırın "
                                    "(kaydedilmiyor)", size=11, color=C.info_text),
                            ft.Text(ident.fingerprint, size=9, color=C.text_muted, selectable=True),
                        ], spacing=1, tight=True, expand=True),
                        add_btn]
            return [ft.Icon(ft.Icons.WARNING_AMBER, color=C.danger, size=16),
                    ft.Text("Kimlik doğrulanmadı (eski kod) — kaydedilmiyor", size=11, color=C.danger)]

        badge = ft.Row(badge_controls(), spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def on_add_contact(e):
            store = self.state.get("store")
            if store and ident.public_key_pem:
                store.save_contact(peer, ident.public_key_pem, ident.fingerprint)
                session["save"] = True
                add_btn.visible = False
                badge.controls = badge_controls()
                self.log_status(f"'{peer}' rehbere eklendi; sohbet artık kaydediliyor.")
                self.page.update()
        add_btn.on_click = on_add_contact

        # ── mesajlar ──
        def add_bubble(sender, text, ts, mine, msg_id=None, delivered=True):
            bubble = self.create_message_bubble(sender, text, self._fmt_time(ts), mine, is_read=delivered)
            messages.controls.append(bubble)
            if mine and msg_id and not delivered:
                session["pending"][msg_id] = (len(messages.controls) - 1, text, ts)

        def save(sender, text, ts, mine, msg_id):
            store = self.state.get("store")
            if session["save"] and store:
                store.save_message(partner=peer, sender=sender, content=text, is_mine=mine,
                                   timestamp=ts, is_read=1, msg_uid=msg_id)

        def send():
            text = (input_field.value or "").strip()
            if not text or not session["open"]:
                return
            frame = p2p_core.text_frame(text)
            parsed = p2p_core.parse_frame(frame)
            try:
                loop.call_soon_threadsafe(channel.send, frame)
            except Exception as ex:
                status_text.value = f"Gönderilemedi: {ex}"
                self.page.update()
                return
            input_field.value = ""
            add_bubble(me, text, parsed["ts"], True, parsed["id"], delivered=False)
            save(me, text, parsed["ts"], True, parsed["id"])
            self.page.update()

        def on_frame(raw):
            f = p2p_core.parse_frame(raw)
            if not f:
                return

            def _ui():
                if f["t"] == "msg":
                    add_bubble(peer, f["text"], f["ts"], False)
                    save(peer, f["text"], f["ts"], False, f["id"])
                    self._notify_incoming()
                elif f["t"] == "ack" and f["id"] in session["pending"]:
                    idx, text, ts = session["pending"].pop(f["id"])
                    messages.controls[idx] = self.create_message_bubble(
                        me, text, self._fmt_time(ts), True, is_read=True)
                self.page.update()
            self.run_on_ui(_ui)
            if f["t"] == "msg":
                channel.send(p2p_core.ack_frame(f["id"]))   # zaten P2P loop'undayız

        def set_closed(reason):
            def _ui():
                if not session["open"]:
                    return
                session["open"] = False
                status_text.value = f"Bağlantı kapandı ({reason}). Yeniden bağlanmak için yeni kod üretin."
                status_text.color = C.danger
                input_field.disabled = True
                send_btn.disabled = True
                self.page.update()
            self.run_on_ui(_ui)

        channel.on("message", on_frame)
        channel.on("close", lambda: set_closed("kanal kapandı"))

        @pc.on("connectionstatechange")
        async def _on_state():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                set_closed(pc.connectionState)

        def close(e=None):
            session["open"] = False
            dialog.open = False
            self.page.update()
            p2p_core.run(pc.close())
            if session["save"]:
                self.load_inbox_chats()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.WIFI_TETHERING, color=C.accent),
                ft.Text(f"Doğrudan bağlantı: {peer}", size=15, color=C.text,
                        weight=ft.FontWeight.BOLD, expand=True),
                ft.IconButton(icon=ft.Icons.CLOSE, icon_size=18, icon_color=C.text_muted,
                              tooltip="Bağlantıyı kapat", on_click=close),
            ], spacing=8),
            content=ft.Container(
                content=ft.Column([
                    badge,
                    ft.Text("Sunucu yok: mesajlar yalnızca iki taraf da açıkken iletilir.",
                            size=10, color=C.text_faint),
                    ft.Divider(color=C.surface_alt, height=6),
                    messages,
                    status_text,
                    ft.Row([input_field, send_btn], spacing=4),
                ], spacing=6, expand=True),
                width=420, height=520,
            ),
            bgcolor=C.surface,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()
