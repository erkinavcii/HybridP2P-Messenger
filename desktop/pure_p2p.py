"""desktop/pure_p2p.py — Sunucusuz ("telsiz") bağlantı: adım adım P2P sihirbazı.

Akış: A "davet kodu" üretir → B'ye herhangi bir kanaldan (kopyala-yapıştır, QR)
iletir → B kodu doğrular ve "yanıt kodu" üretir → A yanıtı yapıştırır → iki
cihaz doğrudan bağlanır. Arada sunucu yoktur; iki taraf da aynı anda açık olmalı.

Kodlar imzalıdır ve karşı tarafın kimliği rehberle karşılaştırılır (ayrıntılar
ve güvenlik modeli: desktop/p2p_core.py).
"""

import asyncio
import base64

import flet as ft
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from desktop import p2p_core, settings_store
from desktop.theme import C, t
from desktop.voip_tracks import AudioPlayer, CameraTrack, MicrophoneTrack

_BLANK_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

MODE_LABELS = {
    "chat": "Text Chat & Files",
    "audio": "Voice Call",
    "video": "Video Call",
}


def _ice_config():
    """Ayarlardaki STUN/TURN seçimine göre yapılandırma."""
    servers = p2p_core.ice_servers_for(settings_store.get("p2p_ice"), settings_store.get("p2p_ice_custom"))
    return RTCConfiguration(iceServers=[
        RTCIceServer(urls=[s["urls"]], username=s.get("username"), credential=s.get("credential"))
        for s in servers
    ])


def ice_preset_label(key):
    """STUN ön ayarının ekranda gösterilen (çevrilmiş) adı.

    p2p_core.ICE_PRESETS metinleri serverless.html ile birebir aynı kalmak zorunda
    (tests/test_serverless_html.py); bu yüzden çeviri burada, gösterim anında yapılır.
    Sözlükte karşılığı olmayan anahtar için ICE_PRESETS metnine düşülür.
    """
    tr_key = f"ice_preset_{key}"
    text = t(tr_key)
    return text if text != tr_key else p2p_core.ICE_PRESETS.get(key, key)


def ice_preset_hint(key):
    tr_key = f"ice_hint_{key}"
    text = t(tr_key)
    return "" if text == tr_key else text


def _qr_base64(data_str):
    """QR (PNG, base64) ya da None. Kod çok uzunsa (≈2,9 KB üstü) QR üretilemez."""
    try:
        import qrcode
        from io import BytesIO
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=3, border=2)
        qr.add_data(data_str)
        qr.make(fit=True)
        buf = BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


