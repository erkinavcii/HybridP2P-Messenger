"""desktop/p2p_chat.py — Sunucusuz ("telsiz") modda doğrudan sohbet ve dosya aktarımı penceresi.

Bağlantıyı desktop/pure_p2p.py kurar; burada açık veri kanalı üzerinden mesaj ve
dosya gönderilip alınır (protokoller: desktop/p2p_core.py, desktop/p2p_files.py).
Kanal DTLS ile uçtan uca şifrelidir ve karşı tarafın kimliği bağlantı kodundaki
imzayla doğrulanmıştır.

Geçmiş: yalnızca kimliği rehberle DOĞRULANMIŞ kişiyle yapılan sohbet, normal
sohbet geçmişine (aynı kişinin sohbetine) kaydedilir; dosyalar için yalnızca
"gönderildi/alındı" kaydı düşülür (dosyanın kendisi İndirilenler/HybridP2P'de).
Yeni kişi rehbere eklenirse o andan itibaren kaydedilir.

İş parçacıkları: veri kanalı olayları P2P loop'unda (p2p_core.get_loop) gelir;
arayüz yalnızca run_on_ui ile güncellenir. Gelen dosya kaydına (IncomingRegistry)
yalnızca P2P loop'undan dokunulur — arayüzdeki "Kabul et" de oraya devredilir.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import flet as ft

from desktop import p2p_core
from desktop import p2p_files as pf
from desktop.theme import C


def _open_folder(path: Path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))                      # noqa: S606 — kullanıcının kendi klasörü
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as ex:
        print(f"[P2P] Klasör açılamadı: {ex}")


class P2PChatMixin:

    def open_p2p_chat(self, pc, channel, ident: "p2p_core.PeerIdentity"):
        me = self.state.get("username") or "ben"
        peer = ident.username or "karşı taraf"
        session = {"save": ident.can_save_history, "open": True, "pending": {}}
        loop = p2p_core.get_loop()
        incoming = pf.IncomingRegistry()
        outgoing = {}          # fid → {"path", "cancelled", "card"}
        cards = {}             # fid → kart kontrolleri

        messages = ft.ListView(expand=True, spacing=6, auto_scroll=True)
        status_text = ft.Text("", size=11, color=C.text_muted)
        input_field = ft.TextField(hint_text="Mesaj yazın…", expand=True, multiline=False,
                                   max_length=p2p_core.MAX_TEXT_CHARS, counter="",
                                   border_color=C.surface_alt, focused_border_color=C.accent,
                                   text_size=13, on_submit=lambda e: send_text())
        send_btn = ft.IconButton(icon=ft.Icons.SEND, icon_color=C.accent, tooltip="Gönder",
                                 on_click=lambda e: send_text())
        attach_btn = ft.IconButton(icon=ft.Icons.ATTACH_FILE, icon_color=C.accent,
                                   tooltip=f"Dosya gönder (en fazla {pf.fmt_size(pf.MAX_FILE_BYTES)})")

        def ui(fn):
            self.run_on_ui(fn)

        def send_frame(obj):
            loop.call_soon_threadsafe(channel.send, obj if isinstance(obj, str) else json.dumps(obj))

        # ── kimlik rozeti ──
        add_btn = ft.TextButton("Rehbere ekle", visible=False, style=ft.ButtonStyle(color=C.accent))

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

        # ── metin mesajları ──
        def add_bubble(sender, text, ts, mine, msg_id=None, delivered=True):
            messages.controls.append(
                self.create_message_bubble(sender, text, self._fmt_time(ts), mine, is_read=delivered))
            if mine and msg_id and not delivered:
                session["pending"][msg_id] = (len(messages.controls) - 1, text, ts)

        def save(sender, text, ts, mine, msg_id=None):
            store = self.state.get("store")
            if session["save"] and store:
                store.save_message(partner=peer, sender=sender, content=text, is_mine=mine,
                                   timestamp=ts, is_read=1, msg_uid=msg_id)

        def now_iso():
            from datetime import datetime, timezone
            return datetime.now(timezone.utc).isoformat()

        def send_text():
            text = (input_field.value or "").strip()
            if not text or not session["open"]:
                return
            frame = p2p_core.text_frame(text)
            parsed = p2p_core.parse_frame(frame)
            send_frame(frame)
            input_field.value = ""
            add_bubble(me, text, parsed["ts"], True, parsed["id"], delivered=False)
            save(me, text, parsed["ts"], True, parsed["id"])
            self.page.update()

        # ── dosya kartı (gelen ve giden ortak görünüm) ──
        def file_card(fid, name, size, mine):
            title = ft.Text(f"📄 {name}", size=13, color=C.on_accent if mine else C.text_bubble_other,
                            weight=ft.FontWeight.BOLD, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
            sub = ft.Text(pf.fmt_size(size), size=10, color=C.on_accent_muted if mine else C.text_muted)
            bar = ft.ProgressBar(value=0, visible=False, color=C.success,
                                 bgcolor=C.on_accent_muted if mine else C.surface_deep)
            state = ft.Text("", size=11, color=C.on_accent if mine else C.text_bubble_other)
            actions = ft.Row([], spacing=4, wrap=True)
            box = ft.Container(
                ft.Column([title, sub, bar, state, actions], spacing=4, tight=True),
                bgcolor=C.accent if mine else C.surface_alt, padding=12, border_radius=12, width=300)
            row = ft.Row([box], alignment=ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START)
            cards[fid] = {"bar": bar, "state": state, "actions": actions, "last": 0.0, "mine": mine}
            messages.controls.append(row)
            return cards[fid]

        def card_progress(fid, frac):
            c = cards.get(fid)
            if not c:
                return
            now = time.monotonic()
            if frac < 1.0 and now - c["last"] < 0.15:        # arayüzü boğma
                return
            c["last"] = now

            def _ui():
                c["bar"].visible = True
                c["bar"].value = frac
                c["state"].value = f"%{int(frac * 100)}"
                self.page.update()
            ui(_ui)

        def card_done(fid, text, color=None, actions=()):
            c = cards.get(fid)
            if not c:
                return

            def _ui():
                c["bar"].visible = False
                c["state"].value = text
                if color:
                    c["state"].color = color
                c["actions"].controls = list(actions)
                self.page.update()
            ui(_ui)

        def cancel_btn(fid):
            def _cancel(e):
                if fid in outgoing:
                    outgoing[fid]["cancelled"] = True
                else:
                    loop.call_soon_threadsafe(incoming.cancel, fid)
                send_frame({"t": "file_cancel", "fid": fid})
                card_done(fid, "İptal edildi.", C.danger)
            return ft.TextButton("İptal", on_click=_cancel, style=ft.ButtonStyle(color=C.danger))

        # ── dosya gönderme ──
        async def pick_and_send(e):
            if not session["open"]:
                return
            files = await self.file_picker.pick_files(allow_multiple=False)
            if not files:
                return
            path = Path(files[0].path)

            def _prepare():                                     # SHA-256 büyük dosyada sürebilir
                try:
                    offer = pf.make_offer(path)
                except (OSError, pf.FileTransferError) as ex:
                    msg = f"Dosya gönderilemez: {ex}"     # ex except bitince silinir
                    ui(lambda: (setattr(status_text, "value", msg), self.page.update()))
                    return

                def _ui():
                    c = file_card(offer["fid"], offer["name"], offer["size"], True)
                    c["state"].value = "Karşı tarafın onayı bekleniyor…"
                    c["actions"].controls = [cancel_btn(offer["fid"])]
                    self.page.update()
                ui(_ui)
                outgoing[offer["fid"]] = {"path": path, "cancelled": False, "offer": offer}
                send_frame(offer)
            threading.Thread(target=_prepare, daemon=True).start()
        attach_btn.on_click = pick_and_send

        async def run_upload(fid):
            item = outgoing[fid]
            card_done(fid, "Gönderiliyor…", actions=[cancel_btn(fid)])
            try:
                ok = await pf.send_file(channel, item["path"], fid,
                                        on_progress=lambda f: card_progress(fid, f),
                                        is_cancelled=lambda: item["cancelled"])
            except Exception as ex:
                card_done(fid, f"Gönderilemedi: {ex}", C.danger)
                return
            if ok:
                name, size = item["offer"]["name"], item["offer"]["size"]
                card_done(fid, "✓ Gönderildi (karşı taraf doğruluyor)")
                ui(lambda: save(me, f"📄 Dosya gönderildi: {name} ({pf.fmt_size(size)})", now_iso(), True))

        # ── gelen çerçeveler (P2P loop'unda çalışır) ──
        def on_message(raw):
            if isinstance(raw, bytes):
                try:
                    inc = incoming.chunk(raw)
                    card_progress(inc.fid, inc.progress)
                except pf.FileTransferError as ex:
                    print(f"[P2P] Parça reddedildi: {ex}")
                return
            f = p2p_core.parse_frame(raw)
            if not f:
                return
            t = f["t"]
            if t == "msg":
                channel.send(p2p_core.ack_frame(f["id"]))

                def _ui():
                    add_bubble(peer, f["text"], f["ts"], False)
                    save(peer, f["text"], f["ts"], False, f["id"])
                    self._notify_incoming()
                    self.page.update()
                ui(_ui)
            elif t == "ack" and f["id"] in session["pending"]:
                def _ui():
                    idx, text, ts = session["pending"].pop(f["id"])
                    messages.controls[idx] = self.create_message_bubble(
                        me, text, self._fmt_time(ts), True, is_read=True)
                    self.page.update()
                ui(_ui)
            elif t == "file_offer":
                handle_offer(f)
            elif t == "file_accept" and f["fid"] in outgoing:
                p2p_core.run(run_upload(f["fid"]))
            elif t == "file_reject" and f["fid"] in outgoing:
                outgoing.pop(f["fid"], None)
                card_done(f["fid"], "Karşı taraf reddetti.", C.danger)
            elif t == "file_cancel":
                if f["fid"] in outgoing:
                    outgoing[f["fid"]]["cancelled"] = True
                incoming.cancel(f["fid"])
                card_done(f["fid"], "Karşı taraf iptal etti.", C.danger)
            elif t == "file_end":
                fid = f["fid"]
                try:
                    path = incoming.end(fid)
                except pf.FileTransferError as ex:
                    card_done(fid, f"⛔ {ex}", C.danger)
                    return
                folder_btn = ft.TextButton("Klasörü aç", on_click=lambda e, p=path.parent: _open_folder(p),
                                           style=ft.ButtonStyle(color=C.accent))
                card_done(fid, f"✓ Kaydedildi ve doğrulandı:\n{path}", C.success, [folder_btn])
                ui(lambda: (save(peer, f"📄 Dosya alındı: {path.name} — {path}", now_iso(), False),
                            self._notify_incoming()))

        def handle_offer(f):
            try:
                incoming.offer(f)
            except pf.FileTransferError as ex:
                channel.send(json.dumps({"t": "file_reject", "fid": f["fid"]}))
                print(f"[P2P] Dosya teklifi reddedildi: {ex}")
                return
            fid, name = f["fid"], pf.safe_filename(f["name"])
            too_big = not (0 < f["size"] <= pf.MAX_FILE_BYTES)

            def accept(e):
                def _on_loop():
                    try:
                        incoming.accept(fid)
                    except pf.FileTransferError as ex:
                        channel.send(json.dumps({"t": "file_reject", "fid": fid}))
                        card_done(fid, f"Alınamadı: {ex}", C.danger)
                        return
                    channel.send(json.dumps({"t": "file_accept", "fid": fid}))
                    card_done(fid, "Alınıyor…", actions=[cancel_btn(fid)])
                loop.call_soon_threadsafe(_on_loop)

            def reject(e):
                loop.call_soon_threadsafe(incoming.reject, fid)
                send_frame({"t": "file_reject", "fid": fid})
                card_done(fid, "Reddettiniz.", C.text_muted)

            def _ui():
                c = file_card(fid, name, f["size"], False)
                if too_big:
                    c["state"].value = f"Çok büyük (sınır {pf.fmt_size(pf.MAX_FILE_BYTES)}) — reddedildi."
                    self.page.update()
                    return
                c["state"].value = (f"{peer} bu dosyayı göndermek istiyor. Kabul ederseniz "
                                    f"{pf.default_download_dir()} klasörüne kaydedilir.")
                c["actions"].controls = [
                    ft.TextButton("Kabul et", on_click=accept, style=ft.ButtonStyle(color=C.success)),
                    ft.TextButton("Reddet", on_click=reject, style=ft.ButtonStyle(color=C.danger)),
                ]
                self._notify_incoming()
                self.page.update()
            if too_big:
                loop.call_soon_threadsafe(incoming.reject, fid)
                channel.send(json.dumps({"t": "file_reject", "fid": fid}))
            ui(_ui)

        # ── bağlantı durumu ──
        def set_closed(reason):
            for item in outgoing.values():
                item["cancelled"] = True
            loop.call_soon_threadsafe(incoming.abort_all)       # yarım dosyalar silinir

            def _ui():
                if not session["open"]:
                    return
                session["open"] = False
                status_text.value = f"Bağlantı kapandı ({reason}). Yeniden bağlanmak için yeni kod üretin."
                status_text.color = C.danger
                for ctl in (input_field, send_btn, attach_btn):
                    ctl.disabled = True
                self.page.update()
            ui(_ui)

        channel.on("message", on_message)
        channel.on("close", lambda: set_closed("kanal kapandı"))

        @pc.on("connectionstatechange")
        async def _on_state():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                set_closed(pc.connectionState)

        def close(e=None):
            set_closed("siz kapattınız")
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
                    ft.Text("Sunucu yok: mesajlar ve dosyalar yalnızca iki taraf da açıkken iletilir.",
                            size=10, color=C.text_faint),
                    ft.Divider(color=C.surface_alt, height=6),
                    messages,
                    status_text,
                    ft.Row([attach_btn, input_field, send_btn], spacing=4),
                ], spacing=6, expand=True),
                width=420, height=540,
            ),
            bgcolor=C.surface,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()
