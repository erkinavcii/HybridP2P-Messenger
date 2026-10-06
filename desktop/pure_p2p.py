"""desktop/pure_p2p.py — Sunucusuz (Pure P2P) manuel SDP takası ile arama.

client.py'den taşındı (modülerleştirme): open_pure_p2p_dialog.

NOT (bulgu, agents.md'de belgelendi — bu refactor'da davranış değiştirilmedi):
Bu diyalog hiçbir RSA/AES E2EE çağrısı yapmaz, güvenliği tamamen WebRTC'nin
kendi DTLS-SRTP'sine bırakır ve `state["ws_loop"]"i (WsClientMixin'in kurduğu
event loop) yeniden kullanır — giriş yapıldıktan hemen sonra bu diyalog
açılırsa teorik bir yarış penceresi vardır. KNOWN_ISSUES.md'ye eklendi.
"""

import asyncio
import base64
import json
import zlib

import flet as ft
from desktop.theme import C
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)

from desktop.voip_tracks import MicrophoneTrack, AudioPlayer, CameraTrack


class PureP2PMixin:

    def open_pure_p2p_dialog(self, e):

        def pack_sdp(sdp_str, sdp_type, call_type="audio", compress=True):
            data = {
                "sdp": sdp_str,
                "type": sdp_type,
                "call_type": call_type
            }
            json_str = json.dumps(data)
            if compress:
                compressed = zlib.compress(json_str.encode("utf-8"))
                b64 = base64.b64encode(compressed).decode("ascii")
                return f"z1:{b64}"
            else:
                b64 = base64.b64encode(json_str.encode("utf-8")).decode("ascii")
                return f"v1:{b64}"

        def unpack_sdp(packed_str):
            packed_str = packed_str.strip()
            if packed_str.startswith("z1:"):
                b64 = packed_str[3:]
                compressed = base64.b64decode(b64)
                json_bytes = zlib.decompress(compressed)
                return json.loads(json_bytes.decode("utf-8"))
            elif packed_str.startswith("v1:"):
                b64 = packed_str[3:]
                json_bytes = base64.b64decode(b64)
                return json.loads(json_bytes.decode("utf-8"))
            else:
                try:
                    decoded = base64.b64decode(packed_str)
                    try:
                        decomp = zlib.decompress(decoded)
                        return json.loads(decomp.decode("utf-8"))
                    except Exception:
                        return json.loads(decoded.decode("utf-8"))
                except Exception:
                    raise ValueError("Invalid packed SDP format")

        def generate_qr_code_image(data_str):
            try:
                import qrcode
                from io import BytesIO
                qr = qrcode.QRCode(version=1, box_size=6, border=2)
                qr.add_data(data_str)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buffered = BytesIO()
                img.save(buffered, format="PNG")
                return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"
            except ImportError:
                return None

        # Tab 1: Caller controls
        caller_call_type = ft.Dropdown(
            label="Görüşme Tipi",
            options=[
                ft.dropdown.Option("audio", "Sesli Arama (Audio)"),
                ft.dropdown.Option("video", "Görüntülü Arama (Video)"),
            ],
            value="audio",
            border_color=C.surface_alt,
            focused_border_color=C.accent,
        )

        caller_offer_tf = ft.TextField(
            label="Arama Teklifiniz (Offer Kodu)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            text_size=10,
        )

        caller_qr_image = ft.Image(src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", width=160, height=160, fit="contain", visible=False)
        caller_qr_container = ft.Container(
            content=ft.Column([
                ft.Text("QR Kod (Karşı tarafa taratın):", size=11, color=C.text_muted),
                caller_qr_image
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            visible=False,
            alignment=ft.Alignment(0, 0)
        )

        caller_status_text = ft.Text("", size=11, color=C.accent)
        caller_prog = ft.ProgressBar(color=C.accent, visible=False)

        caller_copy_btn = ft.Button(
            content="Teklifi Kopyala",
            icon=ft.Icons.COPY,
            on_click=lambda e: self.copy_to_clipboard(caller_offer_tf.value) if caller_offer_tf.value else None,
            disabled=True,
            style=ft.ButtonStyle(bgcolor=C.surface_alt, color=C.text)
        )

        caller_answer_tf = ft.TextField(
            label="Karşı Tarafın Cevabı (Answer Kodu)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            text_size=10,
        )

        p2p_connect_btn = ft.Button(
            content="3. Bağlan ve Görüşmeyi Başlat",
            icon=ft.Icons.PLAY_ARROW,
            width=300,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent),
            disabled=True
        )

        def generate_offer_click(e):
            p2p_gen_offer_btn.disabled = True
            caller_status_text.value = "ICE adayları toplanıyor (2-5 sn)..."
            caller_prog.visible = True
            self.page.update()

            async def _setup_offer():
                try:
                    config_servers = [
                        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                        RTCIceServer(urls=["stun:stun.cloudflare.com:3478"])
                    ]
                    config = RTCConfiguration(iceServers=config_servers)
                    pc = RTCPeerConnection(configuration=config)
                    self.state["active_pc"] = pc
                    self.state["call_role"] = "caller"
                    self.state["call_type"] = caller_call_type.value
                    self.state["call_partner"] = "Pure P2P Peer"

                    local_audio = MicrophoneTrack()
                    self.state["local_audio_track"] = local_audio
                    pc.addTrack(local_audio)

                    if self.state["call_type"] == "video":
                        local_video = CameraTrack()
                        self.state["local_video_track"] = local_video
                        pc.addTrack(local_video)
                        self.start_local_video_rendering()

                    @pc.on("track")
                    def on_track(track):
                        print(f"[VoIP] P2P Remote track: {track.kind}")
                        if track.kind == "audio":
                            player = AudioPlayer(track)
                            self.state["audio_player"] = player
                            player.start()
                        elif track.kind == "video":
                            self.start_remote_video_rendering(track)

                    @pc.on("iceconnectionstatechange")
                    async def on_iceconnectionstatechange():
                        print(f"[VoIP] P2P ICE state: {pc.iceConnectionState}")
                        if pc.iceConnectionState in ["connected", "completed"]:
                            self.state["call_state"] = "connected"
                            async def _start_ui():
                                dialog.open = False
                                self.show_call_screen()
                                self.call_status_text.value = "Connected"
                                self.page.update()
                            self.page.run_task(_start_ui)
                            self.page.run_task(self._call_timer_loop)
                        elif pc.iceConnectionState in ["failed", "closed"]:
                            self.cleanup_call()

                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)

                    while pc.iceGatheringState != "complete":
                        await asyncio.sleep(0.05)

                    packed = pack_sdp(pc.localDescription.sdp, "offer", call_type=self.state["call_type"])

                    async def _done():
                        caller_offer_tf.value = packed
                        caller_copy_btn.disabled = False
                        p2p_connect_btn.disabled = False
                        caller_status_text.value = "Teklif üretildi! Karşı tarafa gönderin."
                        caller_prog.visible = False
                        qr_url = generate_qr_code_image(packed)
                        if qr_url:
                            caller_qr_image.src_base64 = qr_url.split(",")[1]
                            caller_qr_image.visible = True
                            caller_qr_container.visible = True
                        else:
                            caller_status_text.value += " (QR kod için 'qrcode' modülü eksik)"
                        self.page.update()
                    self.page.run_task(_done)

                except Exception as ex:
                    print(f"P2P Offer setup error: {ex}")
                    async def _fail(msg=str(ex)):
                        caller_status_text.value = f"Hata: {msg}"
                        caller_prog.visible = False
                        p2p_gen_offer_btn.disabled = False
                        self.page.update()
                    self.page.run_task(_fail)
                    self.cleanup_call()

            asyncio.run_coroutine_threadsafe(_setup_offer(), self.state["ws_loop"])

        p2p_gen_offer_btn = ft.Button(
            content="1. Arama Teklifi (Offer) Üret",
            icon=ft.Icons.WIFI,
            on_click=generate_offer_click,
            width=300,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent)
        )

        def connect_call_click(e):
            if not caller_answer_tf.value:
                caller_status_text.value = "Lütfen karşı tarafın cevap kodunu girin!"
                self.page.update()
                return

            caller_status_text.value = "Bağlanıyor..."
            self.page.update()

            async def _connect():
                try:
                    raw_answer = caller_answer_tf.value.strip()
                    unpacked = unpack_sdp(raw_answer)
                    remote_sdp = unpacked.get("sdp", "")
                    pc = self.state.get("active_pc")
                    if pc:
                        await pc.setRemoteDescription(RTCSessionDescription(
                            sdp=remote_sdp,
                            type="answer"
                        ))
                    else:
                        raise ValueError("Aktif PeerConnection bulunamadı.")
                except Exception as ex:
                    print(f"P2P Connect error: {ex}")
                    async def _fail(msg=str(ex)):
                        caller_status_text.value = f"Hata: {msg}"
                        self.page.update()
                    self.page.run_task(_fail)
                    self.cleanup_call()

            asyncio.run_coroutine_threadsafe(_connect(), self.state["ws_loop"])

        p2p_connect_btn.on_click = connect_call_click

        # Tab 2: Callee controls
        callee_offer_tf = ft.TextField(
            label="Karşı Tarafın Teklifi (Offer Kodu Yapıştırın)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            text_size=10,
        )

        callee_answer_tf = ft.TextField(
            label="Cevabınız (Answer Kodu)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            text_size=10,
        )

        callee_qr_image = ft.Image(src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", width=160, height=160, fit="contain", visible=False)
        callee_qr_container = ft.Container(
            content=ft.Column([
                ft.Text("QR Kod (Karşı tarafa taratın):", size=11, color=C.text_muted),
                callee_qr_image
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            visible=False,
            alignment=ft.Alignment(0, 0)
        )

        callee_status_text = ft.Text("", size=11, color=C.accent)
        callee_prog = ft.ProgressBar(color=C.accent, visible=False)

        callee_copy_btn = ft.Button(
            content="Cevabı Kopyala",
            icon=ft.Icons.COPY,
            on_click=lambda e: self.copy_to_clipboard(callee_answer_tf.value) if callee_answer_tf.value else None,
            disabled=True,
            style=ft.ButtonStyle(bgcolor=C.surface_alt, color=C.text)
        )

        def generate_answer_click(e):
            if not callee_offer_tf.value:
                callee_status_text.value = "Lütfen önce teklif kodunu girin!"
                self.page.update()
                return

            p2p_gen_answer_btn.disabled = True
            callee_status_text.value = "Cevap hazırlanıyor (2-5 sn)..."
            callee_prog.visible = True
            self.page.update()

            async def _setup_answer():
                try:
                    raw_offer = callee_offer_tf.value.strip()
                    unpacked = unpack_sdp(raw_offer)
                    call_type = unpacked.get("call_type", "audio")
                    remote_sdp = unpacked.get("sdp", "")

                    config_servers = [
                        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                        RTCIceServer(urls=["stun:stun.cloudflare.com:3478"])
                    ]
                    config = RTCConfiguration(iceServers=config_servers)
                    pc = RTCPeerConnection(configuration=config)
                    self.state["active_pc"] = pc
                    self.state["call_role"] = "callee"
                    self.state["call_type"] = call_type
                    self.state["call_partner"] = "Pure P2P Peer"

                    local_audio = MicrophoneTrack()
                    self.state["local_audio_track"] = local_audio
                    pc.addTrack(local_audio)

                    if call_type == "video":
                        local_video = CameraTrack()
                        self.state["local_video_track"] = local_video
                        pc.addTrack(local_video)
                        self.start_local_video_rendering()

                    @pc.on("track")
                    def on_track(track):
                        print(f"[VoIP] P2P Remote track: {track.kind}")
                        if track.kind == "audio":
                            player = AudioPlayer(track)
                            self.state["audio_player"] = player
                            player.start()
                        elif track.kind == "video":
                            self.start_remote_video_rendering(track)

                    @pc.on("iceconnectionstatechange")
                    async def on_iceconnectionstatechange():
                        print(f"[VoIP] P2P ICE state: {pc.iceConnectionState}")
                        if pc.iceConnectionState in ["connected", "completed"]:
                            self.state["call_state"] = "connected"
                            async def _start_ui():
                                dialog.open = False
                                self.show_call_screen()
                                self.call_status_text.value = "Connected"
                                self.page.update()
                            self.page.run_task(_start_ui)
                            self.page.run_task(self._call_timer_loop)
                        elif pc.iceConnectionState in ["failed", "closed"]:
                            self.cleanup_call()

                    await pc.setRemoteDescription(RTCSessionDescription(
                        sdp=remote_sdp,
                        type="offer"
                    ))

                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)

                    while pc.iceGatheringState != "complete":
                        await asyncio.sleep(0.05)

                    packed = pack_sdp(pc.localDescription.sdp, "answer", call_type=call_type)

                    async def _done():
                        callee_answer_tf.value = packed
                        callee_copy_btn.disabled = False
                        callee_status_text.value = "Cevap üretildi! Karşı tarafa gönderin. Bağlantı bekleniyor..."
                        callee_prog.visible = False
                        qr_url = generate_qr_code_image(packed)
                        if qr_url:
                            callee_qr_image.src_base64 = qr_url.split(",")[1]
                            callee_qr_image.visible = True
                            callee_qr_container.visible = True
                        self.page.update()
                    self.page.run_task(_done)

                except Exception as ex:
                    print(f"P2P Answer setup error: {ex}")
                    async def _fail(msg=str(ex)):
                        callee_status_text.value = f"Hata: {msg}"
                        callee_prog.visible = False
                        p2p_gen_answer_btn.disabled = False
                        self.page.update()
                    self.page.run_task(_fail)
                    self.cleanup_call()

            asyncio.run_coroutine_threadsafe(_setup_answer(), self.state["ws_loop"])

        p2p_gen_answer_btn = ft.Button(
            content="2. Kabul Et ve Cevap (Answer) Üret",
            icon=ft.Icons.CHECK,
            on_click=generate_answer_click,
            width=300,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent)
        )

        caller_tab = ft.Container(
            content=ft.Column(
                controls=[
                    caller_call_type,
                    p2p_gen_offer_btn,
                    caller_prog,
                    caller_offer_tf,
                    caller_copy_btn,
                    caller_qr_container,
                    ft.Divider(color=C.surface_alt, height=10),
                    caller_answer_tf,
                    p2p_connect_btn,
                    caller_status_text,
                ],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=10
        )

        callee_tab = ft.Container(
            content=ft.Column(
                controls=[
                    callee_offer_tf,
                    p2p_gen_answer_btn,
                    callee_prog,
                    callee_answer_tf,
                    callee_copy_btn,
                    callee_qr_container,
                    callee_status_text,
                ],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=10
        )

        tabs = ft.Tabs(
            selected_index=0,
            length=2,
            content=ft.Column(
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Arama Başlat (Caller)"),
                            ft.Tab(label="Aramaya Cevap Ver (Callee)"),
                        ]
                    ),
                    ft.TabBarView(
                        controls=[
                            caller_tab,
                            callee_tab,
                        ],
                        expand=True
                    )
                ],
                expand=True
            ),
            expand=True
        )

        def close_p2p(e):
            dialog.open = False
            self.page.update()
            if self.state.get("call_state") != "connected":
                self.cleanup_call()

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WIFI_TETHERING, color=C.accent),
                    ft.Text("Pure P2P (Sunucusuz Bağlantı)", size=16, color=C.text, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color=C.text_muted,
                        on_click=close_p2p,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=tabs,
                width=380,
                height=460,
                padding=0,
            ),
            bgcolor=C.surface,
        )

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()