class PureP2PMixin:

    # ── yardımcılar ──
    def _p2p_contact_pem(self, username):
        store = self.state.get("store")
        contact = store.get_contact(username) if store else None
        return contact.get("public_key") if contact else None

    def _p2p_identity_view(self, ident):
        """Karşı tarafın kimlik durumunu anlatan kısa metin + renk."""
        s = ident.status
        if s == p2p_core.VERIFIED:
            return t("p2p_id_verified", user=ident.username), C.success
        if s == p2p_core.NEW:
            return t("p2p_id_new", user=ident.username, fp=ident.fingerprint), C.info_text
        if s == p2p_core.LEGACY:
            return t("p2p_id_legacy"), C.danger
        who = f"'{ident.username}' " if ident.username else ""
        return t("p2p_id_blocked", who=who, detail=ident.detail), C.danger

    def _p2p_start_media(self, pc, mode):
        local_audio = MicrophoneTrack()
        self.state["local_audio_track"] = local_audio
        pc.addTrack(local_audio)
        if mode == "video":
            local_video = CameraTrack()
            self.state["local_video_track"] = local_video
            pc.addTrack(local_video)
            self.start_local_video_rendering()

        @pc.on("track")
        def on_track(track):
            print(f"[P2P] Remote track: {track.kind}")
            if track.kind == "audio":
                player = AudioPlayer(track)
                self.state["audio_player"] = player
                player.start()
            elif track.kind == "video":
                self.start_remote_video_rendering(track)

    def _p2p_watch_call(self, pc, dialog):
        @pc.on("iceconnectionstatechange")
        async def on_ice():
            print(f"[P2P] ICE state: {pc.iceConnectionState}")
            if pc.iceConnectionState in ("connected", "completed"):
                self.state["call_state"] = "connected"

                async def _start_ui():
                    dialog.open = False
                    self.show_call_screen()
                    self.call_status_text.value = t("call_connected")
                    self.page.update()

                self.page.run_task(_start_ui)
                self.page.run_task(self._call_timer_loop)
                self._start_quality_monitor(pc)
            elif pc.iceConnectionState in ("failed", "closed"):
                self.cleanup_call()

    async def _p2p_release_session(self):
        """Yarım kalmış önceki sunucusuz oturumu kapatır: eski RTCPeerConnection,
        mikrofon/kamera izleri ve hoparlör (AudioPlayer).

        Yeni davet/yanıt üretilmeden önce p2p loop'unda çağrılır. cleanup_call'dan
        farkı: ekran DEĞİŞTİRMEZ (sihirbaz açık kalır). Dinleyiciler close()'dan ÖNCE
        sökülür; yoksa eski pc'nin 'closed' ICE olayı _p2p_watch_call üzerinden
        cleanup_call'u tetikler ve sihirbazı kapatıp yeni oturumu da bozar.
        """
        self.state["quality_pc"] = None
        for key in ("local_audio_track", "local_video_track", "audio_player"):
            obj = self.state.get(key)
            self.state[key] = None
            if obj is not None:
                try:
                    obj.stop()
                except Exception as ex:
                    print(f"[P2P] {key} durdurulamadı: {ex}")
        pc = self.state.get("active_pc")
        loop = self.state.get("call_loop")
        self.state["active_pc"] = None
        self.state["call_loop"] = None
        if pc is None:
            return
        try:
            pc.remove_all_listeners()
        except Exception:
            pass
        try:
            if loop is not None and loop is not asyncio.get_running_loop():
                # Başka loop'ta yaşayan pc (ör. sunucu araması, ws_loop) kendi loop'unda kapanır
                asyncio.run_coroutine_threadsafe(pc.close(), loop)
            else:
                await pc.close()
        except Exception as ex:
            print(f"[P2P] Önceki bağlantı kapatılamadı: {ex}")

    async def _p2p_new_pc(self, role, mode, partner="P2P"):
        """Önceki yarım oturumu temizleyip yeni bir bağlantı kurar (p2p loop'unda)."""
        await self._p2p_release_session()
        pc = RTCPeerConnection(configuration=_ice_config())
        self.state.update({
            "call_state": None,    # önceki oturumdan kalan "ended", video önizleme döngüsünü durdurmasın
            "active_pc": pc,
            "call_loop": p2p_core.get_loop(),
            "call_role": role,
            "call_type": mode,
            "call_partner": partner,
        })
        return pc

    @staticmethod
    async def _p2p_gather(pc):
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)

    def _p2p_open_chat_when_ready(self, pc, channel, ident_getter, dialog):
        def _open():
            async def _ui():
                dialog.open = False
                self.page.update()
                self.open_p2p_chat(pc, channel, ident_getter())

            self.page.run_task(_ui)

        if channel.readyState == "open":
            _open()
        else:
            channel.on("open", _open)

    # ── ana sihirbaz diyalogu ──
    def open_pure_p2p_dialog(self, e):
        if not self.state.get("private_key"):
            self.log_status(t("p2p_need_identity"))
            return
        me = self.state["username"]
        priv, pub = self.state["private_key"], self.state["public_key"]

        def qr_box():
            img = ft.Image(src=_BLANK_GIF, width=200, height=200, fit="contain", visible=False)
            note = ft.Text("", size=10, color=C.text_muted, text_align=ft.TextAlign.CENTER)
            box = ft.Container(
                ft.Column([note, img], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                visible=False,
                alignment=ft.Alignment(0, 0),
                padding=ft.Padding(0, 4, 0, 4),
            )

            def show(code):
                b64 = _qr_base64(code)
                box.visible = True
                if b64:
                    img.src, img.visible = f"data:image/png;base64,{b64}", True
                    note.value = t("p2p_qr_note")
                else:
                    img.visible = False
                    note.value = t("p2p_qr_too_long")

            return box, show

        def code_field(label, read_only):
            return ft.TextField(
                label=label,
                multiline=True,
                min_lines=2,
                max_lines=4,
                read_only=read_only,
                border_color=C.border,
                focused_border_color=C.accent,
                border_radius=10,
                bgcolor=C.surface_deep,
                text_size=10,
            )

        def paste_btn(field):
            async def _paste(e):
                try:
                    text = await ft.Clipboard().get()
                except Exception as ex:
                    print(f"[P2P] Clipboard read failed: {ex}")
                    text = None
                if text:
                    field.value = text.strip()
                    self.page.update()

            return ft.TextButton(
                t("paste_clipboard"),
                icon=ft.Icons.CONTENT_PASTE,
                on_click=_paste,
                style=ft.ButtonStyle(color=C.accent_light),
            )

        # ════════════════ Daraltılabilir Ağ Yardımcısı (STUN/TURN) ════════════════
        ice_hint = ft.Text("", size=10, color=C.text_muted)
        ice_custom_tf = ft.TextField(
            label=t("p2p_custom_ice"),
            multiline=True,
            min_lines=2,
            max_lines=4,
            text_size=11,
            value=settings_store.get("p2p_ice_custom"),
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=10,
            bgcolor=C.surface_deep,
            hint_text="stun:host.example.com:3478\nturn:host.example.com:3478 user pass",
        )
        ice_summary = ft.Text("", size=11, color=C.text_secondary, expand=True)

        def ice_refresh():
            preset = ice_dd.value
            ice_custom_tf.visible = preset == "custom"
            ice_summary.value = f"{t('p2p_network_helper')}: {ice_preset_label(preset)}"
            ice_hint.value, ice_hint.color = ice_preset_hint(preset), C.text_muted
            if preset == "custom":
                try:
                    n = len(p2p_core.ice_servers_for("custom", ice_custom_tf.value))
                    ice_hint.value += "\n" + t("p2p_ice_count", n=n)
                except p2p_core.IceConfigError as ex:
                    ice_hint.value, ice_hint.color = t("error_msg", err=ex), C.danger

        def ice_changed(e):
            settings_store.set("p2p_ice", ice_dd.value)
            settings_store.set("p2p_ice_custom", ice_custom_tf.value or "")
            ice_refresh()
            self.page.update()

        ice_dd = ft.Dropdown(
            label=t("p2p_network_helper"),
            dense=True,
            border_color=C.border,
            focused_border_color=C.accent,
            on_select=ice_changed,
            value=(
                settings_store.get("p2p_ice")
                if settings_store.get("p2p_ice") in p2p_core.ICE_PRESETS
                else p2p_core.ICE_DEFAULT
            ),
            options=[ft.dropdown.Option(k, ice_preset_label(k)) for k in p2p_core.ICE_PRESETS],
        )
        ice_custom_tf.on_change = ice_changed
        ice_refresh()

        ice_panel_open = {"on": False}
        ice_panel = ft.Container(
            content=ft.Column([ice_dd, ice_custom_tf, ice_hint], spacing=6, tight=True),
            visible=False,
            padding=ft.Padding(8, 6, 8, 6),
            bgcolor=C.surface_deep,
            border_radius=10,
            border=ft.Border.all(1, C.border),
        )
        ice_chevron = ft.Icon(ft.Icons.EXPAND_MORE, size=18, color=C.text_muted)

        def toggle_ice_panel(e):
            ice_panel_open["on"] = not ice_panel_open["on"]
            ice_panel.visible = ice_panel_open["on"]
            ice_chevron.icon = ft.Icons.EXPAND_LESS if ice_panel_open["on"] else ft.Icons.EXPAND_MORE
            self.page.update()

        ice_toggle_row = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.SETTINGS_ETHERNET, size=15, color=C.text_muted),
                    ice_summary,
                    ice_chevron,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(10, 6, 10, 6),
            border_radius=8,
            bgcolor=C.surface_deep,
            border=ft.Border.all(1, C.border),
            ink=True,
            on_click=toggle_ice_panel,
        )

        # ════════════════ 1. sekme: Davet Başlatan (Caller) ════════════════
        mode_labels_loc = {
            "chat": t("p2p_mode_chat"),
            "audio": t("p2p_mode_audio"),
            "video": t("p2p_mode_video"),
        }
        mode_dd = ft.Dropdown(
            label=t("p2p_conn_type"),
            value="chat",
            expand=True,
            border_color=C.border,
            focused_border_color=C.accent,
            options=[ft.dropdown.Option(k, v) for k, v in mode_labels_loc.items()],
        )
        caller_offer_tf = code_field(t("p2p_invite_ready"), True)
        caller_offer_tf.visible = False
        caller_qr, caller_show_qr = qr_box()
        caller_status = ft.Text("", size=11, color=C.accent_light, selectable=True)
        caller_ident = ft.Text("", size=11, selectable=True)
        caller_prog = ft.ProgressBar(color=C.accent, visible=False)

        def raw_toggle_btn(field):
            """Ham kodu göster/gizle düğmesi (Flet 0.85 TextButton'da metin = content)."""
            btn = ft.TextButton(
                t("p2p_show_raw"),
                icon=ft.Icons.CODE,
                style=ft.ButtonStyle(color=C.text_muted),
            )

            def _toggle(e):
                field.visible = not field.visible
                btn.content = t("p2p_hide_raw") if field.visible else t("p2p_show_raw")
                self.page.update()

            btn.on_click = _toggle
            return btn

        def copy_btn(label, field, status):
            def _copy(e):
                if field.value:
                    self.copy_to_clipboard(field.value)
                    status.value, status.color = t("copied"), C.accent_light
                    self.page.update()

            return ft.Button(
                content=ft.Row([ft.Icon(ft.Icons.COPY, size=16), ft.Text(label)], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
                disabled=True,
                height=40,
                on_click=_copy,
                style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=8)),
            )

        def ready_card(title, sub, copy, raw, field, qr):
            # Kopyala düğmesi tam genişlikte, "ham kod" altında ortada: dar kartta taşmaz
            return ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=C.success, size=18),
                        ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=C.success, expand=True),
                    ], spacing=6),
                    ft.Text(sub, size=11, color=C.text_secondary),
                    copy,
                    ft.Row([raw], alignment=ft.MainAxisAlignment.CENTER),
                    field,
                    qr,
                ], spacing=6, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                visible=False,
                bgcolor=C.surface_deep,
                border=ft.Border.all(1, C.border),
                border_radius=12,
                padding=12,
            )

        caller_raw_btn = raw_toggle_btn(caller_offer_tf)
        caller_copy_btn = copy_btn(t("p2p_copy_invite"), caller_offer_tf, caller_status)
        caller_ready_card = ready_card(
            t("p2p_invite_ready"), t("p2p_invite_ready_sub"), caller_copy_btn, caller_raw_btn, caller_offer_tf, caller_qr
        )

        caller_answer_tf = code_field(t("p2p_step2_answer"), False)
        connect_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.LOCK_OPEN_ROUNDED, size=18), ft.Text(t("p2p_step3_connect"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            height=44,
            disabled=True,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=10)),
        )
        caller_step2_card = ft.Container(
            content=ft.Column([
                caller_answer_tf,
                ft.Row([paste_btn(caller_answer_tf)], alignment=ft.MainAxisAlignment.END),
                connect_btn,
            ], spacing=4, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            visible=False,
        )

        # Sihirbazın sahip olduğu yarım oturum: pc + hangi sekmenin kurduğu
        session = {"ident": None, "offer_sdp": "", "channel": None, "pc": None, "role": None}

        def reset_caller_ui():
            """Davet tarafının önceki (artık kapatılacak) oturumuna ait UI'ı sıfırlar."""
            session.update({"offer_sdp": "", "channel": None, "ident": None})
            caller_ready_card.visible = False
            caller_step2_card.visible = False
            caller_copy_btn.disabled = True
            connect_btn.disabled = True
            caller_offer_tf.value = ""
            caller_answer_tf.value = ""

        def reset_callee_ui():
            callee_ready_card.visible = False
            callee_copy_btn.disabled = True
            callee_answer_tf.value = ""

        async def release_if_current(pc):
            """Hata yolunda yalnızca HÂLÂ etkin olan bu oturumu kapatır (ekran değişmez)."""
            if pc is not None and self.state.get("active_pc") is pc:
                await self._p2p_release_session()
            if session["pc"] is pc:
                session.update({"pc": None, "role": None})

        def caller_fail(msg, can_retry_connect=False):
            async def _ui():
                caller_status.value = t("error_msg", err=msg)
                caller_status.color = C.danger
                caller_prog.visible = False
                gen_offer_btn.disabled = False
                mode_dd.disabled = False
                connect_btn.disabled = not can_retry_connect
                self.page.update()

            self.page.run_task(_ui)

        def gen_offer_click(e):
            gen_offer_btn.disabled = True
            mode_dd.disabled = True
            # Yeni davet önceki yarım oturumu (davet ya da yanıt) kapatır: eski UI da sıfırlanır
            reset_caller_ui()
            reset_callee_ui()
            caller_ident.value = ""
            callee_status.value = callee_ident.value = ""
            caller_status.value = t("p2p_gathering")
            caller_status.color = C.accent_light
            caller_prog.visible = True
            self.page.update()
            mode = mode_dd.value

            async def _setup():
                pc = None
                try:
                    pc = await self._p2p_new_pc("caller", mode)
                    session.update({"pc": pc, "role": "caller"})
                    if mode == "chat":
                        session["channel"] = pc.createDataChannel(p2p_core.CHANNEL_LABEL)
                        self._p2p_open_chat_when_ready(pc, session["channel"], lambda: session["ident"], dialog)
                    else:
                        self._p2p_start_media(pc, mode)
                        self._p2p_watch_call(pc, dialog)
                    await pc.setLocalDescription(await pc.createOffer())
                    await self._p2p_gather(pc)
                    if session["pc"] is not pc:
                        return          # bu arada başka bir oturum başlatıldı
                    session["offer_sdp"] = pc.localDescription.sdp
                    code = p2p_core.make_envelope("offer", mode, session["offer_sdp"], me, priv, pub)

                    async def _done():
                        caller_offer_tf.value = code
                        caller_copy_btn.disabled = False
                        connect_btn.disabled = False
                        gen_offer_btn.disabled = False      # yeniden üretmek önceki oturumu kapatır
                        mode_dd.disabled = False
                        caller_prog.visible = False
                        caller_ready_card.visible = True
                        caller_step2_card.visible = True
                        caller_status.value = ""
                        caller_show_qr(code)
                        self.page.update()

                    self.page.run_task(_done)
                except Exception as ex:
                    print(f"[P2P] Offer error: {ex}")
                    caller_fail(ex)
                    await release_if_current(pc)

            p2p_core.run(_setup())

        gen_offer_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.WIFI_TETHERING, size=18), ft.Text(t("p2p_step1_create"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            on_click=gen_offer_click,
            height=44,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=10)),
        )

        def connect_click(e):
            raw_code = (caller_answer_tf.value or "").strip()
            try:
                env = p2p_core.parse_code(raw_code)
            except p2p_core.P2PCodeError as ex:
                caller_status.value = t("p2p_invalid_answer", err=ex)
                caller_status.color = C.danger
                self.page.update()
                return
            if env.type == "offer":
                if session["offer_sdp"] and env.sdp == session["offer_sdp"]:
                    caller_ident.value = t("p2p_own_invite")
                    caller_ident.color = C.warning
                    caller_status.value = t("p2p_wait_answer")
                    caller_status.color = C.warning
                    self.page.update()
                    return
                # Karşı tarafın DAVET kodu yapıştırılmış: "Davete Katıl" sekmesinde işlenir
                # (bu, kendi yarım davetimizi kapatır — gen_answer_click içinde)
                callee_offer_tf.value = raw_code
                caller_answer_tf.value = ""
                tabs.selected_index = 1
                self.page.update()
                gen_answer_click(e)
                return
            ident = p2p_core.identify_peer(
                env, self._p2p_contact_pem, "answer", own_offer_sdp=session["offer_sdp"]
            )
            if ident.status == p2p_core.LEGACY and mode_dd.value == "chat":
                ident = p2p_core.PeerIdentity(p2p_core.INVALID, detail="legacy code does not support chat")
            elif env.mode != mode_dd.value:
                ident = p2p_core.PeerIdentity(
                    p2p_core.INVALID, username=ident.username, detail="answer belongs to another mode"
                )
            text, color = self._p2p_identity_view(ident)
            caller_ident.value, caller_ident.color = text, color
            if ident.blocked:
                # Engellenen kod ASLA uygulanmaz (setRemoteDescription çağrılmaz). Kimlik
                # uyarısı (caller_ident) görünür kalır; durum satırı onu gizlemez, tekrarlar.
                caller_status.color = C.danger
                if ident.status == p2p_core.KEY_CHANGED:
                    # Olası MITM: bu davet iptal edilir (aynı oturumla devam edilemez)
                    caller_status.value = t("p2p_blocked_key_changed")
                    reset_caller_ui()
                    pc = session["pc"] if session["role"] == "caller" else None
                    p2p_core.run(release_if_current(pc))
                else:
                    caller_status.value = t("p2p_blocked_retry")
                self.page.update()
                return
            pc = session["pc"] if session["role"] == "caller" else None
            if pc is None or self.state.get("active_pc") is not pc:
                caller_fail(ValueError("no active invite; create a new invite"))
                return
            session["ident"] = ident
            self.state["call_partner"] = ident.username or "P2P"
            caller_status.value = t("p2p_connecting")
            caller_status.color = C.accent_light
            connect_btn.disabled = True
            self.page.update()

            async def _connect():
                try:
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=env.sdp, type="answer"))
                except Exception as ex:
                    print(f"[P2P] Connect error: {ex}")
                    caller_fail(ex, can_retry_connect=True)

            p2p_core.run(_connect())

        connect_btn.on_click = connect_click

        # ════════════════ 2. sekme: Davete Katılan (Callee) ════════════════
        callee_offer_tf = code_field(t("p2p_join_step1"), False)
        callee_answer_tf = code_field(t("p2p_answer_ready"), True)
        callee_answer_tf.visible = False
        callee_qr, callee_show_qr = qr_box()
        callee_status = ft.Text("", size=11, color=C.accent_light, selectable=True)
        callee_ident = ft.Text("", size=11, selectable=True)
        callee_prog = ft.ProgressBar(color=C.accent, visible=False)

        callee_raw_btn = raw_toggle_btn(callee_answer_tf)
        callee_copy_btn = copy_btn(t("p2p_copy_answer"), callee_answer_tf, callee_status)
        callee_ready_card = ready_card(
            t("p2p_answer_ready"), t("p2p_answer_ready_sub"), callee_copy_btn, callee_raw_btn, callee_answer_tf, callee_qr
        )

        def callee_fail(msg):
            async def _ui():
                callee_status.value = t("error_msg", err=msg)
                callee_status.color = C.danger
                callee_prog.visible = False
                gen_answer_btn.disabled = False
                self.page.update()

            self.page.run_task(_ui)

        def gen_answer_click(e):
            raw_code = (callee_offer_tf.value or "").strip()
            try:
                env = p2p_core.parse_code(raw_code)
            except p2p_core.P2PCodeError as ex:
                callee_status.value = t("p2p_invalid_invite", err=ex)
                callee_status.color = C.danger
                self.page.update()
                return
            if env.type == "answer":
                if session["offer_sdp"]:
                    # Yanıt kodu yanlış sekmeye yapıştırılmış: davet sekmesinin 2. adımına taşınır
                    caller_answer_tf.value = raw_code
                    callee_offer_tf.value = ""
                    tabs.selected_index = 0
                    self.page.update()
                    connect_click(e)
                    return
                callee_ident.value = t("p2p_is_answer")
                callee_ident.color = C.warning
                callee_status.value = t("p2p_wait_invite")
                callee_status.color = C.warning
                self.page.update()
                return
            ident = p2p_core.identify_peer(env, self._p2p_contact_pem, "offer")
            text, color = self._p2p_identity_view(ident)
            callee_ident.value, callee_ident.color = text, color
            if ident.blocked:
                callee_status.value = t("p2p_blocked")
                callee_status.color = C.danger
                self.page.update()
                return
            mode = env.mode
            # Yeni yanıt önceki yarım oturumu (davet ya da yanıt) kapatır: eski UI da sıfırlanır
            reset_callee_ui()
            reset_caller_ui()
            caller_status.value = caller_ident.value = ""
            callee_status.value = t("p2p_gathering")
            callee_status.color = C.accent_light
            callee_prog.visible = True
            gen_answer_btn.disabled = True
            self.page.update()

            async def _setup():
                pc = None
                try:
                    pc = await self._p2p_new_pc("callee", mode, ident.username or "P2P")
                    session.update({"pc": pc, "role": "callee"})
                    if mode == "chat":
                        @pc.on("datachannel")
                        def on_dc(channel):
                            if channel.label == p2p_core.CHANNEL_LABEL:
                                self._p2p_open_chat_when_ready(pc, channel, lambda: ident, dialog)
                    else:
                        self._p2p_start_media(pc, mode)
                        self._p2p_watch_call(pc, dialog)
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=env.sdp, type="offer"))
                    await pc.setLocalDescription(await pc.createAnswer())
                    await self._p2p_gather(pc)
                    if session["pc"] is not pc:
                        return          # bu arada başka bir oturum başlatıldı
                    code = p2p_core.make_envelope(
                        "answer", mode, pc.localDescription.sdp, me, priv, pub, offer_sdp=env.sdp
                    )

                    async def _done():
                        callee_answer_tf.value = code
                        callee_copy_btn.disabled = False
                        gen_answer_btn.disabled = False     # yeniden üretmek önceki oturumu kapatır
                        callee_prog.visible = False
                        callee_ready_card.visible = True
                        callee_status.value = ""
                        callee_show_qr(code)
                        self.page.update()

                    self.page.run_task(_done)
                except Exception as ex:
                    print(f"[P2P] Answer error: {ex}")
                    callee_fail(ex)
                    await release_if_current(pc)

            p2p_core.run(_setup())

        gen_answer_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.CHECK, size=18), ft.Text(t("p2p_join_step2"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            on_click=gen_answer_click,
            height=44,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=10)),
        )

        # ════════════════ Yerleşim ════════════════
        def tab_body(controls):
            # expand: sekme içeriği TabBarView'in yüksekliğiyle sınırlanır, yoksa Column
            # sınırsız yükseklik alır ve kaydırılamaz (QR'dan sonraki 2. adım kesilirdi).
            # Üstteki boşluk: açılır listenin yüzen etiketi kaydırma alanının kenarında kırpılmasın.
            return ft.Container(
                ft.Column(
                    [ft.Container(height=6), *controls, ft.Container(height=8)],
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                padding=ft.Padding(10, 0, 10, 0),
                expand=True,
            )

        caller_tab = tab_body([
            ft.Row([mode_dd]),
            gen_offer_btn,
            caller_prog,
            caller_ready_card,
            caller_step2_card,
            caller_ident,
            caller_status,
        ])
        callee_tab = tab_body([
            callee_offer_tf,
            ft.Row([paste_btn(callee_offer_tf)], alignment=ft.MainAxisAlignment.END),
            gen_answer_btn,
            callee_ident,
            callee_prog,
            callee_ready_card,
            callee_status,
        ])
        tabs = ft.Tabs(
            selected_index=0,
            length=2,
            expand=True,
            content=ft.Column([
                ft.TabBar(tabs=[ft.Tab(label=t("p2p_tab_start")), ft.Tab(label=t("p2p_tab_join"))]),
                ft.TabBarView(controls=[caller_tab, callee_tab], expand=True),
            ], expand=True),
        )

        def close_p2p(e):
            dialog.open = False
            self.page.update()
            if self.state.get("call_state") != "connected":
                self.cleanup_call()

        dialog = ft.AlertDialog(
            title=ft.Row([
                ft.Icon(ft.Icons.WIFI_TETHERING, color=C.accent_light),
                ft.Text(t("p2p_wizard_title"), size=16, color=C.text, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.CLOSE, icon_size=18, icon_color=C.text_muted, on_click=close_p2p),
            ], spacing=8),
            content=ft.Container(
                ft.Column([
                    ft.Text(t("p2p_wizard_sub"), size=11, color=C.text_muted),
                    ice_toggle_row,
                    ice_panel,
                    tabs,
                ], spacing=8, expand=True),
                width=400,
                height=580,
                padding=0,
            ),
            bgcolor=C.surface,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()
