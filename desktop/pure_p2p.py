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


ICE_HINTS = {
    "google+cloudflare": "Google and Cloudflare STUN servers only see your IP; media flows peer-to-peer.",
    "cloudflare": "Only Cloudflare STUN sees your IP address.",
    "google": "Only Google STUN sees your IP address.",
    "custom": "One server per line:  stun:host:3478  or  turn:host:3478 user pass",
    "lan": "No external requests; connects only on the same local network or reachable IP.",
}


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
            return f"✓ {ident.username} — identity verified (matches saved contact)", C.success
        if s == p2p_core.NEW:
            return (
                f"? {ident.username} — new contact. Verify fingerprint out-of-band:\n{ident.fingerprint}",
                C.info_text,
            )
        if s == p2p_core.LEGACY:
            return "⚠ Legacy unsigned code: sender identity cannot be verified.", C.danger
        who = f"'{ident.username}' " if ident.username else ""
        return f"⛔ {who}connection blocked: {ident.detail}", C.danger

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
                    self.call_status_text.value = "Connected"
                    self.page.update()

                self.page.run_task(_start_ui)
                self.page.run_task(self._call_timer_loop)
                self._start_quality_monitor(pc)
            elif pc.iceConnectionState in ("failed", "closed"):
                self.cleanup_call()

    def _p2p_new_pc(self, role, mode, partner="P2P"):
        pc = RTCPeerConnection(configuration=_ice_config())
        self.state.update({
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
            self.log_status("Sign in or start Direct P2P with a username first.")
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
            label="Custom STUN/TURN",
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
            ice_summary.value = f"{t('p2p_network_helper')}: {p2p_core.ICE_PRESETS.get(preset, preset)}"
            ice_hint.value, ice_hint.color = ICE_HINTS.get(preset, ""), C.text_muted
            if preset == "custom":
                try:
                    n = len(p2p_core.ice_servers_for("custom", ice_custom_tf.value))
                    ice_hint.value += f"\n✓ {n} server(s)"
                except p2p_core.IceConfigError as ex:
                    ice_hint.value, ice_hint.color = f"Error: {ex}", C.danger

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
            options=[ft.dropdown.Option(k, v) for k, v in p2p_core.ICE_PRESETS.items()],
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
            border_color=C.border,
            focused_border_color=C.accent,
            options=[ft.dropdown.Option(k, v) for k, v in mode_labels_loc.items()],
        )
        caller_offer_tf = code_field(t("p2p_invite_ready"), True)
        caller_offer_tf.visible = False
        caller_qr, caller_show_qr = qr_box()
        caller_status = ft.Text("", size=11, color=C.accent_light)
        caller_ident = ft.Text("", size=11, selectable=True)
        caller_prog = ft.ProgressBar(color=C.accent, visible=False)

        caller_raw_btn = ft.TextButton(
            t("p2p_show_raw"),
            on_click=lambda e: _toggle_caller_raw(),
            style=ft.ButtonStyle(color=C.text_muted),
        )

        def _toggle_caller_raw():
            caller_offer_tf.visible = not caller_offer_tf.visible
            caller_raw_btn.text = t("p2p_hide_raw") if caller_offer_tf.visible else t("p2p_show_raw")
            self.page.update()

        caller_copy_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.COPY, size=16), ft.Text(t("p2p_copy_invite"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            disabled=True,
            on_click=lambda e: (
                self.copy_to_clipboard(caller_offer_tf.value),
                setattr(caller_status, "value", t("copied")),
                self.page.update(),
            ) if caller_offer_tf.value else None,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=8)),
        )

        caller_ready_card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=C.success, size=18),
                    ft.Text(t("p2p_invite_ready"), size=13, weight=ft.FontWeight.BOLD, color=C.success),
                ], spacing=6),
                ft.Text(t("p2p_invite_ready_sub"), size=11, color=C.text_secondary),
                ft.Row([caller_copy_btn, caller_raw_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                caller_offer_tf,
                caller_qr,
            ], spacing=6, tight=True),
            visible=False,
            bgcolor=C.surface_deep,
            border=ft.Border.all(1, C.border),
            border_radius=12,
            padding=12,
        )

        caller_answer_tf = code_field(t("p2p_step2_answer"), False)
        connect_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.LOCK_OPEN_ROUNDED, size=18), ft.Text(t("p2p_step3_connect"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            width=360,
            height=44,
            disabled=True,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=10)),
        )
        caller_step2_card = ft.Container(
            content=ft.Column([
                caller_answer_tf,
                ft.Row([paste_btn(caller_answer_tf)], alignment=ft.MainAxisAlignment.END),
                connect_btn,
            ], spacing=4, tight=True),
            visible=False,
        )

        session = {"ident": None, "offer_sdp": "", "channel": None}

        def caller_fail(msg):
            async def _ui():
                caller_status.value = f"Error: {msg}"
                caller_status.color = C.danger
                caller_prog.visible = False
                gen_offer_btn.disabled = False
                mode_dd.disabled = False
                self.page.update()

            self.page.run_task(_ui)

        def gen_offer_click(e):
            gen_offer_btn.disabled = True
            mode_dd.disabled = True
            caller_status.value = t("p2p_gathering")
            caller_status.color = C.accent_light
            caller_prog.visible = True
            self.page.update()
            mode = mode_dd.value

            async def _setup():
                try:
                    pc = self._p2p_new_pc("caller", mode)
                    if mode == "chat":
                        session["channel"] = pc.createDataChannel(p2p_core.CHANNEL_LABEL)
                        self._p2p_open_chat_when_ready(pc, session["channel"], lambda: session["ident"], dialog)
                    else:
                        self._p2p_start_media(pc, mode)
                        self._p2p_watch_call(pc, dialog)
                    await pc.setLocalDescription(await pc.createOffer())
                    await self._p2p_gather(pc)
                    session["offer_sdp"] = pc.localDescription.sdp
                    code = p2p_core.make_envelope("offer", mode, session["offer_sdp"], me, priv, pub)

                    async def _done():
                        caller_offer_tf.value = code
                        caller_copy_btn.disabled = False
                        connect_btn.disabled = False
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
                    self.cleanup_call()

            p2p_core.run(_setup())

        gen_offer_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.WIFI_TETHERING, size=18), ft.Text(t("p2p_step1_create"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            on_click=gen_offer_click,
            width=360,
            height=44,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=10)),
        )

        def connect_click(e):
            try:
                env = p2p_core.parse_code(caller_answer_tf.value or "")
            except p2p_core.P2PCodeError as ex:
                caller_status.value = f"Invalid answer code: {ex}"
                caller_status.color = C.danger
                self.page.update()
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
                caller_status.value = "Connection blocked."
                caller_status.color = C.danger
                connect_btn.disabled = True
                self.page.update()
                self.cleanup_call()
                return
            session["ident"] = ident
            self.state["call_partner"] = ident.username or "P2P"
            caller_status.value = t("p2p_connecting")
            caller_status.color = C.accent_light
            connect_btn.disabled = True
            self.page.update()

            async def _connect():
                try:
                    pc = self.state.get("active_pc")
                    if not pc:
                        raise ValueError("no active session; create a new invite")
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=env.sdp, type="answer"))
                except Exception as ex:
                    print(f"[P2P] Connect error: {ex}")
                    caller_fail(ex)
                    self.cleanup_call()

            p2p_core.run(_connect())

        connect_btn.on_click = connect_click

        # ════════════════ 2. sekme: Davete Katılan (Callee) ════════════════
        callee_offer_tf = code_field(t("p2p_join_step1"), False)
        callee_answer_tf = code_field(t("p2p_answer_ready"), True)
        callee_answer_tf.visible = False
        callee_qr, callee_show_qr = qr_box()
        callee_status = ft.Text("", size=11, color=C.accent_light)
        callee_ident = ft.Text("", size=11, selectable=True)
        callee_prog = ft.ProgressBar(color=C.accent, visible=False)

        callee_raw_btn = ft.TextButton(
            t("p2p_show_raw"),
            on_click=lambda e: _toggle_callee_raw(),
            style=ft.ButtonStyle(color=C.text_muted),
        )

        def _toggle_callee_raw():
            callee_answer_tf.visible = not callee_answer_tf.visible
            callee_raw_btn.text = t("p2p_hide_raw") if callee_answer_tf.visible else t("p2p_show_raw")
            self.page.update()

        callee_copy_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.COPY, size=16), ft.Text(t("p2p_copy_answer"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            disabled=True,
            on_click=lambda e: (
                self.copy_to_clipboard(callee_answer_tf.value),
                setattr(callee_status, "value", t("copied")),
                self.page.update(),
            ) if callee_answer_tf.value else None,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=8)),
        )

        callee_ready_card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=C.success, size=18),
                    ft.Text(t("p2p_answer_ready"), size=13, weight=ft.FontWeight.BOLD, color=C.success),
                ], spacing=6),
                ft.Text(t("p2p_answer_ready_sub"), size=11, color=C.text_secondary),
                ft.Row([callee_copy_btn, callee_raw_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                callee_answer_tf,
                callee_qr,
            ], spacing=6, tight=True),
            visible=False,
            bgcolor=C.surface_deep,
            border=ft.Border.all(1, C.border),
            border_radius=12,
            padding=12,
        )

        def callee_fail(msg):
            async def _ui():
                callee_status.value = f"Error: {msg}"
                callee_status.color = C.danger
                callee_prog.visible = False
                gen_answer_btn.disabled = False
                self.page.update()

            self.page.run_task(_ui)

        def gen_answer_click(e):
            try:
                env = p2p_core.parse_code(callee_offer_tf.value or "")
            except p2p_core.P2PCodeError as ex:
                callee_status.value = f"Invalid invite code: {ex}"
                callee_status.color = C.danger
                self.page.update()
                return
            ident = p2p_core.identify_peer(env, self._p2p_contact_pem, "offer")
            text, color = self._p2p_identity_view(ident)
            callee_ident.value, callee_ident.color = text, color
            if ident.blocked:
                callee_status.value = "Connection blocked."
                callee_status.color = C.danger
                self.page.update()
                return
            mode = env.mode
            callee_status.value = t("p2p_gathering")
            callee_status.color = C.accent_light
            callee_prog.visible = True
            gen_answer_btn.disabled = True
            self.page.update()

            async def _setup():
                try:
                    pc = self._p2p_new_pc("callee", mode, ident.username or "P2P")
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
                    code = p2p_core.make_envelope(
                        "answer", mode, pc.localDescription.sdp, me, priv, pub, offer_sdp=env.sdp
                    )

                    async def _done():
                        callee_answer_tf.value = code
                        callee_copy_btn.disabled = False
                        callee_prog.visible = False
                        callee_ready_card.visible = True
                        callee_status.value = ""
                        callee_show_qr(code)
                        self.page.update()

                    self.page.run_task(_done)
                except Exception as ex:
                    print(f"[P2P] Answer error: {ex}")
                    callee_fail(ex)
                    self.cleanup_call()

            p2p_core.run(_setup())

        gen_answer_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.CHECK, size=18), ft.Text(t("p2p_join_step2"))], alignment=ft.MainAxisAlignment.CENTER, spacing=6),
            on_click=gen_answer_click,
            width=360,
            height=44,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=10)),
        )

        # ════════════════ Yerleşim ════════════════
        def tab_body(controls):
            return ft.Container(ft.Column(controls, spacing=8, scroll=ft.ScrollMode.AUTO), padding=10)

        caller_tab = tab_body([
            mode_dd,
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
