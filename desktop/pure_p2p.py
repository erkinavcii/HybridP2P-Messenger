"""desktop/pure_p2p.py — Sunucusuz ("telsiz") bağlantı: manuel kod takasıyla
yazılı mesajlaşma ya da sesli/görüntülü arama.

Akış: A "teklif kodu" üretir → B'ye herhangi bir kanaldan (kopyala-yapıştır, QR)
iletir → B kodu doğrular ve "cevap kodu" üretir → A cevabı yapıştırır → iki
cihaz doğrudan bağlanır. Arada sunucu yoktur; iki taraf da aynı anda açık olmalı.

Kodlar imzalıdır ve karşı tarafın kimliği rehberle karşılaştırılır (ayrıntılar
ve güvenlik modeli: desktop/p2p_core.py). Engellenen durumlarda (anahtar
değişmiş, imza geçersiz, başka teklife ait cevap) bağlantı kurulmaz.

aiortc nesneleri sunucu WebSocket'inin loop'unda değil, P2P'nin kendi arka plan
loop'unda çalışır (p2p_core.get_loop) — sunucu yokken de kullanılabilsin diye.
"""

import asyncio
import base64

import flet as ft
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from desktop import p2p_core
from desktop.theme import C
from desktop.voip_tracks import AudioPlayer, CameraTrack, MicrophoneTrack

_BLANK_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

MODE_LABELS = {
    "chat": "Yazılı mesajlaşma",
    "audio": "Sesli arama",
    "video": "Görüntülü arama",
}


