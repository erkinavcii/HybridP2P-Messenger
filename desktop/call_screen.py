"""desktop/call_screen.py — Sunucu-aracılı VoIP arama ekranı ve WebRTC akışı.

client.py'den taşındı (modülerleştirme): show_call_screen, start_voip_call,
accept_call_clicked, decline_call_clicked, hangup_call_clicked, toggle_call_mic,
toggle_call_cam, _call_timer_loop, start_local_video_rendering,
start_remote_video_rendering, cleanup_call.

WsClientMixin._ws_listen bu mixin'in show_call_screen/cleanup_call metotlarını
çağırır ve state["ws_loop"] (WsClientMixin tarafından kurulur) burada
asyncio.run_coroutine_threadsafe ile yeniden kullanılır — kasıtlı çift yönlü
bağımlılık, bkz. ws_client.py başlığı.
"""

import asyncio
import base64
import uuid as uuid_lib

import cv2
import flet as ft
from desktop.theme import C
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)

from desktop.voip_tracks import MicrophoneTrack, AudioPlayer, CameraTrack


class CallScreenMixin:

    def show_call_screen(self):
        self.fab.visible = False
        self.call_avatar.content.value = self.state["call_partner"][:1].upper() if self.state["call_partner"] else "?"
        self.call_name_text.value = self.state["call_partner"]

        # Reset button states
        self.mic_btn.icon = ft.Icons.MIC
        self.mic_btn.icon_color = C.text
        self.mic_btn.bgcolor = C.surface_alt
        self.cam_btn.icon = ft.Icons.VIDEOCAM
        self.cam_btn.icon_color = C.text
        self.cam_btn.bgcolor = C.surface_alt

        if self.state["call_state"] == "ringing":
            self.call_status_text.value = f"Incoming {self.state['call_type']} call..."
            self.call_controls_container.content = self.callee_controls_row
            self.video_container.visible = False
            self.call_avatar.visible = True
            self.call_timer_text.visible = False
        elif self.state["call_state"] == "calling":
            self.call_status_text.value = "Calling..."
            self.call_controls_container.content = self.caller_controls_row
            self.video_container.visible = self.state["call_type"] == "video"
            self.call_avatar.visible = self.state["call_type"] == "audio"
            self.call_timer_text.visible = False
        elif self.state["call_state"] == "connected":
            self.call_status_text.value = "Connected"
            self.call_controls_container.content = self.caller_controls_row
            self.video_container.visible = self.state["call_type"] == "video"
            self.call_avatar.visible = self.state["call_type"] == "audio"
            self.call_timer_text.visible = True

        self.page.controls.clear()
        self.page.add(self.call_view)
        self.page.update()

    def start_voip_call(self, video: bool):
        if not self.is_ws_connected():
            self.log_status("HATA: WebSocket bağlı değil, arama yapılamaz!")
            return

        call_id = str(uuid_lib.uuid4())
        self.state["active_call_id"] = call_id
        self.state["call_role"] = "caller"
        self.state["call_partner"] = self.state["recipient"]
        self.state["call_type"] = "video" if video else "audio"
        self.state["call_state"] = "calling"

        self.show_call_screen()

        async def _setup():
            try:
                # ICE listesi sunucudan (STUN/TURN ayarı orada). Alınamazsa üçüncü
                # tarafa sessizce dönülmez: boş liste = yalnızca yerel ağ adayları.
                try:
                    r = self.signed_get("/api/ice_servers")
                    ice_data = r.json().get("ice_servers", []) if r.status_code == 200 else []
                except Exception:
                    ice_data = []

                config_servers = []
                for s in ice_data:
                    urls = s.get("urls")
                    if isinstance(urls, str):
                        urls = [urls]
                    config_servers.append(RTCIceServer(
                        urls=urls,
                        username=s.get("username"),
                        credential=s.get("credential")
                    ))

                config = RTCConfiguration(iceServers=config_servers)
                pc = RTCPeerConnection(configuration=config)
                self.state["active_pc"] = pc

                local_audio = MicrophoneTrack()
                self.state["local_audio_track"] = local_audio
                pc.addTrack(local_audio)

                if video:
                    local_video = CameraTrack()
                    self.state["local_video_track"] = local_video
                    pc.addTrack(local_video)
                    self.start_local_video_rendering()

                @pc.on("track")
                def on_track(track):
                    print(f"[VoIP] Remote track received: {track.kind}")
                    if track.kind == "audio":
                        player = AudioPlayer(track)
                        self.state["audio_player"] = player
                        player.start()
                    elif track.kind == "video":
                        self.start_remote_video_rendering(track)

                @pc.on("iceconnectionstatechange")
                async def on_iceconnectionstatechange():
                    print(f"[VoIP] ICE connection state: {pc.iceConnectionState}")
                    if pc.iceConnectionState in ["connected", "completed"]:
                        if self.call_status_text.value != "Connected":
                            self.state["call_state"] = "connected"
                            async def _start_call_ui():
                                self.call_status_text.value = "Connected"
                                self.page.update()
                            self.page.run_task(_start_call_ui)
                            self.page.run_task(self._call_timer_loop)
                    elif pc.iceConnectionState in ["failed", "closed"]:
                        self.cleanup_call()

                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)

                while pc.iceGatheringState != "complete":
                    await asyncio.sleep(0.05)

                self.send_ws_message_with_fallback({
                    "type": "call_offer",
                    "recipient": self.state["call_partner"],
                    "call_id": self.state["active_call_id"],
                    "call_type": self.state["call_type"],
                    "sdp_offer": pc.localDescription.sdp
                })

            except Exception as ex:
                print(f"[VoIP] Setup error: {ex}")
                import traceback
                traceback.print_exc()
                self.cleanup_call()

        asyncio.run_coroutine_threadsafe(_setup(), self.state["ws_loop"])

    def accept_call_clicked(self):
        if self.state.get("call_state") != "ringing":
            return

        self.state["call_state"] = "connected"
        self.call_status_text.value = "Connecting..."
        self.call_controls_container.content = self.caller_controls_row
        self.page.update()

        async def _accept():
            try:
                # ICE listesi sunucudan (STUN/TURN ayarı orada). Alınamazsa üçüncü
                # tarafa sessizce dönülmez: boş liste = yalnızca yerel ağ adayları.
                try:
                    r = self.signed_get("/api/ice_servers")
                    ice_data = r.json().get("ice_servers", []) if r.status_code == 200 else []
                except Exception:
                    ice_data = []

                config_servers = []
                for s in ice_data:
                    urls = s.get("urls")
                    if isinstance(urls, str):
                        urls = [urls]
                    config_servers.append(RTCIceServer(
                        urls=urls,
                        username=s.get("username"),
                        credential=s.get("credential")
                    ))

                config = RTCConfiguration(iceServers=config_servers)
                pc = RTCPeerConnection(configuration=config)
                self.state["active_pc"] = pc

                local_audio = MicrophoneTrack()
                self.state["local_audio_track"] = local_audio
                pc.addTrack(local_audio)

                video = (self.state["call_type"] == "video")
                if video:
                    local_video = CameraTrack()
                    self.state["local_video_track"] = local_video
                    pc.addTrack(local_video)
                    self.start_local_video_rendering()

                @pc.on("track")
                def on_track(track):
                    print(f"[VoIP] Remote track received: {track.kind}")
                    if track.kind == "audio":
                        player = AudioPlayer(track)
                        self.state["audio_player"] = player
                        player.start()
                    elif track.kind == "video":
                        self.start_remote_video_rendering(track)

                @pc.on("iceconnectionstatechange")
                async def on_iceconnectionstatechange():
                    print(f"[VoIP] ICE connection state: {pc.iceConnectionState}")
                    if pc.iceConnectionState in ["connected", "completed"]:
                        if self.call_status_text.value != "Connected":
                            self.state["call_state"] = "connected"
                            async def _start_call_ui():
                                self.call_status_text.value = "Connected"
                                self.page.update()
                            self.page.run_task(_start_call_ui)
                            self.page.run_task(self._call_timer_loop)
                    elif pc.iceConnectionState in ["failed", "closed"]:
                        self.cleanup_call()

                await pc.setRemoteDescription(RTCSessionDescription(
                    sdp=self.state["remote_sdp"],
                    type="offer"
                ))

                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                while pc.iceGatheringState != "complete":
                    await asyncio.sleep(0.05)

                self.send_ws_message_with_fallback({
                    "type": "call_answer",
                    "recipient": self.state["call_partner"],
                    "call_id": self.state["active_call_id"],
                    "sdp_answer": pc.localDescription.sdp
                })

            except Exception as ex:
                print(f"[VoIP] Error accepting call: {ex}")
                self.cleanup_call()

        asyncio.run_coroutine_threadsafe(_accept(), self.state["ws_loop"])

    def decline_call_clicked(self):
        if self.state.get("active_call_id") and self.state.get("call_partner"):
            self.send_ws_message_with_fallback({
                "type": "call_reject",
                "recipient": self.state["call_partner"],
                "call_id": self.state["active_call_id"],
                "reason": "rejected"
            })
        self.cleanup_call()

    def hangup_call_clicked(self):
        if self.state.get("active_call_id") and self.state.get("call_partner"):
            self.send_ws_message_with_fallback({
                "type": "call_end",
                "recipient": self.state["call_partner"],
                "call_id": self.state["active_call_id"],
                "duration_seconds": self.state.get("call_duration", 0)
            })
        self.cleanup_call()

    def toggle_call_mic(self):
        track = self.state.get("local_audio_track")
        if track:
            track.enabled = not track.enabled
            self.mic_btn.icon = ft.Icons.MIC if track.enabled else ft.Icons.MIC_OFF
            self.mic_btn.icon_color = C.text if track.enabled else C.danger
            self.mic_btn.bgcolor = C.surface_alt if track.enabled else C.danger_bg
            self.page.update()

    def toggle_call_cam(self):
        track = self.state.get("local_video_track")
        if track:
            track.enabled = not track.enabled
            self.cam_btn.icon = ft.Icons.VIDEOCAM if track.enabled else ft.Icons.VIDEOCAM_OFF
            self.cam_btn.icon_color = C.text if track.enabled else C.danger
            self.cam_btn.bgcolor = C.surface_alt if track.enabled else C.danger_bg
            self.local_video_preview.visible = track.enabled
            self.page.update()

    async def _call_timer_loop(self):
        def _init_timer():
            self.state["call_duration"] = 0
            self.call_timer_text.value = "00:00"
            self.call_timer_text.visible = True
            self.page.update()
        self.run_on_ui(_init_timer)
        while self.state.get("call_state") == "connected":
            await asyncio.sleep(1)
            self.state["call_duration"] += 1
            mins = self.state["call_duration"] // 60
            secs = self.state["call_duration"] % 60
            def _update_timer_ui(m=mins, s=secs):
                self.call_timer_text.value = f"{m:02d}:{s:02d}"
                self.page.update()
            self.run_on_ui(_update_timer_ui)

    def start_local_video_rendering(self):
        async def _init_local_ui():
            self.local_video_preview.visible = True
            self.page.update()
        self.page.run_task(_init_local_ui)

        async def _render_local():
            track = self.state.get("local_video_track")
            while track and track.running and self.state.get("call_state") != "ended":
                try:
                    frame = await track.recv()
                    img = frame.to_ndarray(format='bgr24')
                    _, buffer = cv2.imencode('.jpg', img)
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    def _update_local(b=b64_str):
                        self.local_video_preview.src_base64 = b
                        self.page.update()
                    self.run_on_ui(_update_local)
                except Exception as e:
                    break

        self.page.run_task(_render_local)

    def start_remote_video_rendering(self, track):
        async def _init_remote_ui():
            self.remote_video_view.visible = True
            self.call_avatar.visible = False
            self.video_container.visible = True
            self.page.update()
        self.page.run_task(_init_remote_ui)

        async def _render_remote():
            while self.state.get("call_state") != "ended":
                try:
                    frame = await track.recv()
                    img = frame.to_ndarray(format='bgr24')
                    _, buffer = cv2.imencode('.jpg', img)
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    def _update_remote(b=b64_str):
                        self.remote_video_view.src_base64 = b
                        self.page.update()
                    self.run_on_ui(_update_remote)
                except Exception as e:
                    break

        self.page.run_task(_render_remote)

    def cleanup_call(self):
        print("[VoIP] Cleaning up call...")
        self.state["call_state"] = "ended"

        if self.state.get("local_audio_track"):
            try:
                self.state["local_audio_track"].stop()
            except Exception as e:
                pass
            self.state["local_audio_track"] = None

        if self.state.get("local_video_track"):
            try:
                self.state["local_video_track"].stop()
            except Exception as e:
                pass
            self.state["local_video_track"] = None

        if self.state.get("audio_player"):
            try:
                self.state["audio_player"].stop()
            except Exception as e:
                pass
            self.state["audio_player"] = None

        pc = self.state.get("active_pc")
        if pc:
            try:
                asyncio.run_coroutine_threadsafe(pc.close(), self.state["ws_loop"])
            except Exception as e:
                pass
            self.state["active_pc"] = None

        self.state["active_call_id"] = None
        self.state["call_role"] = None
        self.state["call_partner"] = None
        self.state["call_type"] = None

        async def _cleanup_ui():
            self.local_video_preview.src_base64 = None
            self.local_video_preview.src = self.transparent_placeholder
            self.local_video_preview.visible = False
            self.remote_video_view.src_base64 = None
            self.remote_video_view.src = self.transparent_placeholder
            self.remote_video_view.visible = False
            self.call_timer_text.visible = False

            if self.state.get("logged_in", False):
                self.show_chat_screen()
            else:
                self.show_login_screen()

        self.page.run_task(_cleanup_ui)