def _ice_config():
    # S4'te seçilebilir olacak (Google / Cloudflare / özel / yalnızca yerel ağ)
    return RTCConfiguration(iceServers=[
        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
        RTCIceServer(urls=["stun:stun.cloudflare.com:3478"]),
    ])


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
            return f"✓ {ident.username} — kimlik doğrulandı (rehberdeki anahtarla eşleşiyor)", C.success
        if s == p2p_core.NEW:
            return (f"? {ident.username} — rehberinizde yok. Parmak izini karşı tarafla "
                    f"başka bir kanaldan karşılaştırın:\n{ident.fingerprint}"), C.info_text
        if s == p2p_core.LEGACY:
            return "⚠ Eski biçim kod: karşı tarafın kimliği doğrulanamıyor.", C.danger
        who = f"'{ident.username}' " if ident.username else ""
        return f"⛔ {who}bağlantı engellendi: {ident.detail}", C.danger

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
            print(f"[P2P] Uzak iz: {track.kind}")
            if track.kind == "audio":
                player = AudioPlayer(track)
                self.state["audio_player"] = player
                player.start()
            elif track.kind == "video":
                self.start_remote_video_rendering(track)

    def _p2p_watch_call(self, pc, dialog):
        @pc.on("iceconnectionstatechange")
        async def on_ice():
            print(f"[P2P] ICE durumu: {pc.iceConnectionState}")
            if pc.iceConnectionState in ("connected", "completed"):
                self.state["call_state"] = "connected"

                async def _start_ui():
                    dialog.open = False
                    self.show_call_screen()
                    self.call_status_text.value = "Connected"
                    self.page.update()
                self.page.run_task(_start_ui)
                self.page.run_task(self._call_timer_loop)
            elif pc.iceConnectionState in ("failed", "closed"):
                self.cleanup_call()

    def _p2p_new_pc(self, role, mode, partner="P2P"):
        pc = RTCPeerConnection(configuration=_ice_config())
        self.state.update({"active_pc": pc, "call_loop": p2p_core.get_loop(), "call_role": role,
                           "call_type": mode, "call_partner": partner})
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

    # ── ana diyalog ──
    def open_pure_p2p_dialog(self, e):
        if not self.state.get("private_key"):
            self.log_status("Sunucusuz mod için önce kimlik anahtarınızla giriş yapın.")
            return
        me = self.state["username"]
        priv, pub = self.state["private_key"], self.state["public_key"]

        def qr_box():
            img = ft.Image(src=_BLANK_GIF, width=220, height=220, fit="contain", visible=False)
            note = ft.Text("", size=10, color=C.text_muted)
            box = ft.Container(ft.Column([note, img], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                               visible=False, alignment=ft.Alignment(0, 0))

            def show(code):
                b64 = _qr_base64(code)
                box.visible = True
                if b64:
                    # Flet 0.85'te src_base64 yok (atama sessizce boşa gider); data URL kullanılır
                    img.src, img.visible = f"data:image/png;base64,{b64}", True
                    note.value = "QR kod (karşı tarafa okutun; okumazsa kodu kopyalayın):"
                else:
                    img.visible = False
                    note.value = "Kod QR'a sığmayacak kadar uzun — kopyala-yapıştır kullanın."
            return box, show

        def code_field(label, read_only):
            return ft.TextField(label=label, multiline=True, min_lines=3, max_lines=5,
                                read_only=read_only, border_color=C.surface_alt,
                                focused_border_color=C.accent, text_size=10)

        # ════════════════ 1. sekme: bağlantıyı başlatan ════════════════
        mode_dd = ft.Dropdown(
            label="Bağlantı türü", value="chat", border_color=C.surface_alt, focused_border_color=C.accent,
            options=[ft.dropdown.Option(k, v) for k, v in MODE_LABELS.items()],
        )
        caller_offer_tf = code_field("Teklif kodunuz — karşı tarafa gönderin", True)
        caller_qr, caller_show_qr = qr_box()
        caller_status = ft.Text("", size=11, color=C.accent)
        caller_ident = ft.Text("", size=11, selectable=True)
        caller_prog = ft.ProgressBar(color=C.accent, visible=False)
        caller_copy_btn = ft.Button(
            content="Teklifi kopyala", icon=ft.Icons.COPY, disabled=True,
            on_click=lambda e: self.copy_to_clipboard(caller_offer_tf.value) if caller_offer_tf.value else None,
            style=ft.ButtonStyle(bgcolor=C.surface_alt, color=C.text))
        caller_answer_tf = code_field("2. Karşı tarafın cevap kodunu yapıştırın", False)
        connect_btn = ft.Button(content="3. Doğrula ve bağlan", icon=ft.Icons.PLAY_ARROW, width=300,
                                disabled=True, style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent))
        session = {"ident": None, "offer_sdp": "", "channel": None}

        def caller_fail(msg):
            async def _ui():
                caller_status.value = f"Hata: {msg}"
                caller_prog.visible = False
                gen_offer_btn.disabled = False
                self.page.update()
            self.page.run_task(_ui)

        def gen_offer_click(e):
            gen_offer_btn.disabled = True
            mode_dd.disabled = True
            caller_status.value = "Ağ adresleri toplanıyor (2-5 sn)…"
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
                        caller_status.value = ("Teklif hazır. Karşı tarafa iletin (kod tek kullanımlıktır) "
                                               "ve cevabını aşağıya yapıştırın.")
                        caller_show_qr(code)
                        self.page.update()
                    self.page.run_task(_done)
                except Exception as ex:
                    print(f"[P2P] Teklif hatası: {ex}")
                    caller_fail(ex)
                    self.cleanup_call()

            p2p_core.run(_setup())

        gen_offer_btn = ft.Button(content="1. Teklif kodu üret", icon=ft.Icons.WIFI_TETHERING,
                                  on_click=gen_offer_click, width=300,
                                  style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent))

        def connect_click(e):
            try:
                env = p2p_core.parse_code(caller_answer_tf.value or "")
            except p2p_core.P2PCodeError as ex:
                caller_status.value = f"Cevap kodu okunamadı: {ex}"
                self.page.update()
                return
            ident = p2p_core.identify_peer(env, self._p2p_contact_pem, "answer",
                                           own_offer_sdp=session["offer_sdp"])
            if ident.status == p2p_core.LEGACY and mode_dd.value == "chat":
                ident = p2p_core.PeerIdentity(p2p_core.INVALID, detail="eski sürüm yazılı mesajlaşmayı desteklemiyor")
            elif env.mode != mode_dd.value:
                ident = p2p_core.PeerIdentity(p2p_core.INVALID, username=ident.username,
                                              detail="cevap başka bir bağlantı türüne ait")
            text, color = self._p2p_identity_view(ident)
            caller_ident.value, caller_ident.color = text, color
            if ident.blocked:
                caller_status.value = "Bağlanılmadı."
                connect_btn.disabled = True
                self.page.update()
                self.cleanup_call()
                return
            session["ident"] = ident
            self.state["call_partner"] = ident.username or "P2P"
            caller_status.value = "Bağlanıyor…"
            connect_btn.disabled = True
            self.page.update()

            async def _connect():
                try:
                    pc = self.state.get("active_pc")
                    if not pc:
                        raise ValueError("aktif bağlantı yok; yeni teklif üretin")
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=env.sdp, type="answer"))
                except Exception as ex:
                    print(f"[P2P] Bağlanma hatası: {ex}")
                    caller_fail(ex)
                    self.cleanup_call()

            p2p_core.run(_connect())

        connect_btn.on_click = connect_click

        # ════════════════ 2. sekme: koda cevap veren ════════════════
        callee_offer_tf = code_field("1. Karşı tarafın teklif kodunu yapıştırın", False)
        callee_answer_tf = code_field("Cevap kodunuz — karşı tarafa gönderin", True)
        callee_qr, callee_show_qr = qr_box()
        callee_status = ft.Text("", size=11, color=C.accent)
        callee_ident = ft.Text("", size=11, selectable=True)
        callee_prog = ft.ProgressBar(color=C.accent, visible=False)
        callee_copy_btn = ft.Button(
            content="Cevabı kopyala", icon=ft.Icons.COPY, disabled=True,
            on_click=lambda e: self.copy_to_clipboard(callee_answer_tf.value) if callee_answer_tf.value else None,
            style=ft.ButtonStyle(bgcolor=C.surface_alt, color=C.text))

        def callee_fail(msg):
            async def _ui():
                callee_status.value = f"Hata: {msg}"
                callee_prog.visible = False
                gen_answer_btn.disabled = False
                self.page.update()
            self.page.run_task(_ui)

        def gen_answer_click(e):
            try:
                env = p2p_core.parse_code(callee_offer_tf.value or "")
            except p2p_core.P2PCodeError as ex:
                callee_status.value = f"Teklif kodu okunamadı: {ex}"
                self.page.update()
                return
            ident = p2p_core.identify_peer(env, self._p2p_contact_pem, "offer")
            text, color = self._p2p_identity_view(ident)
            callee_ident.value, callee_ident.color = text, color
            if ident.blocked:
                callee_status.value = "Cevap üretilmedi."
                self.page.update()
                return
            mode = env.mode
            callee_status.value = f"{MODE_LABELS[mode]} için cevap hazırlanıyor (2-5 sn)…"
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
                    code = p2p_core.make_envelope("answer", mode, pc.localDescription.sdp, me, priv, pub,
                                                  offer_sdp=env.sdp)

                    async def _done():
                        callee_answer_tf.value = code
                        callee_copy_btn.disabled = False
                        callee_prog.visible = False
                        callee_status.value = "Cevap hazır. Karşı tarafa iletin; bağlantı bekleniyor…"
                        callee_show_qr(code)
                        self.page.update()
                    self.page.run_task(_done)
                except Exception as ex:
                    print(f"[P2P] Cevap hatası: {ex}")
                    callee_fail(ex)
                    self.cleanup_call()

            p2p_core.run(_setup())

        gen_answer_btn = ft.Button(content="2. Doğrula ve cevap kodu üret", icon=ft.Icons.CHECK,
                                   on_click=gen_answer_click, width=300,
                                   style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent))

        # ════════════════ yerleşim ════════════════
        def tab_body(controls):
            return ft.Container(ft.Column(controls, spacing=8, scroll=ft.ScrollMode.AUTO), padding=10)

        # QR en sonda: görünürken cevap alanını aşağı itmesin
        caller_tab = tab_body([
            mode_dd, gen_offer_btn, caller_prog, caller_offer_tf, caller_copy_btn,
            ft.Divider(color=C.surface_alt, height=10),
            caller_answer_tf, connect_btn, caller_ident, caller_status, caller_qr,
        ])
        callee_tab = tab_body([
            callee_offer_tf, gen_answer_btn, callee_ident, callee_prog, callee_answer_tf,
            callee_copy_btn, callee_status, callee_qr,
        ])
        tabs = ft.Tabs(
            selected_index=0, length=2, expand=True,
            content=ft.Column([
                ft.TabBar(tabs=[ft.Tab(label="Bağlantı başlat"), ft.Tab(label="Koda cevap ver")]),
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
                ft.Icon(ft.Icons.WIFI_TETHERING, color=C.accent),
                ft.Text("Sunucusuz bağlantı", size=16, color=C.text, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.CLOSE, icon_size=18, icon_color=C.text_muted, on_click=close_p2p),
            ], spacing=8),
            content=ft.Container(
                ft.Column([
                    ft.Text("Arada sunucu yok: iki taraf da aynı anda açık olmalı. Kodlar imzalıdır "
                            "ve tek kullanımlıktır.", size=10, color=C.text_muted),
                    tabs,
                ], spacing=6, expand=True),
                width=400, height=540, padding=0,
            ),
            bgcolor=C.surface,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()
