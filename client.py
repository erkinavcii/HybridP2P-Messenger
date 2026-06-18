"""
client.py — Flet Tabanlı E2EE Mesajlaşma İstemcisi
=====================================================
v3.0 — View-Once Mesaj + Dosya/Resim Gönderimi

Yeni Özellikler (v3.0):
  • Tek görünümlü mesaj (view-once): per-mesaj 👁 toggle, otomatik kapanır
  • Dosya/resim gönderimi: AES-GCM ile şifreli upload → UUID → receiver indirir
  • Resimler inline thumbnail olarak gösterilir
  • Diğer dosyalar kaydet butonuyla indirilir
"""

import io
import os
import json
import asyncio
import threading
import tempfile
import base64
import mimetypes
from pathlib import Path
from datetime import datetime, timezone

import requests
import flet as ft
import time
import uuid as uuid_lib
import av
import sounddevice as sd
import cv2
import numpy as np
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
    MediaStreamTrack,
    VideoStreamTrack
)
from av import VideoFrame

from crypto_utils import (
    generate_rsa_keypair,
    save_keys_to_disk,
    load_keys_from_disk,
    public_key_to_pem_string,
    pem_string_to_public_key,
    get_public_key_fingerprint,
    encrypt_message,
    decrypt_message,
    encrypt_bytes,
    decrypt_bytes,
    encrypt_symmetric,
    decrypt_symmetric,
)
from message_store import MessageStore

# ── Sunucu Ayarları ─────────────────────────────────────────────────
SERVER_HOST = "127.0.0.1"
SERVER_PORT  = 8000
BASE_URL     = f"http://{SERVER_HOST}:{SERVER_PORT}"
WS_URL       = f"ws://{SERVER_HOST}:{SERVER_PORT}"

def update_server_urls(host_port_str: str):
    global BASE_URL, WS_URL, SERVER_HOST, SERVER_PORT
    host_port_str = host_port_str.strip()
    if not host_port_str:
        host_port_str = "127.0.0.1:8000"
    
    if ":" in host_port_str:
        parts = host_port_str.split(":", 1)
        host = parts[0]
        port = parts[1]
    else:
        host = host_port_str
        port = "8000"
        
    SERVER_HOST = host
    SERVER_PORT = int(port) if port.isdigit() else 8000
    BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
    WS_URL = f"ws://{SERVER_HOST}:{SERVER_PORT}"

# Dosya tipi → ikon eşlemesi
FILE_ICONS = {
    "image":    ft.Icons.IMAGE,
    "video":    ft.Icons.VIDEO_FILE,
    "audio":    ft.Icons.AUDIO_FILE,
    "document": ft.Icons.DESCRIPTION,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}


def _guess_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        if mime.startswith("video"): return "video"
        if mime.startswith("audio"): return "audio"
    return "document"


# ── VoIP WebRTC Track Yardımcı Sınıfları ─────────────────────────────

class MicrophoneTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.sample_rate = 48000
        self.channels = 1
        self.frame_size = 960
        self.enabled = True
        
        def callback(indata, frames, time_info, status):
            if status:
                print(f"[MicTrack] Status: {status}")
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, indata.copy())
            except Exception as e:
                pass
            
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            blocksize=self.frame_size,
            callback=callback
        )
        self.stream.start()

    async def recv(self):
        if self.stream is None:
            raise Exception("Track stopped")
            
        data = await self.queue.get()
        if not self.enabled:
            data = np.zeros_like(data)
            
        data_transposed = data.T
        frame = av.AudioFrame.from_ndarray(data_transposed, format='s16', layout='mono')
        frame.sample_rate = self.sample_rate
        if not hasattr(self, "_pts"):
            self._pts = 0
        frame.pts = self._pts
        frame.time_base = av.Fraction(1, self.sample_rate)
        self._pts += self.frame_size
        return frame

    def stop(self):
        if hasattr(self, "stream") and self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print("Error stopping sd.InputStream:", e)
            self.stream = None


class AudioPlayer:
    def __init__(self, track):
        self.track = track
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.sample_rate = 48000
        self.channels = 1
        self.frame_size = 960
        self.running = True
        self.play_task = None
        
        def callback(outdata, frames, time_info, status):
            if status:
                print(f"[AudioPlayer] Status: {status}")
            try:
                if not self.queue.empty():
                    data = self.queue.get_nowait()
                    outdata[:] = data
                else:
                    outdata.fill(0)
            except Exception as e:
                outdata.fill(0)
                
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            blocksize=self.frame_size,
            callback=callback
        )
        self.stream.start()

    def start(self):
        self.play_task = asyncio.create_task(self._run())

    async def _run(self):
        while self.running:
            try:
                frame = await self.track.recv()
                data = frame.to_ndarray().T
                await self.queue.put(data)
            except Exception as e:
                print("[AudioPlayer] Error receiving frame:", e)
                break

    def stop(self):
        self.running = False
        if self.play_task:
            self.play_task.cancel()
        if hasattr(self, "stream") and self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print("Error stopping sd.OutputStream:", e)
            self.stream = None


class CameraTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        self.running = True
        self.enabled = True
        self.last_frame_time = 0
        self.fps_interval = 1.0 / 15.0

    async def recv(self):
        now = time.time()
        elapsed = now - self.last_frame_time
        if elapsed < self.fps_interval:
            await asyncio.sleep(self.fps_interval - elapsed)
        
        if not self.running or not self.cap:
            raise Exception("Track stopped")
            
        ret, frame = self.cap.read()
        self.last_frame_time = time.time()
        
        if not self.enabled:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
        else:
            if not ret:
                img = np.zeros((480, 640, 3), dtype=np.uint8)
            else:
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
        v_frame = VideoFrame.from_ndarray(img, format='rgb24')
        if not hasattr(self, "_pts"):
            self._pts = 0
        v_frame.pts = self._pts
        v_frame.time_base = av.Fraction(1, 90000)
        self._pts += 6000
        return v_frame

    def stop(self):
        self.running = False
        if hasattr(self, "cap") and self.cap:
            try:
                self.cap.release()
            except Exception as e:
                print("Error releasing cv2.VideoCapture:", e)
            self.cap = None


def main(page: ft.Page):

    # ── Sayfa Ayarları ────────────────────────────────────────────────
    page.title       = "HybridP2P Messenger"
    page.theme_mode  = ft.ThemeMode.DARK
    page.window.width  = 480
    page.window.height = 820
    page.padding     = 0
    page.bgcolor     = "#09090b"
    page.theme       = ft.Theme(color_scheme_seed="#8b5cf6", font_family="Inter, sans-serif")

    # ── Uygulama Durumu ──────────────────────────────────────────────
    state = {
        "username":          None,
        "private_key":       None,
        "public_key":        None,
        "recipient":         None,
        "recipient_pub_key": None,
        "ws":                None,
        "ws_loop":           None,
        "store":             None,
        "ephemeral":         False,
        "view_once_mode":    False,   # per-mesaj view-once toggle
        "staged_file":       None,
        "logged_in":         False,
        "active_pc":         None,
        "active_call_id":    None,
        "call_role":         None,
        "call_type":         None,
        "call_state":        None,
        "call_partner":      None,
        "local_audio_track": None,
        "local_video_track": None,
        "audio_player":      None,
        "call_duration":     0,
        "remote_sdp":        None,
    }

    def run_on_ui(func, *args, **kwargs):
        async def _run():
            res = func(*args, **kwargs)
            if asyncio.iscoroutine(res):
                await res
        page.run_task(_run)

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     MESAJ BALONCULARI                          ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def create_message_bubble(sender: str, text: str, time_str: str, is_mine: bool, is_read: bool = True):
        bubble_color = "#8b5cf6" if is_mine else "#27272a"
        text_color   = "#ffffff" if is_mine else "#e0e0e0"
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        
        # Build timestamp row containing tick status icons for sender's messages
        time_row_controls = [
            ft.Text(time_str, size=10, color="#888888")
        ]
        if is_mine:
            tick_icon = ft.Icon(
                ft.Icons.DONE_ALL if is_read else ft.Icons.DONE,
                size=14,
                color="#22c55e" if is_read else "#71717a"
            )
            time_row_controls.append(tick_icon)

        time_row = ft.Row(
            controls=time_row_controls,
            spacing=4,
            alignment=ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START,
            tight=True
        )

        return ft.Row(
            alignment=align,
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(sender, size=11, color="#9e9e9e",
                                    weight=ft.FontWeight.BOLD, visible=not is_mine),
                            ft.Text(text, size=14, color=text_color, selectable=True),
                            time_row,
                        ],
                        spacing=2, tight=True,
                    ),
                    bgcolor=bubble_color,
                    padding=ft.Padding(14, 10, 14, 10),
                    border_radius=ft.BorderRadius(
                        top_left=14, top_right=14,
                        bottom_left=4 if is_mine else 14,
                        bottom_right=14 if is_mine else 4,
                    ),
                    width=300,
                    shadow=ft.BoxShadow(blur_radius=8, color="#00000033", offset=ft.Offset(0, 2)),
                    animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
                ),
            ],
        )

    def create_view_once_bubble(sender: str, time_str: str, is_mine: bool,
                                 encrypted_payload: str, plaintext_fallback: str = ""):
        """
        Tek görünümlü mesaj baloncuğu.
        Tıklanınca içerik diyalogda gösterilir, kapanınca silinir.
        """
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = "#8b5cf6" if is_mine else "#27272a"
        bubble_row = None

        def on_tap(e):
            nonlocal bubble_row
            if is_mine:
                plaintext = plaintext_fallback or "View-once message sent."
            else:
                try:
                    plaintext = decrypt_message(encrypted_payload, state["private_key"])
                except Exception as ex:
                    plaintext = f"[Cozme hatasi: {ex}]"

            content_text = ft.Text(plaintext, size=15, color="#ffffff",
                                   selectable=True, text_align=ft.TextAlign.CENTER)

            has_cleaned = False
            def clean_up():
                nonlocal has_cleaned
                if has_cleaned:
                    return
                has_cleaned = True
                try:
                    if bubble_row in chat_list.controls:
                        chat_list.controls.remove(bubble_row)
                except:
                    pass
                try:
                    page.overlay.remove(dialog)
                except:
                    pass
                page.update()

            def close_dialog(e):
                dialog.open = False
                page.update()
                clean_up()

            dialog = ft.AlertDialog(
                modal=False,  # Herhangi bir yere tıklayınca da kapansın
                content=ft.Column(
                    controls=[
                        # Header Row (interactive elements inside content to avoid click blocking in title)
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ef4444", size=20),
                                ft.Text("View-Once Message", size=14, color="#ef4444", weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color="#ef4444",
                                    icon_size=18,
                                    on_click=close_dialog,
                                    tooltip="Close",
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color="#ef444444", height=1),
                        ft.Container(height=10),
                        content_text,
                        ft.Container(height=12),
                        ft.Text("This message will be permanently deleted from the chat once closed.",
                                size=11, color="#ef4444", text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Close (Delete)", on_click=close_dialog)
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: clean_up(),
                bgcolor="#18181b",
            )
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

        bubble_row = ft.Row(
            alignment=align,
            controls=[
                ft.GestureDetector(
                    on_tap=on_tap,
                    content=ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ef4444", size=18),
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            "Sender" if not is_mine else "You",
                                            size=11, color="#9e9e9e", visible=not is_mine
                                        ),
                                        ft.Text("View-once message",
                                                size=13, color="#ef4444"),
                                        ft.Text("Tap to view",
                                                size=10, color="#888888"),
                                        ft.Text(time_str, size=9, color="#666666"),
                                    ],
                                    spacing=1, tight=True,
                                ),
                            ],
                            spacing=8,
                        ),
                        bgcolor=color,
                        padding=ft.Padding(14, 10, 14, 10),
                        border_radius=ft.BorderRadius(
                            top_left=14, top_right=14,
                        bottom_left=4 if is_mine else 14,
                        bottom_right=14 if is_mine else 4,
                        ),
                        border=ft.Border(left=ft.BorderSide(1, "#ef444444"), top=ft.BorderSide(1, "#ef444444"), right=ft.BorderSide(1, "#ef444444"), bottom=ft.BorderSide(1, "#ef444444")),
                        width=260,
                    ),
                ),
            ],
        )
        return bubble_row

    def create_file_bubble(sender: str, file_uuid: str, original_name: str,
                            file_type: str, time_str: str, is_mine: bool,
                            view_once: bool = False):
        """
        Dosya / resim mesaj baloncuğu.
        Resimler için indirme sonrası thumbnail gösterilir.
        """
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = "#8b5cf6" if is_mine else "#27272a"
        icon  = FILE_ICONS.get(file_type, ft.Icons.DESCRIPTION)
        bubble_row = None

        # İndirme durumu için durum göstergesi
        status_text = ft.Text("Download", size=11, color="#a78bfa")
        image_display = ft.Column(controls=[], visible=False)

        def show_view_once_dialog(content_control, message_text):
            nonlocal bubble_row
            
            has_cleaned = False
            def clean_up():
                nonlocal has_cleaned
                if has_cleaned:
                    return
                has_cleaned = True
                try:
                    if bubble_row in chat_list.controls:
                        chat_list.controls.remove(bubble_row)
                except:
                    pass
                try:
                    page.overlay.remove(dialog)
                except:
                    pass
                page.update()

            def close_dialog(e):
                dialog.open = False
                page.update()
                clean_up()

            dialog = ft.AlertDialog(
                modal=False,  # Herhangi bir yere tıklayınca da kapansın
                content=ft.Column(
                    controls=[
                        # Header Row
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ef4444", size=20),
                                ft.Text("View-Once File", size=14, color="#ef4444", weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color="#ef4444",
                                    icon_size=18,
                                    on_click=close_dialog,
                                    tooltip="Close",
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color="#ef444444", height=1),
                        ft.Container(height=10),
                        content_control,
                        ft.Container(height=12),
                        ft.Text(message_text,
                                size=11, color="#ef4444", text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Close (Delete)", on_click=close_dialog)
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: clean_up(),
                bgcolor="#18181b",
            )
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

        def on_download(e):
            if view_once and is_mine:
                show_view_once_dialog(
                    ft.Text(f"Gonderdiginiz dosya: {original_name}", size=14, color="#ffffff"),
                    "This message will be permanently deleted from the chat once closed."
                )
                return

            status_text.value = "Downloading..."
            page.update()

            def do_download():
                try:
                    resp = signed_get(f"/api/download_file/{file_uuid}", timeout=30)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = decrypt_bytes(data["encrypted_data"], state["private_key"])

                        if view_once:
                            if file_type == "image":
                                b64 = base64.b64encode(raw).decode("ascii")
                                ext = Path(original_name).suffix.lstrip(".")
                                data_url = f"data:image/{ext or 'png'};base64,{b64}"
                                img = ft.Image(
                                    src=data_url,
                                    width=300, height=250,
                                    fit="contain",
                                    border_radius=8,
                                )
                                run_on_ui(show_view_once_dialog, img, "This file will be permanently deleted from the chat once closed.")
                            else:
                                downloads = Path.home() / "Downloads"
                                downloads.mkdir(exist_ok=True)
                                dest = downloads / original_name
                                dest.write_bytes(raw)
                                def _show_file_vo():
                                    show_view_once_dialog(
                                        ft.Text(f"Dosya indirildi ve kaydedildi:\n{dest.name}", size=13, color="#ffffff", text_align=ft.TextAlign.CENTER),
                                        "This file has been saved to your local Downloads folder. It will be permanently deleted from the chat once closed."
                                    )
                                run_on_ui(_show_file_vo)
                        else:
                            if file_type == "image":
                                b64 = base64.b64encode(raw).decode("ascii")
                                ext = Path(original_name).suffix.lstrip(".")
                                data_url = f"data:image/{ext or 'png'};base64,{b64}"
                                img = ft.Image(
                                    src=data_url,
                                    width=250, height=200,
                                    fit="contain",
                                    border_radius=8,
                                )
                                def _show_normal_img():
                                    image_display.controls.clear()
                                    image_display.controls.append(img)
                                    image_display.visible = True
                                    status_text.value = original_name
                                    page.update()
                                run_on_ui(_show_normal_img)
                            else:
                                downloads = Path.home() / "Downloads"
                                downloads.mkdir(exist_ok=True)
                                dest = downloads / original_name
                                dest.write_bytes(raw)
                                def _show_normal_file():
                                    status_text.value = f"Kaydedildi: {dest.name}"
                                    page.update()
                                run_on_ui(_show_normal_file)
                    else:
                        def _failed():
                            status_text.value = "Download failed or already downloaded."
                            page.update()
                        run_on_ui(_failed)
                except Exception as ex:
                    def _err():
                        status_text.value = f"Hata: {ex}"
                        page.update()
                    run_on_ui(_err)

            threading.Thread(target=do_download, daemon=True).start()

        vo_badge = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VISIBILITY, size=10, color="#ef4444"),
                    ft.Text("View-once", size=9, color="#ef4444"),
                ],
                spacing=2,
            ),
            visible=view_once,
        )

        bubble_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(sender, size=11, color="#9e9e9e",
                            weight=ft.FontWeight.BOLD, visible=not is_mine),
                    vo_badge,
                    ft.Row(
                        controls=[
                            ft.Icon(icon, size=24, color="#a78bfa"),
                            ft.Column(
                                controls=[
                                    ft.Text(original_name, size=12,
                                            color="#ffffff", max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS),
                                    status_text,
                                ],
                                spacing=1, tight=True, expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DOWNLOAD if not view_once else ft.Icons.VISIBILITY,
                                icon_color="#ef4444" if view_once else "#a78bfa",
                                icon_size=18,
                                on_click=on_download,
                                tooltip="View" if view_once else "Download & Decrypt",
                            ),
                        ],
                        spacing=6,
                    ),
                    image_display,
                    ft.Text(time_str, size=10, color="#888888"),
                ],
                spacing=4, tight=True,
            ),
            bgcolor=color,
            padding=ft.Padding(14, 10, 14, 10),
            border_radius=ft.BorderRadius(
                top_left=14, top_right=14,
                        bottom_left=4 if is_mine else 14,
                        bottom_right=14 if is_mine else 4,
            ),
            border=ft.Border(left=ft.BorderSide(1, "#ef444444"), top=ft.BorderSide(1, "#ef444444"), right=ft.BorderSide(1, "#ef444444"), bottom=ft.BorderSide(1, "#ef444444")) if view_once else None,
            width=300,
            shadow=ft.BoxShadow(blur_radius=8, color="#00000033",
                                offset=ft.Offset(0, 2)),
        )

        if view_once:
            bubble_row = ft.Row(
                alignment=align,
                controls=[
                    ft.GestureDetector(
                        on_tap=on_download,
                        content=bubble_content,
                    )
                ]
            )
        else:
            bubble_row = ft.Row(
                alignment=align,
                controls=[
                    bubble_content
                ]
            )
        return bubble_row

    def create_system_bubble(text: str):
        return ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Text(text, size=11, color="#aaaaaa",
                                    text_align=ft.TextAlign.CENTER),
                    bgcolor="#27272a",
                    padding=ft.Padding(16, 6, 16, 6),
                    border_radius=8,
                    border=ft.Border(left=ft.BorderSide(1, "#3f3f46"), top=ft.BorderSide(1, "#3f3f46"), right=ft.BorderSide(1, "#3f3f46"), bottom=ft.BorderSide(1, "#3f3f46")),
                ),
            ],
        )

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     ANAHTAR YÖNETİMİ                           ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def initialize_keys(username: str):
        priv, pub = load_keys_from_disk(username)
        if priv and pub:
            log_status("Existing keys loaded.")
            return priv, pub
        log_status("Generating RSA-4096 keys...")
        page.update()
        priv, pub = generate_rsa_keypair()
        save_keys_to_disk(username, priv, pub)
        log_status("Keys generated.")
        return priv, pub

    def _canonical_json(payload: dict) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def make_auth_headers(username: str, private_key, method: str, path: str, body_text: str = "") -> dict:
        if not username or not private_key:
            return {}
        from datetime import datetime, timezone
        import base64
        import hashlib
        from crypto_utils import sign_data
        
        timestamp = datetime.now(timezone.utc).isoformat()
        body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        data_to_sign = "\n".join([
            username,
            timestamp,
            method.upper(),
            path,
            body_hash,
        ]).encode("utf-8")
        sig = sign_data(private_key, data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")
        
        return {
            "X-Username": username,
            "X-Timestamp": timestamp,
            "X-Signature": sig_b64
        }

    def signed_get(path: str, timeout: int = 5):
        headers = make_auth_headers(state["username"], state["private_key"], "GET", path)
        return requests.get(f"{BASE_URL}{path}", headers=headers, timeout=timeout)

    def signed_delete(path: str, timeout: int = 5):
        headers = make_auth_headers(state["username"], state["private_key"], "DELETE", path)
        return requests.delete(f"{BASE_URL}{path}", headers=headers, timeout=timeout)

    def signed_post(path: str, payload: dict, timeout: int = 5):
        body_text = _canonical_json(payload)
        headers = make_auth_headers(state["username"], state["private_key"], "POST", path, body_text)
        headers["Content-Type"] = "application/json"
        return requests.post(f"{BASE_URL}{path}", data=body_text.encode("utf-8"), headers=headers, timeout=timeout)

    def register_with_server(username: str, public_key, private_key) -> bool:
        from datetime import datetime, timezone
        import base64
        from crypto_utils import sign_data
        
        pem_key = public_key_to_pem_string(public_key)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        data_to_sign = f"{username}:{timestamp}:{pem_key}".encode("utf-8")
        sig = sign_data(private_key, data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        try:
            resp = requests.post(f"{BASE_URL}/api/register", json={
                "username": username,
                "public_key": pem_key,
                "timestamp": timestamp,
                "signature": sig_b64
            }, timeout=5)
        except requests.exceptions.Timeout:
            raise Exception("Server request timed out after 5 seconds.")
        except requests.exceptions.ConnectionError as conn_err:
            raise Exception(f"Server did not respond (offline or connection refused): {conn_err}")
        except Exception as ex:
            raise Exception(f"Network error: {ex}")

        if resp.status_code == 200:
            return True
            
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
            
        raise Exception(f"Server rejected registration ({resp.status_code}): {detail}")

    def fetch_recipient_pub_key(recipient: str):
        try:
            resp = signed_get(f"/api/public_key/{recipient}", timeout=5)
            if resp.status_code == 200:
                return pem_string_to_public_key(resp.json()["public_key"])
        except: pass
        return None

    def sync_chat_settings():
        if not state["username"]: return
        try:
            resp = signed_get(f"/api/chat_settings/{state['username']}", timeout=5)
            if resp.status_code == 200:
                for s in resp.json().get("settings", []):
                    parts   = s["chat_id"].split("_", 1)
                    partner = parts[0] if len(parts) > 1 and parts[1] == state["username"] else parts[-1]
                    if state["store"]:
                        state["store"].set_ephemeral(partner, s["ephemeral"], s.get("changed_by"))
                    if partner == state["recipient"]:
                        state["ephemeral"] = s["ephemeral"]
                        _update_ephemeral_ui()
        except: pass

    def fetch_offline_messages():
        print(f"[REST] Sunucudan cevrimdisi mesajlar talep ediliyor ({state['username']})...")
        try:
            resp = signed_get(f"/api/fetch_messages/{state['username']}", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data["count"]:
                    log_status(f"{data['count']} cevrimdisi mesaj alindi.")
                    print(f"[REST] {data['count']} yeni cevrimdisi mesaj alindi.")
                else:
                    print("[REST] Cevrimdisi mesaj yok.")
                for msg in data["messages"]:
                    try:
                        pt = decrypt_message(msg["encrypted_payload"], state["private_key"])
                        print(f"[REST] '{msg['sender']}' kullanicisindan gelen cevrimdisi mesaj basariyla cozuldu.")
                        _on_incoming_message(msg["sender"], pt, msg.get("timestamp", ""),
                                             bool(msg.get("view_once", False)),
                                             msg["encrypted_payload"])
                    except Exception as ex:
                        print(f"[REST] Mesaj cozme hatasi: {ex}")
                        _on_incoming_message(msg["sender"], f"[Hata: {ex}]",
                                             msg.get("timestamp", ""))
        except Exception as e:
            log_status("Failed to fetch offline messages.")
            print(f"[REST] Cevrimdisi mesajlar sunucudan cekilemedi: {e}")

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     CHAT LİSTESİ YÖNETİMİ                     ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def _fmt_time(ts: str) -> str:
        try:
            ts_norm = ts.replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(ts_norm)
            if dt.tzinfo is None:
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%H:%M")
        except: return ts[:5] if ts else ""

    def add_message_to_chat(sender: str, text: str, is_mine: bool,
                             time_str: str = "", save: bool = True,
                             view_once: bool = False, encrypted_payload: str = "",
                             is_read: bool = True):
        from datetime import timezone
        if not time_str:
            time_str = datetime.now(timezone.utc).isoformat()
        
        raw_ts = time_str
        display_ts = _fmt_time(time_str)

        if view_once:
            bubble = create_view_once_bubble(sender, display_ts, is_mine, encrypted_payload, plaintext_fallback=text)
        else:
            bubble = create_message_bubble(sender, text, display_ts, is_mine, is_read=is_read)

        chat_list.controls.append(bubble)

        if save and state["recipient"] and state["store"] and not view_once:
            state["store"].save_message(
                partner=state["recipient"], sender=sender,
                content=text, is_mine=is_mine, timestamp=raw_ts, is_read=(0 if is_mine else 1)
            )
        try: page.update()
        except: pass

    def add_file_to_chat(sender: str, file_uuid: str, original_name: str,
                          file_type: str, is_mine: bool, time_str: str = "",
                          view_once: bool = False):
        from datetime import timezone
        if not time_str:
            time_str = datetime.now(timezone.utc).isoformat()
        time_str = _fmt_time(time_str)

        bubble = create_file_bubble(sender, file_uuid, original_name,
                                     file_type, time_str, is_mine, view_once)
        chat_list.controls.append(bubble)
        try: page.update()
        except: pass

    def add_system_event(text: str, partner: str = None):
        chat_list.controls.append(create_system_bubble(text))
        if partner and state["store"] and not state["ephemeral"]:
            state["store"].save_system_event(partner, text)
        try: page.update()
        except: pass

    def _on_incoming_message(sender: str, plaintext: str, timestamp: str = "",
                              view_once: bool = False, encrypted_payload: str = ""):
        def _update():
            if state["recipient"] and sender == state["recipient"]:
                add_message_to_chat(sender, plaintext, is_mine=False,
                                     time_str=timestamp, save=True,
                                     view_once=view_once,
                                     encrypted_payload=encrypted_payload)
                send_read_receipt(sender, timestamp)
            else:
                if state["store"] and not view_once:
                    state["store"].save_message(
                        partner=sender, sender=sender,
                        content=plaintext, is_mine=False, timestamp=timestamp,
                        is_read=0
                    )
                log_status(f"'{sender}' adlisindan yeni mesaj var!")
            load_inbox_chats()
        run_on_ui(_update)

    def _on_incoming_file(sender: str, file_uuid: str, original_name: str,
                           file_type: str, timestamp: str, view_once: bool):
        def _update():
            if state["recipient"] and sender == state["recipient"]:
                add_file_to_chat(sender, file_uuid, original_name,
                                  file_type, is_mine=False, time_str=timestamp,
                                  view_once=view_once)
            else:
                log_status(f"'{sender}' adlisindan dosya var! ({original_name})")
            load_inbox_chats()
        run_on_ui(_update)

    def load_history_to_chat():
        if not state["recipient"] or not state["store"]: return
        chat_list.controls.clear()
        for m in state["store"].get_messages(state["recipient"]):
            if m["msg_type"] == "system":
                chat_list.controls.append(create_system_bubble(m["content"]))
            else:
                ts = _fmt_time(m["timestamp"])
                is_read_val = bool(m.get("is_read", 1))
                chat_list.controls.append(
                    create_message_bubble(m["sender"], m["content"], ts, bool(m["is_mine"]), is_read=is_read_val)
                )
        try: page.update()
        except: pass

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                   EPHEMERAL MOD KONTROLÜ                       ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def toggle_ephemeral(e):
        if not state["recipient"]:
            log_status("Please select a recipient first!")
            return
        new_val = not state["ephemeral"]
        state["ephemeral"] = new_val
        state["store"].set_ephemeral(state["recipient"], new_val, state["username"])
        _update_ephemeral_ui()
        
        send_ws_message_with_fallback({
            "type": "ephemeral_toggle",
            "sender": state["username"],
            "recipient": state["recipient"],
            "ephemeral": new_val,
        })

        label = ("Ephemeral mode ON — messages are not saved"
                 if new_val else "Message history ON — messages are saved")
        add_system_event(label, partner=state["recipient"])

    def _update_ephemeral_ui():
        if state["ephemeral"]:
            ephemeral_btn.icon       = ft.Icons.VISIBILITY_OFF
            ephemeral_btn.icon_color = "#ef4444"
            ephemeral_btn.tooltip    = "Ephemeral mode ON — disable"
        else:
            ephemeral_btn.icon       = ft.Icons.VISIBILITY
            ephemeral_btn.icon_color = "#8b5cf6"
            ephemeral_btn.tooltip    = "Switch to Ephemeral Chat"
        try: page.update()
        except: pass

    def copy_public_key(e):
        try:
            pem_str = public_key_to_pem_string(state["public_key"])
            fingerprint = get_public_key_fingerprint(state["public_key"])
            card = {
                "username": state["username"],
                "public_key": pem_str,
                "fingerprint": fingerprint
            }
            copy_to_clipboard(json.dumps(card, indent=2))
            log_status("Contact card copied to clipboard!")
        except Exception as ex:
            log_status(f"Kopyalama hatasi: {ex}")

    def _on_ephemeral_toggle_received(sender: str, ephemeral: bool, ts: str):
        if state["store"]:
            state["store"].set_ephemeral(sender, ephemeral, sender)
        if state["recipient"] == sender:
            state["ephemeral"] = ephemeral
            _update_ephemeral_ui()
            label = (f"{sender} gecici modu ACTI — kayit durdu"
                     if ephemeral else f"{sender} kayit modunu ACTI")
            add_system_event(label, partner=sender)

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║             VIEW-ONCE MESAJ TOGGLE (per-mesaj)                 ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def toggle_view_once_msg(e):
        """Mesaj bazında tek görünümlü toggle."""
        state["view_once_mode"] = not state["view_once_mode"]
        if state["view_once_mode"]:
            view_once_msg_btn.icon       = ft.Icons.VISIBILITY_OFF
            view_once_msg_btn.icon_color = "#ef4444"
            view_once_msg_btn.tooltip    = "View-once ON — disable"
        else:
            view_once_msg_btn.icon       = ft.Icons.VISIBILITY
            view_once_msg_btn.icon_color = "#888888"
            view_once_msg_btn.tooltip    = "Send as view-once"
        page.update()

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                DOSYA / RESİM GÖNDERİMİ                        ║
    # ╚═══════════════════════════════════════════════════════════════╝

    async def on_attach_click(e):
        if not state["recipient"]:
            log_status("Please connect to a recipient or group first!")
            return
        files = await file_picker.pick_files(allow_multiple=False)
        if not files: return
        f = files[0]
        
        # Check size limit
        try:
            sz = os.path.getsize(f.path)
            if sz > 10 * 1024 * 1024:  # 10 MB limit
                log_status("File too large! Max 10 MB.")
                return
        except:
            pass

        file_type = _guess_file_type(f.name)
        
        # Stage the file in state
        state["staged_file"] = {
            "name": f.name,
            "path": f.path,
            "type": file_type,
        }
        
        staged_file_name_text.value = f.name
        staged_file_container.visible = True
        page.update()
        log_status(f"Staged file: '{f.name}'. Press send button to encrypt & upload.")

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                   WEBSOCKET İLETİŞİMİ                          ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def start_websocket_listener():
        threading.Thread(target=_run_ws_loop, daemon=True, name="ws-listener").start()
        log_status("Establishing WebSocket connection...")

    def _run_ws_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ws_listen())

    async def _ws_listen():
        import websockets
        state["ws_loop"] = asyncio.get_running_loop()
        reconnect_delay = 2
        while state.get("logged_in", False):
            try:
                async with websockets.connect(f"{WS_URL}/ws/{state['username']}") as ws:
                    if not state.get("logged_in", False):
                        break
                    # Challenge-Response Handshake:
                    # 1. Receive challenge nonce
                    challenge_raw = await ws.recv()
                    challenge_msg = json.loads(challenge_raw)
                    if challenge_msg.get("type") != "challenge":
                        raise Exception("Handshake error: No challenge received from server.")
                        
                    challenge = challenge_msg["challenge"]
                    
                    # 2. Sign challenge using private key
                    from crypto_utils import sign_data
                    sig = sign_data(state["private_key"], challenge.encode("utf-8"))
                    sig_b64 = base64.b64encode(sig).decode("ascii")
                    
                    # 3. Send signature back to server
                    await ws.send(json.dumps({
                        "type": "auth",
                        "signature": sig_b64
                    }))
                    
                    # 4. Receive auth result
                    auth_res_raw = await ws.recv()
                    auth_res = json.loads(auth_res_raw)
                    if auth_res.get("type") != "auth_result" or auth_res.get("status") != "success":
                        err_msg = auth_res.get("message", "Authentication failed.")
                        log_status(f"Kimlik doğrulama hatası: {err_msg}")
                        raise Exception(f"Kimlik doğrulama hatası: {err_msg}")
                        
                    state["ws"] = ws
                    log_status("Connection established.")
                    update_connection_status(True)
                    run_on_ui(page.update)
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            t    = data.get("type", "")

                            if t == "message":
                                sender    = data.get("sender", "?")
                                enc       = data.get("encrypted_payload", "")
                                ts        = data.get("timestamp", "")
                                vo        = bool(data.get("view_once", False))
                                try:
                                    pt = decrypt_message(enc, state["private_key"])
                                    _on_incoming_message(sender, pt, ts, vo, enc)
                                except Exception as ex:
                                    _on_incoming_message(sender, f"[Hata:{ex}]", ts)

                            elif t == "file_message":
                                _on_incoming_file(
                                    sender=data.get("sender","?"),
                                    file_uuid=data.get("file_uuid",""),
                                    original_name=data.get("original_name","dosya"),
                                    file_type=data.get("file_type","document"),
                                    timestamp=data.get("timestamp",""),
                                    view_once=bool(data.get("view_once", False)),
                                )

                            elif t == "ephemeral_toggle":
                                run_on_ui(
                                    _on_ephemeral_toggle_received,
                                    data.get("sender","?"),
                                    bool(data.get("ephemeral", False)),
                                    data.get("timestamp",""),
                                )

                            elif t == "group_key_dist":
                                sender = data.get("sender", "?")
                                group_id = data.get("group_id", "")
                                enc = data.get("encrypted_payload", "")
                                try:
                                    group_key_hex = decrypt_message(enc, state["private_key"])
                                    state["store"].save_group_key(group_id, group_key_hex)
                                    log_status(f"'{sender}' sizi gruba ekledi. Anahtar alindi.")
                                    sync_user_groups_from_server()
                                except Exception as ex:
                                    print(f"Grup anahtari cozme hatasi: {ex}")

                            elif t == "group_message":
                                sender = data.get("sender", "?")
                                group_id = data.get("group_id", "")
                                enc = data.get("encrypted_payload", "")
                                ts = data.get("timestamp", "")
                                sig_b64 = data.get("signature", "")
                                is_active = (state["recipient"] == group_id)
                                
                                # Grup Taklit Koruması (İmza Doğrulama)
                                from crypto_utils import verify_signature
                                verified = False
                                if sig_b64:
                                    local_contact = state["store"].get_contact(sender)
                                    if local_contact:
                                        pub_key = pem_string_to_public_key(local_contact["public_key"])
                                    else:
                                        pub_key = fetch_recipient_pub_key(sender)
                                        if pub_key:
                                            pub_key_pem = public_key_to_pem_string(pub_key)
                                            fingerprint = get_public_key_fingerprint(pub_key)
                                            state["store"].save_contact(sender, pub_key_pem, fingerprint)
                                    
                                    if pub_key:
                                        try:
                                            sig_bytes = base64.b64decode(sig_b64)
                                            data_to_verify = f"{sender}:{group_id}:{enc}".encode("utf-8")
                                            verified = verify_signature(pub_key, sig_bytes, data_to_verify)
                                        except Exception as sig_ex:
                                            print(f"Grup imza dogrulama hatasi: {sig_ex}")
                                
                                if not verified:
                                    print(f"HATA: '{sender}' kullanicisinin grup imza dogrulamasi basarisiz!")
                                    if state["recipient"] == group_id:
                                        run_on_ui(add_system_event, f"UYARI: '{sender}' adli kullanicinin kimligi dogrulanamadi (Taklit Tesebbusu)!")
                                    continue

                                group_key = state["store"].get_group_key(group_id)
                                if group_key:
                                    try:
                                        pt = decrypt_symmetric(enc, group_key)
                                        state["store"].save_message(
                                            partner=group_id,
                                            sender=sender,
                                            content=pt,
                                            is_mine=False,
                                            timestamp=ts,
                                            is_read=(1 if is_active else 0)
                                        )
                                        if state["recipient"] == group_id:
                                            def _group_ui():
                                                add_message_to_chat(
                                                    sender=sender,
                                                    text=pt,
                                                    is_mine=False,
                                                    time_str=ts,
                                                    save=False
                                                )
                                                load_inbox_chats()
                                            run_on_ui(_group_ui)
                                        else:
                                            chat_info = state["store"].get_chat_info(group_id)
                                            gname = chat_info.get("partner", group_id)
                                            log_status(f"Grup '{gname}'dan yeni mesaj!")
                                            run_on_ui(load_inbox_chats)
                                    except Exception as ex:
                                        print(f"Grup mesaji cozme hatasi: {ex}")

                            elif t == "delivery_ack":
                                s = data.get("status","")
                                r = data.get("recipient","")
                                if s == "delivered_online":
                                    log_status(f"'{r}' adlisina iletildi.")
                                elif s == "stored_offline":
                                    log_status(f"'{r}' cevrimdisi. Mesaj saklandı.")

                            elif t == "read_receipt":
                                sender = data.get("sender", "?")
                                ts = data.get("timestamp", "")
                                if state["store"]:
                                    state["store"].mark_sent_messages_as_read(sender, ts)
                                if state["recipient"] == sender:
                                    run_on_ui(load_history_to_chat)

                            elif t == "call_offer":
                                caller = data.get("caller", "?")
                                cid = data.get("call_id", "")
                                ctype = data.get("call_type", "audio")
                                sdp = data.get("sdp_offer", "")
                                if state.get("active_call_id") is not None:
                                    await ws.send(json.dumps({
                                        "type": "call_reject",
                                        "recipient": caller,
                                        "call_id": cid,
                                        "reason": "busy"
                                    }))
                                else:
                                    state["active_call_id"] = cid
                                    state["call_role"] = "callee"
                                    state["call_partner"] = caller
                                    state["call_type"] = ctype
                                    state["call_state"] = "ringing"
                                    state["remote_sdp"] = sdp
                                    run_on_ui(show_call_screen)

                            elif t == "call_answer":
                                callee = data.get("callee", "?")
                                cid = data.get("call_id", "")
                                sdp = data.get("sdp_answer", "")
                                if state.get("active_call_id") == cid and state.get("call_role") == "caller":
                                    pc = state.get("active_pc")
                                    if pc:
                                        try:
                                            await pc.setRemoteDescription(RTCSessionDescription(
                                                sdp=sdp,
                                                type="answer"
                                            ))
                                        except Exception as ex:
                                            print("Error setting remote answer description:", ex)
                                            cleanup_call()

                            elif t == "call_reject":
                                cid = data.get("call_id", "")
                                reason = data.get("reason", "rejected")
                                if state.get("active_call_id") == cid:
                                    log_status(f"Arama reddedildi ({reason})")
                                    cleanup_call()

                            elif t == "call_end":
                                cid = data.get("call_id", "")
                                if state.get("active_call_id") == cid:
                                    log_status("Arama sonlandırıldı.")
                                    cleanup_call()

                            elif t == "ice_candidate":
                                cid = data.get("call_id", "")
                                if state.get("active_call_id") == cid:
                                    pc = state.get("active_pc")
                                    if pc:
                                        try:
                                            await pc.addIceCandidate(RTCIceCandidate(
                                                sdpMid=data.get("sdp_mid"),
                                                sdpMLineIndex=data.get("sdp_mline_index"),
                                                candidate=data.get("candidate")
                                            ))
                                        except Exception as ex:
                                            print("Error adding ice candidate:", ex)

                        except json.JSONDecodeError: pass
            except Exception as ex:
                import websockets
                if isinstance(ex, (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError)):
                    print(f"[WS Connection Status] Baglanti kapandi/koptu (URL: {WS_URL}/ws/{state.get('username')}): {ex}")
                else:
                    print(f"[WS Unexpected Error] Beklenmedik hata: {ex}")
                    import traceback
                    traceback.print_exc()
                state["ws"] = None
                if not state.get("logged_in", False):
                    break
                log_status(f"WS disconnected. Reconnecting in {reconnect_delay}s...")
                update_connection_status(False)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def is_ws_connected() -> bool:
        ws = state.get("ws")
        loop = state.get("ws_loop")
        ws_not_none = ws is not None
        loop_not_none = loop is not None
        loop_running = loop.is_running() if loop_not_none else False
        
        ws_open = False
        if ws_not_none:
            # websockets v14+ deprecated and removed the .open property.
            # We check if ws.state is websockets.protocol.State.OPEN.
            try:
                from websockets.protocol import State
                ws_open = (getattr(ws, "state", None) == State.OPEN)
            except Exception:
                ws_open = getattr(ws, "open", False)
                
        return ws_not_none and loop_not_none and loop_running and ws_open

    def _ws_send_raw(raw_json: str):
        ws = state.get("ws")
        loop = state.get("ws_loop")
        if is_ws_connected():
            print("[WS] Mesaj sunucuya WebSocket uzerinden gonderiliyor...")
            asyncio.run_coroutine_threadsafe(ws.send(raw_json), loop)
        else:
            print("[WS] HATA: WebSocket baglantisi aktif degil!")

    def send_ws_message_with_fallback(msg_dict: dict):
        raw_json = json.dumps(msg_dict)
        if is_ws_connected():
            _ws_send_raw(raw_json)
        else:
            t = msg_dict.get("type", "")
            print(f"[REST] WebSocket kapali. Mesaj '{t}' REST API ile gonderiliyor...")
            try:
                def do_rest():
                    try:
                        r = signed_post("/api/send_ws_fallback", {"payload": raw_json}, timeout=5)
                        if r.status_code == 200:
                            print(f"[REST] Fallback ile '{t}' basariyla gonderildi.")
                            if t == "message":
                                log_status("Message delivered via REST.")
                        else:
                            print(f"[REST] HATA: Fallback basarisiz ({r.status_code}): {r.text}")
                            if t == "message":
                                log_status("Message could not be delivered!")
                    except Exception as ex:
                        print(f"[REST] HATA: Fallback baglanti hatasi: {ex}")
                        if t == "message":
                            log_status("Message could not be delivered!")
                threading.Thread(target=do_rest, daemon=True).start()
            except Exception as ex:
                print(f"[REST] Thread baslatma hatasi: {ex}")

    def send_message_via_ws(recipient: str, encrypted_payload: str, view_once: bool, timestamp: str = None):
        from datetime import timezone
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
        msg = {
            "type":              "message",
            "sender":            state["username"],
            "recipient":         recipient,
            "encrypted_payload": encrypted_payload,
            "view_once":         view_once,
            "timestamp":         timestamp,
        }
        send_ws_message_with_fallback(msg)

    def send_group_message_via_ws(group_id: str, encrypted_payload: str, timestamp: str = None):
        from datetime import timezone
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
        from crypto_utils import sign_data
        data_to_sign = f"{state['username']}:{group_id}:{encrypted_payload}".encode("utf-8")
        sig = sign_data(state["private_key"], data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        msg = {
            "type":              "group_message",
            "sender":            state["username"],
            "group_id":          group_id,
            "encrypted_payload": encrypted_payload,
            "signature":         sig_b64,
            "timestamp":         timestamp,
        }
        send_ws_message_with_fallback(msg)

    def sync_user_groups_from_server():
        if not state["username"] or not state["store"]: return
        try:
            resp = signed_get(f"/api/groups/{state['username']}", timeout=5)
            if resp.status_code == 200:
                groups = resp.json().get("groups", [])
                for g in groups:
                    state["store"].get_or_create_group_chat(g["group_id"], g["group_name"])
        except Exception as ex:
            print(f"Grup senkronizasyon hatasi: {ex}")

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     UI BİLEŞENLERİ                             ║
    # ╚═══════════════════════════════════════════════════════════════╝

    status_text = ft.Text("Welcome!", size=11, color="#9e9e9e",
                           max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

    def log_status(msg: str):
        def _update():
            status_text.value = msg
            try: page.update()
            except: pass
        run_on_ui(_update)

    def copy_to_clipboard(text: str):
        async def do_copy():
            try:
                await ft.Clipboard().set(text)
            except Exception as ex:
                print(f"[Clipboard Error] {ex}")
        page.run_task(do_copy)

    def send_read_receipt(recipient: str, timestamp: str):
        if state.get("is_group", False) or not recipient or not timestamp:
            return
        send_ws_message_with_fallback({
            "type": "read_receipt",
            "recipient": recipient,
            "timestamp": timestamp
        })

    chat_list = ft.ListView(expand=True, spacing=8,
                             padding=ft.Padding(12, 8, 12, 8),
                             auto_scroll=True)

    # Ephemeral toggle (chat seviyesi)
    ephemeral_btn = ft.IconButton(
        icon=ft.Icons.VISIBILITY, icon_color="#8b5cf6", icon_size=20,
        tooltip="Switch to Ephemeral Chat", on_click=toggle_ephemeral,
    )

    # VoIP call icon buttons
    call_icon_btn = ft.IconButton(
        icon=ft.Icons.CALL, icon_color="#8b5cf6", icon_size=20,
        tooltip="Voice Call (E2EE)", on_click=lambda e: start_voip_call(video=False),
        visible=False
    )
    video_call_icon_btn = ft.IconButton(
        icon=ft.Icons.VIDEOCAM, icon_color="#8b5cf6", icon_size=20,
        tooltip="Video Call (E2EE)", on_click=lambda e: start_voip_call(video=True),
        visible=False
    )

    # View-once toggle (mesaj seviyesi — input yanında)
    view_once_msg_btn = ft.IconButton(
        icon=ft.Icons.VISIBILITY, icon_color="#888888", icon_size=18,
        tooltip="Send as view-once", on_click=toggle_view_once_msg,
    )

    # Dosya ekleme butonu
    attach_btn = ft.IconButton(
        icon=ft.Icons.ATTACH_FILE, icon_color="#888888", icon_size=20,
        tooltip="Send File / Image", on_click=on_attach_click,
    )

    # Dosya seçici
    file_picker = ft.FilePicker()
    # page.overlay.append(file_picker)  # Flet 0.23+ treats this as a Service, appending causes Unknown Control

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     GİRİŞ EKRANI                               ║
    # ╚═══════════════════════════════════════════════════════════════╝

    server_address_field = ft.TextField(
        label="Server Address", value="127.0.0.1:8000",
        hint_text="Example: 127.0.0.1:8000 or server.com:8000",
        prefix_icon=ft.Icons.COMPUTER,
        border_color="#8b5cf6", focused_border_color="#a78bfa",
        cursor_color="#8b5cf6", text_size=15, height=55,
    )

    username_field = ft.TextField(
        label="Username", hint_text="Example: alice",
        prefix_icon=ft.Icons.PERSON,
        border_color="#8b5cf6", focused_border_color="#a78bfa",
        cursor_color="#8b5cf6", text_size=15, height=55,
    )

    import_key_checkbox = ft.Checkbox(
        label="Import existing Private Key (.pem)",
        value=False,
        on_change=lambda e: on_import_key_change(e),
    )

    import_key_field = ft.TextField(
        label="Private Key PEM",
        hint_text="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
        multiline=True,
        min_lines=3,
        max_lines=6,
        visible=False,
        border_color="#8b5cf6",
        focused_border_color="#a78bfa",
        cursor_color="#8b5cf6",
        text_size=12,
    )

    def on_import_key_change(e):
        import_key_field.visible = import_key_checkbox.value
        page.update()

    login_btn = ft.Button(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.LOGIN, size=20),
                ft.Text("Sign In", size=15, weight=ft.FontWeight.BOLD),
            ],
            alignment=ft.MainAxisAlignment.CENTER, spacing=8,
        ),
        on_click=lambda e: on_login_click(e),
        style=ft.ButtonStyle(
            bgcolor="#8b5cf6", color="#ffffff",
            padding=ft.Padding(32, 16, 32, 16),
            shape=ft.RoundedRectangleBorder(radius=8),
            elevation=4,
        ),
        width=280, height=52,
    )

    def on_login_click(e):
        username = username_field.value.strip().lower()
        if not username or len(username) < 2:
            username_field.error_text = "At least 2 characters required!"
            page.update()
            return
        username_field.error_text = None
        
        # update server URLs
        server_addr = server_address_field.value.strip()
        update_server_urls(server_addr)
        
        # Disable button and update text
        login_btn.disabled = True
        login_btn.content.controls[1].value = "Please wait..."
        log_status("Signing in...")
        page.update()

        def do_login():
            try:
                state["username"] = username
                if import_key_checkbox.value:
                    imported_pem = import_key_field.value.strip()
                    if not imported_pem:
                        def _fail_empty_key():
                            login_btn.disabled = False
                            login_btn.content.controls[1].value = "Sign In"
                            username_field.error_text = "Please paste your Private Key PEM."
                            log_status("Sign in failed. Private key empty.")
                            page.update()
                        run_on_ui(_fail_empty_key)
                        return
                    try:
                        from crypto_utils import deserialize_private_key, save_keys_to_disk
                        priv = deserialize_private_key(imported_pem.encode("utf-8"))
                        pub = priv.public_key()
                        save_keys_to_disk(username, priv, pub)
                        print(f"[Import] Key successfully imported and stored for user '{username}'.")
                    except Exception as e_key:
                        def _fail_invalid_key(err_msg=str(e_key)):
                            login_btn.disabled = False
                            login_btn.content.controls[1].value = "Sign In"
                            username_field.error_text = f"Invalid Private Key PEM: {err_msg}"
                            log_status(f"Import failed: {err_msg}")
                            page.update()
                        run_on_ui(_fail_invalid_key)
                        return

                priv, pub = initialize_keys(username)
                state["private_key"] = priv
                state["public_key"]  = pub
                state["store"]       = MessageStore(username)

                register_with_server(username, pub, priv)

                # Reset button state and transition to inbox on Flet's event loop
                async def login_success_ui():
                    login_btn.disabled = False
                    login_btn.content.controls[1].value = "Sign In"
                    state["logged_in"] = True
                    show_inbox_screen()
                    
                    def background_sync():
                        sync_chat_settings()
                        sync_user_groups_from_server()
                        fetch_offline_messages()
                        start_websocket_listener()
                    
                    threading.Thread(target=background_sync, daemon=True).start()

                page.run_task(login_success_ui)
            except Exception as ex:
                async def login_failed_ui():
                    login_btn.disabled = False
                    login_btn.content.controls[1].value = "Sign In"
                    username_field.error_text = f"Hata: {ex}"
                    log_status(f"Giris sirasinda hata olustu: {ex}")
                    page.update()
                page.run_task(login_failed_ui)

        threading.Thread(target=do_login, daemon=True).start()

    login_view = ft.Container(
        content=ft.Column(
            controls=[
                ft.Container(height=60),
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.LOCK_OUTLINE, size=64, color="#8b5cf6"),
                            ft.Text("HybridP2P", size=32, weight=ft.FontWeight.BOLD, color="#ffffff"),
                            ft.Text("Messenger", size=18, weight=ft.FontWeight.W_300, color="#8b5cf6"),
                            ft.Container(height=4),
                            ft.Text("End-to-End Encrypted Messaging", size=13,
                                    color="#9e9e9e", text_align=ft.TextAlign.CENTER),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2,
                    ),
                    alignment=ft.Alignment(0, 0),
                ),
                ft.Container(height=40),
                ft.Container(
                    content=ft.Column(
                        controls=[
                            server_address_field,
                            ft.Container(height=12),
                            username_field,
                            ft.Container(height=12),
                            import_key_checkbox,
                            import_key_field,
                            ft.Container(height=16),
                            login_btn,
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0,
                    ),
                    padding=ft.Padding(40, 0, 40, 0),
                ),
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SHIELD, size=14, color="#22c55e"),
                            ft.Text("RSA-4096 + AES-256-GCM + E2EE Dosya", size=11, color="#22c55e"),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER, spacing=6,
                    ),
                    padding=ft.Padding(0, 0, 0, 24),
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True,
        ),
        expand=True,
        gradient=ft.LinearGradient(
            begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
            colors=["#09090b", "#18181b", "#09090b"],
        ),
    )

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     SOHBET EKRANI                               ║
    # ╚═══════════════════════════════════════════════════════════════╝

    recipient_field = ft.TextField(
        label="Recipient", hint_text="Example: bob",
        prefix_icon=ft.Icons.PERSON_SEARCH,
        border_color="#8b5cf6", focused_border_color="#a78bfa",
        cursor_color="#8b5cf6", text_size=14, height=48, expand=True,
    )

    message_input = ft.TextField(
        hint_text="Type your message...",
        border_color="#3f3f46", focused_border_color="#8b5cf6",
        cursor_color="#8b5cf6", text_size=14,
        min_lines=1, max_lines=3, expand=True,
        on_submit=lambda e: on_send_click(e),
        shift_enter=True,
    )

    def on_connect_recipient(e):
        recipient_input = recipient_field.value.strip()
        if not recipient_input:
            log_status("Recipient name or group ID cannot be empty!")
            return

        def connect_to_recipient_final(rec, pub):
            state["recipient"]         = rec
            state["is_group"]          = False
            state["recipient_pub_key"] = pub
            recipient_field.value      = rec
            recipient_field.read_only  = True
            recipient_field.border_color = "#22c55e"
            ephemeral_btn.disabled = False

            if state["store"]:
                state["store"].mark_as_read(rec)
                history = state["store"].get_messages(rec)
                received_msgs = [m for m in history if not m.get("is_mine", False)]
                if received_msgs:
                    latest_ts = received_msgs[-1]["timestamp"]
                    send_read_receipt(rec, latest_ts)

            ephemeral = state["store"].is_ephemeral(rec)
            state["ephemeral"] = ephemeral
            _update_ephemeral_ui()
            load_history_to_chat()
            if ephemeral:
                add_system_event("This chat is in EPHEMERAL mode — messages are not saved")
            log_status(f"'{rec}' ile sohbet basladi.")
            show_chat_screen()
            page.update()

        _active_dialog = [None]  # mutable container to hold current open dialog

        def _dismiss_active_dialog():
            """Single shared function to safely close whatever dialog is open."""
            d = _active_dialog[0]
            if d is None:
                return
            _active_dialog[0] = None
            d.open = False
            try:
                page.overlay.remove(d)
            except Exception:
                pass
            page.update()

        def close_warning_dialog(accept, rec=None, new_pem=None):
            _dismiss_active_dialog()
            if accept and rec and new_pem:
                try:
                    new_pub_key = pem_string_to_public_key(new_pem)
                    fingerprint = get_public_key_fingerprint(new_pub_key)
                    state["store"].save_contact(rec, new_pem, fingerprint)
                    log_status(f"New key for '{rec}' accepted.")
                    connect_to_recipient_final(rec, new_pub_key)
                except Exception as ex:
                    log_status(f"Error: {ex}")
            else:
                log_status("Connection rejected for security reasons.")

        def close_tofu_dialog(accept, rec=None, pem=None, pub=None):
            _dismiss_active_dialog()
            if accept and rec and pem and pub:
                try:
                    fingerprint = get_public_key_fingerprint(pub)
                    state["store"].save_contact(rec, pem, fingerprint)
                    print(f"[Contact] Saved public key for '{rec}' to local DB")
                    connect_to_recipient_final(rec, pub)
                except Exception as ex:
                    log_status(f"Error: {ex}")
            else:
                log_status("Connection not approved.")

        # Check if the input is a JSON contact card
        imported_card = None
        if recipient_input.startswith("{") and recipient_input.endswith("}"):
            try:
                card_data = json.loads(recipient_input)
                if "username" in card_data and "public_key" in card_data:
                    imported_card = card_data
            except Exception as ex:
                print(f"[Import Contact Card Error] {ex}")

        if imported_card:
            recipient = imported_card["username"].lower()
            if recipient == state["username"]:
                log_status("You cannot add your own contact card!")
                return
            
            pub_key_pem = imported_card["public_key"]
            try:
                pub_key = pem_string_to_public_key(pub_key_pem)
                fingerprint = get_public_key_fingerprint(pub_key)
                state["store"].save_contact(recipient, pub_key_pem, fingerprint)
                log_status(f"'{recipient}' kimlik karti basariyla import edildi!")
                recipient_field.value = recipient
                recipient_input = recipient
            except Exception as ex:
                log_status(f"Kimlik karti yukleme hatasi: {ex}")
                return
        else:
            recipient = recipient_input.lower()
            if recipient == state["username"]:
                log_status("You cannot send a message to yourself!")
                return

        if recipient.startswith("group_"):
            key = state["store"].get_group_key(recipient)
            if not key:
                log_status("Error: You don't have the encryption key for this group!")
                return
            
            state["recipient"] = recipient
            state["is_group"] = True
            
            if state["store"]:
                state["store"].mark_as_read(recipient)
            
            chat_info = state["store"].get_chat_info(recipient)
            gname = chat_info.get("partner", recipient)
            
            recipient_field.value = gname
            recipient_field.read_only = True
            recipient_field.border_color = "#22c55e"
            ephemeral_btn.disabled = True
            
            load_history_to_chat()
            log_status(f"'{gname}' grubu ile sohbet basladi.")
            show_chat_screen()
            page.update()
            return

        local_contact = state["store"].get_contact(recipient)
        if local_contact:
            print(f"[Contact] Loaded local public key for '{recipient}'")
            pub_key = pem_string_to_public_key(local_contact["public_key"])
            server_pub_key = fetch_recipient_pub_key(recipient)
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
                    page.overlay.append(dialog)
                    dialog.open = True
                    page.update()
                    return
        else:
            pub_key = fetch_recipient_pub_key(recipient)
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
                page.overlay.append(dialog)
                dialog.open = True
                page.update()
                return

        if pub_key:
            connect_to_recipient_final(recipient, pub_key)
        else:
            log_status(f"'{recipient}' bulunamadi.")

    def on_send_click(e):
        recipient = state["recipient"]
        if not recipient:
            log_status("Please connect to a recipient or group first!")
            return

        text = message_input.value.strip()
        view_once = state["view_once_mode"]
        staged = state.get("staged_file")

        if not text and not staged:
            return

        # Clear input field immediately
        message_input.value = ""
        page.update()

        if staged:
            # Show progress bar and disable attach/staged remove controls
            upload_progress.visible = True
            staged_file_container.disabled = True
            attach_btn.disabled = True
            upload_progress.value = 0.2
            log_status(f"Encrypting '{staged['name']}'...")
            page.update()

            def do_file_upload_and_send():
                def _progress(val: float, status: str = None):
                    upload_progress.value = val
                    if status:
                        status_text.value = status
                    page.update()

                try:
                    raw = Path(staged["path"]).read_bytes()
                    file_type = staged["type"]
                    
                    run_on_ui(_progress, 0.4, "Uploading encrypted payload...")

                    encrypted = encrypt_bytes(raw, state["recipient_pub_key"])
                    run_on_ui(_progress, 0.6)

                    resp = signed_post(
                        "/api/upload_file",
                        {
                            "sender":         state["username"],
                            "recipient":      recipient,
                            "encrypted_data": encrypted,
                            "original_name":  staged["name"],
                            "file_type":      file_type,
                        },
                        timeout=60,
                    )
                    
                    if resp.status_code != 200:
                        def _upload_failed():
                            status_text.value = f"Upload failed: {resp.text}"
                            upload_progress.visible = False
                            staged_file_container.disabled = False
                            attach_btn.disabled = False
                            page.update()
                        run_on_ui(_upload_failed)
                        return

                    run_on_ui(_progress, 0.8)

                    file_uuid = resp.json()["uuid"]
                    
                    send_ws_message_with_fallback({
                        "type":          "file_message",
                        "sender":        state["username"],
                        "recipient":     recipient,
                        "file_uuid":     file_uuid,
                        "original_name": staged["name"],
                        "file_type":     file_type,
                        "view_once":     view_once,
                    })

                    def _upload_success():
                        add_file_to_chat(
                            sender=state["username"],
                            file_uuid=file_uuid,
                            original_name=staged["name"],
                            file_type=file_type,
                            is_mine=True,
                            view_once=view_once,
                        )
                        load_inbox_chats()

                        # Reset view-once toggle
                        if view_once:
                            state["view_once_mode"] = False
                            view_once_msg_btn.icon       = ft.Icons.VISIBILITY
                            view_once_msg_btn.icon_color = "#888888"

                        status_text.value = f"Sent: {staged['name']}"
                        
                        # Clear staged state and reset UI
                        state["staged_file"] = None
                        staged_file_container.visible = False
                        staged_file_container.disabled = False
                        upload_progress.visible = False
                        attach_btn.disabled = False
                        page.update()
                    run_on_ui(_upload_success)

                except Exception as ex:
                    def _upload_error():
                        status_text.value = f"Upload error: {ex}"
                        upload_progress.visible = False
                        staged_file_container.disabled = False
                        attach_btn.disabled = False
                        page.update()
                    run_on_ui(_upload_error)

            threading.Thread(target=do_file_upload_and_send, daemon=True).start()

        if text:
            is_group = bool(state.get("is_group", False))
            if is_group:
                group_id = recipient
                group_key = state["store"].get_group_key(group_id)
                if not group_key:
                    log_status("Error: Group encryption key not found!")
                    return
                try:
                    encrypted = encrypt_symmetric(text, group_key)
                except Exception as ex:
                    log_status(f"Group encryption error: {ex}")
                    return

                timestamp = datetime.now(timezone.utc).isoformat()
                send_group_message_via_ws(group_id, encrypted, timestamp=timestamp)
                add_message_to_chat(
                    sender=state["username"], text=text,
                    is_mine=True, save=True, view_once=False, time_str=timestamp, is_read=False
                )
            else:
                if not state["recipient_pub_key"]:
                    log_status("Recipient public key not found!")
                    return
                try:
                    encrypted = encrypt_message(text, state["recipient_pub_key"])
                except Exception as ex:
                    log_status(f"Encryption error: {ex}")
                    return

                timestamp = datetime.now(timezone.utc).isoformat()
                send_message_via_ws(recipient, encrypted, view_once, timestamp=timestamp)
                add_message_to_chat(
                    sender=state["username"], text=text,
                    is_mine=True, save=not view_once, view_once=view_once,
                    encrypted_payload=encrypted, time_str=timestamp, is_read=False
                )
                load_inbox_chats()

            if view_once:
                state["view_once_mode"] = False
                view_once_msg_btn.icon       = ft.Icons.VISIBILITY
                view_once_msg_btn.icon_color = "#888888"
                page.update()

    username_text = ft.Text("", size=11, color="#9e9e9e")
    status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor="#ef4444")
    status_label = ft.Text("Server: Offline", size=10, color="#ef4444", weight=ft.FontWeight.BOLD)
    
    username_subtitle = ft.Row(
        controls=[
            username_text,
            ft.Text("|", size=10, color="#3f3f46"),
            status_dot,
            status_label
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER
    )

    recipient_status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor="#ef4444")
    recipient_status_label = ft.Text("Offline", size=10, color="#ef4444", weight=ft.FontWeight.BOLD)
    
    recipient_status_row = ft.Row(
        controls=[
            recipient_status_dot,
            recipient_status_label
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False
    )
    
    def update_recipient_status_ui(online):
        def _update():
            if state.get("is_group", False) or not state["recipient"]:
                recipient_status_row.visible = False
            else:
                recipient_status_row.visible = True
                if online:
                    recipient_status_dot.bgcolor = "#22c55e"
                    recipient_status_label.value = "Online"
                    recipient_status_label.color = "#22c55e"
                else:
                    recipient_status_dot.bgcolor = "#ef4444"
                    recipient_status_label.value = "Offline"
                    recipient_status_label.color = "#ef4444"
            try: page.update()
            except: pass
        run_on_ui(_update)

    def refresh_recipient_status():
        if not state["recipient"] or state.get("is_group", False):
            update_recipient_status_ui(None)
            return

        def run():
            try:
                r = signed_get(f"/api/status/{state['recipient']}", timeout=3)
                if r.status_code == 200:
                    online = r.json().get("online", False)
                    update_recipient_status_ui(online)
                else:
                    update_recipient_status_ui(False)
            except:
                update_recipient_status_ui(False)

        threading.Thread(target=run, daemon=True).start()

    def check_recipient_status_loop():
        while True:
            refresh_recipient_status()
            import time; time.sleep(5)

    threading.Thread(target=check_recipient_status_loop, daemon=True, name="status-checker").start()

    def update_connection_status(is_connected: bool):
        def _update():
            if is_connected:
                status_dot.bgcolor = "#3b82f6"  # Blue
                status_label.value = "Server: Online"
                status_label.color = "#60a5fa"
            else:
                status_dot.bgcolor = "#ef4444"  # Red
                status_label.value = "Server: Offline"
                status_label.color = "#ef4444"
            try: page.update()
            except: pass
        run_on_ui(_update)

    chat_title_text = ft.Text("No active chat", size=16, weight=ft.FontWeight.BOLD, color="#ffffff")
    inbox_list = ft.ListView(expand=True, spacing=4, padding=8)

    # Define a single floating action button
    fab = ft.FloatingActionButton(
        icon=ft.Icons.CHAT,
        bgcolor="#8b5cf6",
        on_click=lambda e: open_new_chat_dialog(e, 0),
        tooltip="Start New Chat / Group",
        visible=False,
    )
    page.floating_action_button = fab

    def load_inbox_chats(query: str = None):
        if not state["store"]: return
        inbox_list.controls.clear()
        
        if not query:
            chats = state["store"].get_all_chats()
            if not chats:
                inbox_list.controls.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=48, color="#3f3f46"),
                                ft.Text("No chats yet.", size=14, color="#9e9e9e"),
                                ft.Text("Start a new chat by clicking the '+' button.", size=11, color="#666666"),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=6,
                        ),
                        padding=40,
                        alignment=ft.Alignment(0, 0),
                    )
                )
            else:
                for c in chats:
                    partner = c["partner"]
                    chat_id = c["chat_id"]
                    is_group = bool(c.get("is_group", 0))
                    last_msg = c.get("last_message") or ""
                    last_time = _fmt_time(c.get("last_time")) if c.get("last_time") else ""
                    unread_count = c.get("unread_count", 0)
                    
                    if len(last_msg) > 35:
                        last_msg = last_msg[:32] + "..."
                    
                    avatar_icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
                    avatar_color = "#8b5cf6" if is_group else "#007acc"
                    
                    def on_chat_tile_click(e, p=partner, ig=is_group):
                        recipient_field.value = p
                        on_connect_recipient(None)
                    
                    row2_controls = [
                        ft.Text(last_msg or "No messages yet", size=12, color="#9e9e9e", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
                    ]
                    if unread_count > 0:
                        row2_controls.append(
                            ft.Container(
                                content=ft.Text(
                                    str(unread_count),
                                    size=10,
                                    color="#ffffff",
                                    weight=ft.FontWeight.BOLD,
                                ),
                                bgcolor="#8b5cf6",
                                border_radius=10,
                                padding=ft.Padding(6, 2, 6, 2),
                                alignment=ft.Alignment(0, 0),
                            )
                        )
                    
                    inbox_list.controls.append(
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    ft.CircleAvatar(
                                        content=ft.Icon(avatar_icon, color="#ffffff", size=18),
                                        bgcolor=avatar_color,
                                        radius=20,
                                    ),
                                    ft.Column(
                                        controls=[
                                            ft.Row(
                                                controls=[
                                                    ft.Text(partner, weight=ft.FontWeight.BOLD, size=14, color="#ffffff"),
                                                    ft.Text(last_time, size=10, color="#22c55e" if unread_count > 0 else "#888888"),
                                                ],
                                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                            ),
                                            ft.Row(
                                                controls=row2_controls,
                                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                ],
                                spacing=12,
                            ),
                            padding=ft.Padding(12, 10, 12, 10),
                            border_radius=8,
                            ink=True,
                            on_click=lambda e, p=partner, ig=is_group: on_chat_tile_click(e, p, ig),
                            bgcolor="#18181b",
                        )
                    )
        else:
            results = state["store"].search_chats_and_messages(query)
            matching_chats = results["chats"]
            matching_msgs = results["messages"]
            
            if not matching_chats and not matching_msgs:
                inbox_list.controls.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.SEARCH_OFF, size=48, color="#3f3f46"),
                                ft.Text("No results found", size=14, color="#9e9e9e"),
                                ft.Text("Try checking the spelling or searching for another keyword.", size=11, color="#666666"),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=6,
                        ),
                        padding=40,
                        alignment=ft.Alignment(0, 0),
                    )
                )
            else:
                def on_chat_tile_click_search(e, p, ig):
                    recipient_field.value = p
                    on_connect_recipient(None)
                
                if matching_chats:
                    inbox_list.controls.append(
                        ft.Container(
                            content=ft.Text("CHATS", size=11, weight=ft.FontWeight.BOLD, color="#8b5cf6"),
                            padding=ft.Padding(12, 8, 12, 4)
                        )
                    )
                    for c in matching_chats:
                        partner = c["partner"]
                        is_group = bool(c.get("is_group", 0))
                        last_msg = c.get("last_message") or ""
                        last_time = _fmt_time(c.get("last_time")) if c.get("last_time") else ""
                        unread_count = c.get("unread_count", 0)
                        
                        if len(last_msg) > 35:
                            last_msg = last_msg[:32] + "..."
                            
                        avatar_icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
                        avatar_color = "#8b5cf6" if is_group else "#007acc"
                        
                        row2_controls = [
                            ft.Text(last_msg or "No messages yet", size=12, color="#9e9e9e", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
                        ]
                        if unread_count > 0:
                            row2_controls.append(
                                ft.Container(
                                    content=ft.Text(
                                        str(unread_count),
                                        size=10,
                                        color="#ffffff",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    bgcolor="#8b5cf6",
                                    border_radius=10,
                                    padding=ft.Padding(6, 2, 6, 2),
                                    alignment=ft.Alignment(0, 0),
                                )
                            )
                            
                        inbox_list.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.CircleAvatar(
                                            content=ft.Icon(avatar_icon, color="#ffffff", size=18),
                                            bgcolor=avatar_color,
                                            radius=20,
                                        ),
                                        ft.Column(
                                            controls=[
                                                ft.Row(
                                                    controls=[
                                                        ft.Text(partner, weight=ft.FontWeight.BOLD, size=14, color="#ffffff"),
                                                        ft.Text(last_time, size=10, color="#22c55e" if unread_count > 0 else "#888888"),
                                                    ],
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                                ft.Row(
                                                    controls=row2_controls,
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                            ],
                                            spacing=2,
                                            expand=True,
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=ft.Padding(12, 10, 12, 10),
                                border_radius=8,
                                ink=True,
                                on_click=lambda e, p=partner, ig=is_group: on_chat_tile_click_search(e, p, ig),
                                bgcolor="#18181b",
                            )
                        )
                        
                if matching_msgs:
                    inbox_list.controls.append(
                        ft.Container(
                            content=ft.Text("MESSAGES", size=11, weight=ft.FontWeight.BOLD, color="#8b5cf6"),
                            padding=ft.Padding(12, 12, 12, 4)
                        )
                    )
                    for m in matching_msgs:
                        partner = m["partner"]
                        sender = m["sender"]
                        content = m["content"]
                        msg_time = _fmt_time(m["timestamp"])
                        is_group = bool(m["is_group"])
                        
                        snippet = f"{sender}: {content}"
                        if len(snippet) > 45:
                            snippet = snippet[:42] + "..."
                            
                        avatar_icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
                        avatar_color = "#8b5cf6" if is_group else "#007acc"
                        
                        inbox_list.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.CircleAvatar(
                                            content=ft.Icon(avatar_icon, color="#ffffff", size=16),
                                            bgcolor=avatar_color,
                                            radius=16,
                                        ),
                                        ft.Column(
                                            controls=[
                                                ft.Row(
                                                    controls=[
                                                        ft.Text(partner, weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                                                        ft.Text(msg_time, size=9, color="#888888"),
                                                    ],
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                                ft.Text(snippet, size=11, color="#9e9e9e", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                            ],
                                            spacing=2,
                                            expand=True,
                                        ),
                                    ],
                                    spacing=10,
                                ),
                                padding=ft.Padding(12, 8, 12, 8),
                                border_radius=6,
                                ink=True,
                                on_click=lambda e, p=partner, ig=is_group: on_chat_tile_click_search(e, p, ig),
                                bgcolor="#141416",
                            )
                        )
        try: page.update()
        except: pass

    def open_new_chat_dialog(e, default_tab_index=0):
        # 1. DM Tab Controls
        name_input = ft.TextField(
            label="Username",
            hint_text="Example: bob",
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
        )
        
        # 2. Group Tab Controls
        group_name_input = ft.TextField(
            label="Group Name",
            hint_text="Example: Family",
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
        )
        group_members_input = ft.TextField(
            label="Members",
            hint_text="Example: bob, charlie (comma separated)",
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
        )
        
        groups_list_column = ft.Column(spacing=6, height=180, scroll=ft.ScrollMode.AUTO)
        groups_loading = ft.Row(
            controls=[
                ft.ProgressRing(width=16, height=16, stroke_width=2, color="#8b5cf6"),
                ft.Text(" Loading groups...", size=12, color="#888888")
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        )
        groups_list_column.controls.append(groups_loading)

        def close_dialog(e):
            dialog.open = False
            page.update()

        def on_confirm(e):
            rec = name_input.value.strip().lower()
            if not rec: return
            if rec == state["username"]:
                name_input.error_text = "You cannot chat with yourself!"
                page.update()
                return
            
            close_dialog(None)
            recipient_field.value = rec
            on_connect_recipient(None)

        def on_group_select(group_id, name):
            key = state["store"].get_group_key(group_id)
            if not key:
                log_status("Error: You don't have the encryption key for this group!")
                close_dialog(None)
                return
            
            state["recipient"] = group_id
            state["is_group"] = True
            
            recipient_field.value = name
            recipient_field.read_only = True
            recipient_field.border_color = "#22c55e"
            ephemeral_btn.disabled = True
            
            load_history_to_chat()
            log_status(f"'{name}' grubu ile sohbet basladi.")
            show_chat_screen()
            close_dialog(None)

        def on_group_rekey(group_id, name):
            import os
            new_key = os.urandom(32)
            state["store"].save_group_key(group_id, new_key.hex())
            
            def do_rekey():
                try:
                    m_resp = signed_get(f"/api/groups/{group_id}/members", timeout=5)
                    members = m_resp.json().get("members", []) if m_resp.status_code == 200 else []
                except:
                    members = []
                    
                for m in members:
                    m_username = m["username"]
                    if m_username == state["username"]: continue
                    m_pub_key = fetch_recipient_pub_key(m_username)
                    if not m_pub_key: continue
                    
                    enc_payload = encrypt_message(new_key.hex(), m_pub_key)
                    
                    send_ws_message_with_fallback({
                        "type": "group_key_dist",
                        "sender": state["username"],
                        "recipient": m_username,
                        "group_id": group_id,
                        "encrypted_payload": enc_payload
                    })
                log_status(f"'{name}' grubunun anahtari yenilendi ve dagitildi.")
                
            threading.Thread(target=do_rekey, daemon=True).start()
            close_dialog(None)

        def on_group_leave(group_id, name):
            def do_leave():
                try:
                    resp = signed_delete(f"/api/groups/{group_id}/members/{state['username']}", timeout=5)
                    if resp.status_code == 200:
                        def _success():
                            log_status(f"'{name}' grubundan ciktiniz.")
                            if state["recipient"] == group_id:
                                state["recipient"] = None
                                state["is_group"] = False
                                recipient_field.value = ""
                                recipient_field.read_only = False
                                recipient_field.border_color = "#8b5cf6"
                                chat_list.controls.clear()
                            load_inbox_chats()
                        run_on_ui(_success)
                except Exception as ex:
                    log_status(f"Gruptan cikma hatasi: {ex}")
                
            threading.Thread(target=do_leave, daemon=True).start()
            close_dialog(None)

        def on_create_click(e):
            name = group_name_input.value.strip()
            members_raw = group_members_input.value.strip()
            
            if not name:
                group_name_input.error_text = "Group name cannot be empty!"
                page.update()
                return
                
            members = [m.strip().lower() for m in members_raw.split(",") if m.strip()]
            
            import uuid as uuid_lib
            group_id = f"group_{uuid_lib.uuid4().hex[:12]}"
            
            import os
            group_key = os.urandom(32)
            
            def do_create():
                try:
                    resp = signed_post("/api/groups", {
                        "group_id": group_id,
                        "group_name": name,
                        "creator": state["username"],
                        "members": members
                    }, timeout=5)
                    
                    if resp.status_code == 200:
                        state["store"].save_group_key(group_id, group_key.hex())
                        state["store"].get_or_create_group_chat(group_id, name)
                        
                        for m in members:
                            m_pub = fetch_recipient_pub_key(m)
                            if m_pub:
                                enc_key = encrypt_message(group_key.hex(), m_pub)
                                
                                send_ws_message_with_fallback({
                                    "type": "group_key_dist",
                                    "sender": state["username"],
                                    "recipient": m,
                                    "group_id": group_id,
                                    "encrypted_payload": enc_key
                                })
                                
                        def _success():
                            state["recipient"] = group_id
                            state["is_group"] = True
                            recipient_field.value = name
                            recipient_field.read_only = True
                            recipient_field.border_color = "#22c55e"
                            ephemeral_btn.disabled = True
                            load_history_to_chat()
                            log_status(f"'{name}' grubu olusturuldu.")
                            show_chat_screen()
                        run_on_ui(_success)
                    else:
                        log_status(f"Grup olusturma hatasi: {resp.text}")
                except Exception as ex:
                    log_status(f"Grup olusturma hatasi: {ex}")
                
            threading.Thread(target=do_create, daemon=True).start()
            close_dialog(None)

        # Tabs & layouts
        dm_tab_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Enter username to chat directly:", size=13, color="#9e9e9e"),
                    name_input,
                    ft.Container(height=10),
                    ft.Row(
                        controls=[
                            ft.TextButton("Cancel", on_click=close_dialog),
                            ft.Button(
                                "Start Chat", 
                                on_click=on_confirm, 
                                style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff")
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    )
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding(12, 16, 12, 16),
        )

        group_tab_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Create New Group", weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                    group_name_input,
                    group_members_input,
                    ft.Button(
                        "Create Group",
                        on_click=on_create_click,
                        style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff")
                    ),
                    ft.Divider(color="#27272a"),
                    ft.Text("My Groups", weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                    groups_list_column,
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding(12, 16, 12, 16),
        )

        tabs = ft.Tabs(
            selected_index=default_tab_index,
            length=2,
            content=ft.Column(
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Direct Message", icon=ft.Icons.PERSON),
                            ft.Tab(label="Group Chat", icon=ft.Icons.GROUP),
                        ],
                    ),
                    ft.TabBarView(
                        controls=[
                            dm_tab_content,
                            group_tab_content,
                        ],
                        expand=True,
                    ),
                ],
                expand=True,
            ),
            expand=True,
            animation_duration=200,
        )

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHAT_ROUNDED, color="#8b5cf6"),
                    ft.Text("New Conversation", size=16, color="#ffffff"),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color="#888888",
                        on_click=close_dialog,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=tabs,
                width=400,
                height=520,
            ),
            bgcolor="#18181b",
        )

        def load_groups_async():
            try:
                resp = signed_get(f"/api/groups/{state['username']}", timeout=5)
                groups = resp.json().get("groups", []) if resp.status_code == 200 else []
            except:
                groups = []
            
            def _update_ui(g_list):
                groups_list_column.controls.clear()
                if not g_list:
                    groups_list_column.controls.append(
                        ft.Text("No groups found.", size=12, color="#888888")
                    )
                else:
                    for g in g_list:
                        gid = g["group_id"]
                        gname = g["group_name"]
                        
                        groups_list_column.controls.append(
                            ft.Container(
                                content=ft.Column(
                                    controls=[
                                        ft.Row(
                                            controls=[
                                                ft.Text(gname, weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                                                ft.Container(expand=True),
                                                ft.IconButton(
                                                    icon=ft.Icons.CHAT,
                                                    icon_size=16,
                                                    icon_color="#8b5cf6",
                                                    tooltip="Start Chat",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_select(gid, gname)
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.KEY,
                                                    icon_size=16,
                                                    icon_color="#22c55e",
                                                    tooltip="Refresh Key (Rekey)",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_rekey(gid, gname)
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.EXIT_TO_APP,
                                                    icon_size=16,
                                                    icon_color="#ef4444",
                                                    tooltip="Leave Group",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_leave(gid, gname)
                                                )
                                            ],
                                            alignment=ft.MainAxisAlignment.CENTER,
                                            spacing=4
                                        ),
                                        ft.Text(f"ID: {gid}", size=9, color="#888888")
                                    ],
                                    spacing=2
                                ),
                                padding=6,
                                border=ft.Border(left=ft.BorderSide(1, "#3f3f46"), top=ft.BorderSide(1, "#3f3f46"), right=ft.BorderSide(1, "#3f3f46"), bottom=ft.BorderSide(1, "#3f3f46")),
                                border_radius=8,
                                bgcolor="#27272a"
                            )
                        )
                try: page.update()
                except: pass

            run_on_ui(_update_ui, groups)

        page.overlay.append(dialog)
        dialog.open = True
        page.update()
        
        threading.Thread(target=load_groups_async, daemon=True).start()

    def on_search_change(e):
        query = search_field.value.strip()
        load_inbox_chats(query)

    search_field = ft.TextField(
        hint_text="Search chats and messages...",
        prefix_icon=ft.Icons.SEARCH,
        border_color="#27272a",
        focused_border_color="#8b5cf6",
        cursor_color="#8b5cf6",
        height=38,
        text_size=13,
        content_padding=ft.Padding(10, 0, 10, 0),
        on_change=on_search_change,
    )

    def perform_logout(e=None):
        # 1. Set logged_in flag to False to break ws loop
        state["logged_in"] = False
        
        # 2. Close WS if exists
        ws = state.get("ws")
        loop = state.get("ws_loop")
        if ws and loop:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
                print("[Logout] WebSocket connection closed.")
            except Exception as ex:
                print(f"[Logout] Error closing ws: {ex}")
        
        # 3. Reset state
        state["username"] = None
        state["private_key"] = None
        state["public_key"] = None
        state["recipient"] = None
        state["recipient_pub_key"] = None
        state["ws"] = None
        state["ws_loop"] = None
        state["store"] = None
        state["ephemeral"] = False
        state["view_once_mode"] = False
        state["staged_file"] = None
        
        # 4. Reset login inputs
        username_field.value = ""
        username_field.error_text = None
        import_key_checkbox.value = False
        import_key_field.value = ""
        import_key_field.visible = False
        
        log_status("Signed out successfully.")
        show_login_screen()

    def open_settings_dialog(e):
        from crypto_utils import serialize_private_key
        
        def clean_pem_for_display(pem_str: str) -> str:
            lines = pem_str.strip().splitlines()
            body_lines = [line for line in lines if not line.strip().startswith("-----")]
            return "\n".join(body_lines).strip()

        # Format keys
        try:
            pub_pem = public_key_to_pem_string(state["public_key"])
            pub_pem_display = clean_pem_for_display(pub_pem)
        except Exception as ex:
            pub_pem = f"Error: {ex}"
            pub_pem_display = pub_pem
            
        try:
            priv_pem = serialize_private_key(state["private_key"]).decode("utf-8")
            priv_pem_display = clean_pem_for_display(priv_pem)
        except Exception as ex:
            priv_pem = f"Error: {ex}"
            priv_pem_display = priv_pem

        # Public Key textfield (read-only, multiline)
        pub_key_tf = ft.TextField(
            label="Public Key PEM",
            value=pub_pem_display,
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            text_size=11,
            cursor_color="#8b5cf6",
        )

        # Private Key container. Initially hidden (shown as dots)
        priv_key_value = "••••••••••••••••••••••••••••••••••••••••••••••••••••••••••"
        
        priv_key_tf = ft.TextField(
            label="Private Key PEM (Secret)",
            value=priv_key_value,
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color="#27272a",
            focused_border_color="#ef4444",
            text_size=11,
            cursor_color="#8b5cf6",
        )

        reveal_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY,
            icon_color="#ef4444",
            icon_size=20,
            tooltip="Reveal Private Key",
        )
        
        copy_btn = ft.IconButton(
            icon=ft.Icons.COPY,
            icon_color="#8b5cf6",
            icon_size=20,
            tooltip="Copy Private Key",
            visible=False,
        )

        def close_settings(e):
            dialog.open = False
            page.update()

        def confirm_reveal_key(e):
            confirm_dialog = None
            
            def cancel_reveal(e):
                confirm_dialog.open = False
                page.update()
                
            def proceed_reveal(e):
                confirm_dialog.open = False
                priv_key_tf.value = priv_pem_display
                priv_key_tf.focused_border_color = "#8b5cf6"
                reveal_btn.visible = False
                copy_btn.visible = True
                page.update()

            confirm_dialog = ft.AlertDialog(
                title=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.WARNING_ROUNDED, color="#ef4444"),
                        ft.Text("Warning: Reveal Private Key", size=16, color="#ef4444", weight=ft.FontWeight.BOLD),
                    ],
                    spacing=8,
                ),
                content=ft.Text(
                    "Are you sure you want to reveal your Private Key?\n\nAnyone with access to this key can decrypt and read your E2EE messages. Keep it highly secure!",
                    size=13,
                    color="#e0e0e0"
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=cancel_reveal),
                    ft.TextButton("Reveal", on_click=proceed_reveal, style=ft.ButtonStyle(color="#ef4444")),
                ],
                bgcolor="#18181b",
            )
            page.overlay.append(confirm_dialog)
            confirm_dialog.open = True
            page.update()

        reveal_btn.on_click = confirm_reveal_key

        def copy_private_key(e):
            copy_to_clipboard(priv_pem)
            log_status("Private Key copied to clipboard!")
            
        copy_btn.on_click = copy_private_key

        def on_signout_click(e):
            dialog.open = False
            page.update()
            perform_logout()

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color="#8b5cf6"),
                    ft.Text("Settings", size=18, color="#ffffff", weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color="#888888",
                        on_click=close_settings,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Text("Logged in as:", size=12, color="#888888"),
                                ft.Text(state["username"], size=14, color="#ffffff", weight=ft.FontWeight.BOLD),
                            ],
                            alignment=ft.MainAxisAlignment.START,
                        ),
                        ft.Divider(color="#27272a", height=10),
                        pub_key_tf,
                        ft.Container(height=5),
                        ft.Row(
                            controls=[
                                ft.Text("Private Key PEM", size=12, color="#888888", weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                reveal_btn,
                                copy_btn,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        priv_key_tf,
                        ft.Container(height=15),
                        ft.Button(
                            content=ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.LOGOUT, size=18, color="#ffffff"),
                                    ft.Text("Sign Out", size=14, weight=ft.FontWeight.BOLD, color="#ffffff"),
                                ],
                                alignment=ft.MainAxisAlignment.CENTER,
                                spacing=8,
                            ),
                            on_click=on_signout_click,
                            style=ft.ButtonStyle(
                                bgcolor="#ef4444",
                                padding=ft.Padding(16, 12, 16, 12),
                                shape=ft.RoundedRectangleBorder(radius=6),
                            ),
                            width=300,
                        ),
                    ],
                    spacing=8,
                    tight=True,
                ),
                width=360,
                padding=ft.Padding(0, 10, 0, 10),
            ),
            bgcolor="#18181b",
        )

        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def open_pure_p2p_dialog(e):
        import zlib
        import base64
        import json
        import threading

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
            border_color="#27272a",
            focused_border_color="#8b5cf6",
        )

        caller_offer_tf = ft.TextField(
            label="Arama Teklifiniz (Offer Kodu)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            text_size=10,
        )

        caller_qr_image = ft.Image(src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", width=160, height=160, fit="contain", visible=False)
        caller_qr_container = ft.Container(
            content=ft.Column([
                ft.Text("QR Kod (Karşı tarafa taratın):", size=11, color="#888888"),
                caller_qr_image
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            visible=False,
            alignment=ft.Alignment(0, 0)
        )

        caller_status_text = ft.Text("", size=11, color="#8b5cf6")
        caller_prog = ft.ProgressBar(color="#8b5cf6", visible=False)

        caller_copy_btn = ft.Button(
            content="Teklifi Kopyala",
            icon=ft.Icons.COPY,
            on_click=lambda e: copy_to_clipboard(caller_offer_tf.value) if caller_offer_tf.value else None,
            disabled=True,
            style=ft.ButtonStyle(bgcolor="#27272a", color="#ffffff")
        )

        caller_answer_tf = ft.TextField(
            label="Karşı Tarafın Cevabı (Answer Kodu)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            text_size=10,
        )

        p2p_connect_btn = ft.Button(
            content="3. Bağlan ve Görüşmeyi Başlat",
            icon=ft.Icons.PLAY_ARROW,
            width=300,
            style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff"),
            disabled=True
        )

        def generate_offer_click(e):
            p2p_gen_offer_btn.disabled = True
            caller_status_text.value = "ICE adayları toplanıyor (2-5 sn)..."
            caller_prog.visible = True
            page.update()

            async def _setup_offer():
                try:
                    config_servers = [
                        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                        RTCIceServer(urls=["stun:stun.cloudflare.com:3478"])
                    ]
                    config = RTCConfiguration(iceServers=config_servers)
                    pc = RTCPeerConnection(configuration=config)
                    state["active_pc"] = pc
                    state["call_role"] = "caller"
                    state["call_type"] = caller_call_type.value
                    state["call_partner"] = "Pure P2P Peer"

                    local_audio = MicrophoneTrack()
                    state["local_audio_track"] = local_audio
                    pc.addTrack(local_audio)

                    if state["call_type"] == "video":
                        local_video = CameraTrack()
                        state["local_video_track"] = local_video
                        pc.addTrack(local_video)
                        start_local_video_rendering()

                    @pc.on("track")
                    def on_track(track):
                        print(f"[VoIP] P2P Remote track: {track.kind}")
                        if track.kind == "audio":
                            player = AudioPlayer(track)
                            state["audio_player"] = player
                            player.start()
                        elif track.kind == "video":
                            start_remote_video_rendering(track)

                    @pc.on("iceconnectionstatechange")
                    async def on_iceconnectionstatechange():
                        print(f"[VoIP] P2P ICE state: {pc.iceConnectionState}")
                        if pc.iceConnectionState in ["connected", "completed"]:
                            state["call_state"] = "connected"
                            async def _start_ui():
                                dialog.open = False
                                show_call_screen()
                                call_status_text.value = "Connected"
                                page.update()
                            page.run_task(_start_ui)
                            page.run_task(_call_timer_loop)
                        elif pc.iceConnectionState in ["failed", "closed"]:
                            cleanup_call()

                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)

                    while pc.iceGatheringState != "complete":
                        await asyncio.sleep(0.05)

                    packed = pack_sdp(pc.localDescription.sdp, "offer", call_type=state["call_type"])

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
                        page.update()
                    page.run_task(_done)

                except Exception as ex:
                    print(f"P2P Offer setup error: {ex}")
                    async def _fail(msg=str(ex)):
                        caller_status_text.value = f"Hata: {msg}"
                        caller_prog.visible = False
                        p2p_gen_offer_btn.disabled = False
                        page.update()
                    page.run_task(_fail)
                    cleanup_call()

            asyncio.run_coroutine_threadsafe(_setup_offer(), state["ws_loop"])

        p2p_gen_offer_btn = ft.Button(
            content="1. Arama Teklifi (Offer) Üret",
            icon=ft.Icons.WIFI,
            on_click=generate_offer_click,
            width=300,
            style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff")
        )

        def connect_call_click(e):
            if not caller_answer_tf.value:
                caller_status_text.value = "Lütfen karşı tarafın cevap kodunu girin!"
                page.update()
                return

            caller_status_text.value = "Bağlanıyor..."
            page.update()

            async def _connect():
                try:
                    raw_answer = caller_answer_tf.value.strip()
                    unpacked = unpack_sdp(raw_answer)
                    remote_sdp = unpacked.get("sdp", "")
                    pc = state.get("active_pc")
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
                        page.update()
                    page.run_task(_fail)
                    cleanup_call()

            asyncio.run_coroutine_threadsafe(_connect(), state["ws_loop"])

        p2p_connect_btn.on_click = connect_call_click

        # Tab 2: Callee controls
        callee_offer_tf = ft.TextField(
            label="Karşı Tarafın Teklifi (Offer Kodu Yapıştırın)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            text_size=10,
        )

        callee_answer_tf = ft.TextField(
            label="Cevabınız (Answer Kodu)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            text_size=10,
        )

        callee_qr_image = ft.Image(src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", width=160, height=160, fit="contain", visible=False)
        callee_qr_container = ft.Container(
            content=ft.Column([
                ft.Text("QR Kod (Karşı tarafa taratın):", size=11, color="#888888"),
                callee_qr_image
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            visible=False,
            alignment=ft.Alignment(0, 0)
        )

        callee_status_text = ft.Text("", size=11, color="#8b5cf6")
        callee_prog = ft.ProgressBar(color="#8b5cf6", visible=False)

        callee_copy_btn = ft.Button(
            content="Cevabı Kopyala",
            icon=ft.Icons.COPY,
            on_click=lambda e: copy_to_clipboard(callee_answer_tf.value) if callee_answer_tf.value else None,
            disabled=True,
            style=ft.ButtonStyle(bgcolor="#27272a", color="#ffffff")
        )

        def generate_answer_click(e):
            if not callee_offer_tf.value:
                callee_status_text.value = "Lütfen önce teklif kodunu girin!"
                page.update()
                return

            p2p_gen_answer_btn.disabled = True
            callee_status_text.value = "Cevap hazırlanıyor (2-5 sn)..."
            callee_prog.visible = True
            page.update()

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
                    state["active_pc"] = pc
                    state["call_role"] = "callee"
                    state["call_type"] = call_type
                    state["call_partner"] = "Pure P2P Peer"

                    local_audio = MicrophoneTrack()
                    state["local_audio_track"] = local_audio
                    pc.addTrack(local_audio)

                    if call_type == "video":
                        local_video = CameraTrack()
                        state["local_video_track"] = local_video
                        pc.addTrack(local_video)
                        start_local_video_rendering()

                    @pc.on("track")
                    def on_track(track):
                        print(f"[VoIP] P2P Remote track: {track.kind}")
                        if track.kind == "audio":
                            player = AudioPlayer(track)
                            state["audio_player"] = player
                            player.start()
                        elif track.kind == "video":
                            start_remote_video_rendering(track)

                    @pc.on("iceconnectionstatechange")
                    async def on_iceconnectionstatechange():
                        print(f"[VoIP] P2P ICE state: {pc.iceConnectionState}")
                        if pc.iceConnectionState in ["connected", "completed"]:
                            state["call_state"] = "connected"
                            async def _start_ui():
                                dialog.open = False
                                show_call_screen()
                                call_status_text.value = "Connected"
                                page.update()
                            page.run_task(_start_ui)
                            page.run_task(_call_timer_loop)
                        elif pc.iceConnectionState in ["failed", "closed"]:
                            cleanup_call()

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
                        page.update()
                    page.run_task(_done)

                except Exception as ex:
                    print(f"P2P Answer setup error: {ex}")
                    async def _fail(msg=str(ex)):
                        callee_status_text.value = f"Hata: {msg}"
                        callee_prog.visible = False
                        p2p_gen_answer_btn.disabled = False
                        page.update()
                    page.run_task(_fail)
                    cleanup_call()

            asyncio.run_coroutine_threadsafe(_setup_answer(), state["ws_loop"])

        p2p_gen_answer_btn = ft.Button(
            content="2. Kabul Et ve Cevap (Answer) Üret",
            icon=ft.Icons.CHECK,
            on_click=generate_answer_click,
            width=300,
            style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff")
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
                    ft.Divider(color="#27272a", height=10),
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
            tabs=[
                ft.Tab(label="Arama Başlat (Caller)", content=caller_tab),
                ft.Tab(label="Aramaya Cevap Ver (Callee)", content=callee_tab),
            ],
            expand=True
        )

        def close_p2p(e):
            dialog.open = False
            page.update()
            if state.get("call_state") != "connected":
                cleanup_call()

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WIFI_TETHERING, color="#8b5cf6"),
                    ft.Text("Pure P2P (Sunucusuz Bağlantı)", size=16, color="#ffffff", weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color="#888888",
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
            bgcolor="#18181b",
        )

        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def refresh_inbox_and_messages():
        search_field.value = ""
        def do_refresh():
            fetch_offline_messages()
            run_on_ui(load_inbox_chats)
        threading.Thread(target=do_refresh, daemon=True).start()

    inbox_view = ft.Container(
        content=ft.Column(
            controls=[
                # Inbox App Bar
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.LOCK, size=20, color="#8b5cf6"),
                            ft.Column(
                                controls=[
                                    ft.Text("Chats", size=18,
                                             weight=ft.FontWeight.BOLD, color="#ffffff"),
                                    username_subtitle,
                                ],
                                spacing=0, tight=True,
                            ),
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.GROUP, icon_color="#8b5cf6",
                                icon_size=20, tooltip="Group Management",
                                on_click=lambda e: open_new_chat_dialog(e, default_tab_index=1),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.ROUTER, icon_color="#8b5cf6",
                                icon_size=20, tooltip="Pure P2P (Sunucusuz Arama)",
                                on_click=open_pure_p2p_dialog,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.REFRESH, icon_color="#8b5cf6",
                                icon_size=20, tooltip="Refresh",
                                on_click=lambda e: refresh_inbox_and_messages(),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.SETTINGS, icon_color="#8b5cf6",
                                icon_size=20, tooltip="Settings",
                                on_click=open_settings_dialog,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor="#18181b",
                    padding=ft.Padding(16, 10, 16, 10),
                    border=ft.Border(bottom=ft.BorderSide(1, "#27272a")),
                ),
                
                # Search Bar
                ft.Container(
                    content=search_field,
                    padding=ft.Padding(12, 6, 12, 6),
                    bgcolor="#18181b",
                    border=ft.Border(bottom=ft.BorderSide(1, "#27272a")),
                ),
                
                # Chat List
                ft.Container(content=inbox_list, expand=True, bgcolor="#09090b"),
                
                # Durum çubuğu
                ft.Container(
                    content=status_text,
                    padding=ft.Padding(16, 4, 16, 4),
                    bgcolor="#18181b",
                ),
            ],
            spacing=0, expand=True,
        ),
        expand=True,
    )

    # staged file controls
    staged_file_name_text = ft.Text("", size=12, color="#ffffff", weight=ft.FontWeight.BOLD)
    
    def remove_staged_file(e):
        state["staged_file"] = None
        staged_file_container.visible = False
        upload_progress.visible = False
        page.update()

    upload_progress = ft.ProgressBar(color="#8b5cf6", height=2, visible=False)

    staged_file_container = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.ATTACH_FILE, color="#8b5cf6", size=16),
                staged_file_name_text,
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_color="#ef4444",
                    icon_size=14,
                    on_click=remove_staged_file,
                    tooltip="Remove file",
                )
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor="#27272a",
        padding=ft.Padding(8, 4, 8, 4),
        border_radius=6,
        visible=False,
    )

    chat_view = ft.Container(
        content=ft.Column(
            controls=[
                # App Bar
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.IconButton(
                                icon=ft.Icons.ARROW_BACK, icon_color="#ffffff",
                                icon_size=20, on_click=lambda e: show_inbox_screen(),
                            ),
                            ft.Column(
                                controls=[
                                    chat_title_text,
                                    recipient_status_row,
                                ],
                                spacing=0, tight=True,
                            ),
                            ft.Container(expand=True),
                            ephemeral_btn,
                            call_icon_btn,
                            video_call_icon_btn,
                            ft.IconButton(
                                icon=ft.Icons.COPY, icon_color="#8b5cf6",
                                icon_size=20, tooltip="Copy Contact Card",
                                on_click=copy_public_key,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.REFRESH, icon_color="#8b5cf6",
                                icon_size=20, tooltip="Fetch Offline Messages",
                                on_click=lambda e: threading.Thread(target=fetch_offline_messages, daemon=True).start(),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor="#18181b",
                    padding=ft.Padding(16, 10, 16, 10),
                    border=ft.Border(bottom=ft.BorderSide(1, "#27272a")),
                ),

                # Chat listesi
                ft.Container(content=chat_list, expand=True, bgcolor="#09090b"),

                # Durum çubuğu
                ft.Container(
                    content=status_text,
                    padding=ft.Padding(16, 4, 16, 4),
                    bgcolor="#18181b",
                ),

                # Mesaj giriş alanı — view-once + attach + send
                ft.Container(
                    content=ft.Column(
                        controls=[
                            staged_file_container,
                            upload_progress,
                            ft.Row(
                                controls=[
                                    view_once_msg_btn,
                                    attach_btn,
                                    message_input,
                                    ft.FloatingActionButton(
                                        icon=ft.Icons.SEND_ROUNDED, bgcolor="#8b5cf6",
                                        mini=True, on_click=on_send_click,
                                        tooltip="Send (E2EE)",
                                    ),
                                ],
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.END,
                            ),
                        ],
                        spacing=6,
                        tight=True,
                    ),
                    bgcolor="#18181b",
                    padding=ft.Padding(12, 10, 12, 10),
                    border=ft.Border(top=ft.BorderSide(1, "#27272a")),
                ),
            ],
            spacing=0, expand=True,
        ),
        expand=True,
    )

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                    VOIP (ARAMA) EKRANI & METOTLARI              ║
    # ╚═══════════════════════════════════════════════════════════════╝

    # VoIP Arayüz Elemanları
    call_avatar = ft.CircleAvatar(
        content=ft.Text("?", size=40, color="#ffffff"),
        radius=60,
        bgcolor="#8b5cf6",
    )
    call_name_text = ft.Text("Username", size=24, weight=ft.FontWeight.BOLD, color="#ffffff")
    call_status_text = ft.Text("Calling...", size=14, color="#a1a1aa")
    call_timer_text = ft.Text("00:00", size=14, color="#8b5cf6", visible=False)

    transparent_placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    local_video_preview = ft.Image(src=transparent_placeholder, width=100, height=140, fit="cover", border_radius=8, visible=False, right=10, bottom=10)
    remote_video_view = ft.Image(src=transparent_placeholder, fit="contain", visible=False)

    video_container = ft.Stack(
        controls=[
            remote_video_view,
            local_video_preview
        ],
        expand=True,
        visible=False
    )

    mic_btn = ft.IconButton(
        icon=ft.Icons.MIC,
        icon_color="#ffffff",
        bgcolor="#27272a",
        on_click=lambda e: toggle_call_mic(),
        tooltip="Mute Microphone"
    )
    cam_btn = ft.IconButton(
        icon=ft.Icons.VIDEOCAM,
        icon_color="#ffffff",
        bgcolor="#27272a",
        on_click=lambda e: toggle_call_cam(),
        tooltip="Toggle Video"
    )
    end_btn = ft.IconButton(
        icon=ft.Icons.CALL_END,
        icon_color="#ffffff",
        bgcolor="#ef4444",
        icon_size=28,
        width=56,
        height=56,
        on_click=lambda e: hangup_call_clicked(),
        tooltip="End Call"
    )

    accept_btn = ft.IconButton(
        icon=ft.Icons.CALL,
        icon_color="#ffffff",
        bgcolor="#22c55e",
        icon_size=28,
        width=56,
        height=56,
        on_click=lambda e: accept_call_clicked(),
        tooltip="Answer Call"
    )
    decline_btn = ft.IconButton(
        icon=ft.Icons.CALL_END,
        icon_color="#ffffff",
        bgcolor="#ef4444",
        icon_size=28,
        width=56,
        height=56,
        on_click=lambda e: decline_call_clicked(),
        tooltip="Decline Call"
    )

    caller_controls_row = ft.Row(
        controls=[mic_btn, end_btn, cam_btn],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=20
    )

    callee_controls_row = ft.Row(
        controls=[decline_btn, accept_btn],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=40
    )

    call_controls_container = ft.Container(
        content=caller_controls_row,
        padding=ft.Padding(0, 20, 0, 40)
    )

    call_view = ft.Container(
        content=ft.Column(
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Container(height=40),
                            ft.Row(
                                controls=[call_avatar],
                                alignment=ft.MainAxisAlignment.CENTER
                            ),
                            ft.Container(height=10),
                            ft.Row(
                                controls=[call_name_text],
                                alignment=ft.MainAxisAlignment.CENTER
                            ),
                            ft.Row(
                                controls=[call_status_text, call_timer_text],
                                alignment=ft.MainAxisAlignment.CENTER,
                                spacing=10
                            ),
                            ft.Container(height=20),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor="#18181b",
                    border_radius=ft.BorderRadius(bottom_left=24, bottom_right=24, top_left=0, top_right=0),
                    shadow=ft.BoxShadow(blur_radius=15, color="#000000aa")
                ),
                ft.Container(
                    content=video_container,
                    expand=True,
                    alignment=ft.Alignment(0, 0),
                ),
                call_controls_container
            ],
            spacing=0,
            expand=True
        ),
        bgcolor="#09090b",
        expand=True
    )

    # VoIP İş Mantığı Metotları

    def show_call_screen():
        fab.visible = False
        call_avatar.content.value = state["call_partner"][:1].upper() if state["call_partner"] else "?"
        call_name_text.value = state["call_partner"]

        # Reset button states
        mic_btn.icon = ft.Icons.MIC
        mic_btn.icon_color = "#ffffff"
        mic_btn.bgcolor = "#27272a"
        cam_btn.icon = ft.Icons.VIDEOCAM
        cam_btn.icon_color = "#ffffff"
        cam_btn.bgcolor = "#27272a"

        if state["call_state"] == "ringing":
            call_status_text.value = f"Incoming {state['call_type']} call..."
            call_controls_container.content = callee_controls_row
            video_container.visible = False
            call_avatar.visible = True
            call_timer_text.visible = False
        elif state["call_state"] == "calling":
            call_status_text.value = "Calling..."
            call_controls_container.content = caller_controls_row
            video_container.visible = state["call_type"] == "video"
            call_avatar.visible = state["call_type"] == "audio"
            call_timer_text.visible = False
        elif state["call_state"] == "connected":
            call_status_text.value = "Connected"
            call_controls_container.content = caller_controls_row
            video_container.visible = state["call_type"] == "video"
            call_avatar.visible = state["call_type"] == "audio"
            call_timer_text.visible = True

        page.controls.clear()
        page.add(call_view)
        page.update()

    def start_voip_call(video: bool):
        if not is_ws_connected():
            log_status("HATA: WebSocket bağlı değil, arama yapılamaz!")
            return
            
        call_id = str(uuid_lib.uuid4())
        state["active_call_id"] = call_id
        state["call_role"] = "caller"
        state["call_partner"] = state["recipient"]
        state["call_type"] = "video" if video else "audio"
        state["call_state"] = "calling"
        
        show_call_screen()
        
        async def _setup():
            try:
                try:
                    r = signed_get("/api/ice_servers")
                    if r.status_code == 200:
                        ice_data = r.json().get("ice_servers", [])
                    else:
                        ice_data = [{"urls": "stun:stun.l.google.com:19302"}]
                except Exception:
                    ice_data = [{"urls": "stun:stun.l.google.com:19302"}]

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
                state["active_pc"] = pc
                
                local_audio = MicrophoneTrack()
                state["local_audio_track"] = local_audio
                pc.addTrack(local_audio)
                
                if video:
                    local_video = CameraTrack()
                    state["local_video_track"] = local_video
                    pc.addTrack(local_video)
                    start_local_video_rendering()
                    
                @pc.on("track")
                def on_track(track):
                    print(f"[VoIP] Remote track received: {track.kind}")
                    if track.kind == "audio":
                        player = AudioPlayer(track)
                        state["audio_player"] = player
                        player.start()
                    elif track.kind == "video":
                        start_remote_video_rendering(track)
                        
                @pc.on("iceconnectionstatechange")
                async def on_iceconnectionstatechange():
                    print(f"[VoIP] ICE connection state: {pc.iceConnectionState}")
                    if pc.iceConnectionState in ["connected", "completed"]:
                        if call_status_text.value != "Connected":
                            state["call_state"] = "connected"
                            async def _start_call_ui():
                                call_status_text.value = "Connected"
                                page.update()
                            page.run_task(_start_call_ui)
                            page.run_task(_call_timer_loop)
                    elif pc.iceConnectionState in ["failed", "closed"]:
                        cleanup_call()
                        
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
                
                while pc.iceGatheringState != "complete":
                    await asyncio.sleep(0.05)
                    
                send_ws_message_with_fallback({
                    "type": "call_offer",
                    "recipient": state["call_partner"],
                    "call_id": state["active_call_id"],
                    "call_type": state["call_type"],
                    "sdp_offer": pc.localDescription.sdp
                })
                
            except Exception as ex:
                print(f"[VoIP] Setup error: {ex}")
                import traceback
                traceback.print_exc()
                cleanup_call()
                
        asyncio.run_coroutine_threadsafe(_setup(), state["ws_loop"])

    def accept_call_clicked():
        if state.get("call_state") != "ringing":
            return
            
        state["call_state"] = "connected"
        call_status_text.value = "Connecting..."
        call_controls_container.content = caller_controls_row
        page.update()
        
        async def _accept():
            try:
                try:
                    r = signed_get("/api/ice_servers")
                    if r.status_code == 200:
                        ice_data = r.json().get("ice_servers", [])
                    else:
                        ice_data = [{"urls": "stun:stun.l.google.com:19302"}]
                except Exception:
                    ice_data = [{"urls": "stun:stun.l.google.com:19302"}]

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
                state["active_pc"] = pc
                
                local_audio = MicrophoneTrack()
                state["local_audio_track"] = local_audio
                pc.addTrack(local_audio)
                
                video = (state["call_type"] == "video")
                if video:
                    local_video = CameraTrack()
                    state["local_video_track"] = local_video
                    pc.addTrack(local_video)
                    start_local_video_rendering()
                    
                @pc.on("track")
                def on_track(track):
                    print(f"[VoIP] Remote track received: {track.kind}")
                    if track.kind == "audio":
                        player = AudioPlayer(track)
                        state["audio_player"] = player
                        player.start()
                    elif track.kind == "video":
                        start_remote_video_rendering(track)
                        
                @pc.on("iceconnectionstatechange")
                async def on_iceconnectionstatechange():
                    print(f"[VoIP] ICE connection state: {pc.iceConnectionState}")
                    if pc.iceConnectionState in ["connected", "completed"]:
                        if call_status_text.value != "Connected":
                            state["call_state"] = "connected"
                            async def _start_call_ui():
                                call_status_text.value = "Connected"
                                page.update()
                            page.run_task(_start_call_ui)
                            page.run_task(_call_timer_loop)
                    elif pc.iceConnectionState in ["failed", "closed"]:
                        cleanup_call()
                        
                await pc.setRemoteDescription(RTCSessionDescription(
                    sdp=state["remote_sdp"],
                    type="offer"
                ))
                
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                
                while pc.iceGatheringState != "complete":
                    await asyncio.sleep(0.05)
                    
                send_ws_message_with_fallback({
                    "type": "call_answer",
                    "recipient": state["call_partner"],
                    "call_id": state["active_call_id"],
                    "sdp_answer": pc.localDescription.sdp
                })
                
            except Exception as ex:
                print(f"[VoIP] Error accepting call: {ex}")
                cleanup_call()
                
        asyncio.run_coroutine_threadsafe(_accept(), state["ws_loop"])

    def decline_call_clicked():
        if state.get("active_call_id") and state.get("call_partner"):
            send_ws_message_with_fallback({
                "type": "call_reject",
                "recipient": state["call_partner"],
                "call_id": state["active_call_id"],
                "reason": "rejected"
            })
        cleanup_call()

    def hangup_call_clicked():
        if state.get("active_call_id") and state.get("call_partner"):
            send_ws_message_with_fallback({
                "type": "call_end",
                "recipient": state["call_partner"],
                "call_id": state["active_call_id"],
                "duration_seconds": state.get("call_duration", 0)
            })
        cleanup_call()

    def toggle_call_mic():
        track = state.get("local_audio_track")
        if track:
            track.enabled = not track.enabled
            mic_btn.icon = ft.Icons.MIC if track.enabled else ft.Icons.MIC_OFF
            mic_btn.icon_color = "#ffffff" if track.enabled else "#ef4444"
            mic_btn.bgcolor = "#27272a" if track.enabled else "#2d1b1f"
            page.update()

    def toggle_call_cam():
        track = state.get("local_video_track")
        if track:
            track.enabled = not track.enabled
            cam_btn.icon = ft.Icons.VIDEOCAM if track.enabled else ft.Icons.VIDEOCAM_OFF
            cam_btn.icon_color = "#ffffff" if track.enabled else "#ef4444"
            cam_btn.bgcolor = "#27272a" if track.enabled else "#2d1b1f"
            local_video_preview.visible = track.enabled
            page.update()

    async def _call_timer_loop():
        def _init_timer():
            state["call_duration"] = 0
            call_timer_text.value = "00:00"
            call_timer_text.visible = True
            page.update()
        run_on_ui(_init_timer)
        while state.get("call_state") == "connected":
            await asyncio.sleep(1)
            state["call_duration"] += 1
            mins = state["call_duration"] // 60
            secs = state["call_duration"] % 60
            def _update_timer_ui(m=mins, s=secs):
                call_timer_text.value = f"{m:02d}:{s:02d}"
                page.update()
            run_on_ui(_update_timer_ui)

    def start_local_video_rendering():
        async def _init_local_ui():
            local_video_preview.visible = True
            page.update()
        page.run_task(_init_local_ui)
        
        async def _render_local():
            track = state.get("local_video_track")
            while track and track.running and state.get("call_state") != "ended":
                try:
                    frame = await track.recv()
                    img = frame.to_ndarray(format='bgr24')
                    _, buffer = cv2.imencode('.jpg', img)
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    def _update_local(b=b64_str):
                        local_video_preview.src_base64 = b
                        page.update()
                    run_on_ui(_update_local)
                except Exception as e:
                    break
                    
        page.run_task(_render_local)

    def start_remote_video_rendering(track):
        async def _init_remote_ui():
            remote_video_view.visible = True
            call_avatar.visible = False
            video_container.visible = True
            page.update()
        page.run_task(_init_remote_ui)
        
        async def _render_remote():
            while state.get("call_state") != "ended":
                try:
                    frame = await track.recv()
                    img = frame.to_ndarray(format='bgr24')
                    _, buffer = cv2.imencode('.jpg', img)
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    def _update_remote(b=b64_str):
                        remote_video_view.src_base64 = b
                        page.update()
                    run_on_ui(_update_remote)
                except Exception as e:
                    break
                    
        page.run_task(_render_remote)

    def cleanup_call():
        print("[VoIP] Cleaning up call...")
        state["call_state"] = "ended"
        
        if state.get("local_audio_track"):
            try:
                state["local_audio_track"].stop()
            except Exception as e:
                pass
            state["local_audio_track"] = None
            
        if state.get("local_video_track"):
            try:
                state["local_video_track"].stop()
            except Exception as e:
                pass
            state["local_video_track"] = None

        if state.get("audio_player"):
            try:
                state["audio_player"].stop()
            except Exception as e:
                pass
            state["audio_player"] = None

        pc = state.get("active_pc")
        if pc:
            try:
                asyncio.run_coroutine_threadsafe(pc.close(), state["ws_loop"])
            except Exception as e:
                pass
            state["active_pc"] = None

        state["active_call_id"] = None
        state["call_role"] = None
        state["call_partner"] = None
        state["call_type"] = None
        
        async def _cleanup_ui():
            local_video_preview.src_base64 = None
            local_video_preview.src = transparent_placeholder
            local_video_preview.visible = False
            remote_video_view.src_base64 = None
            remote_video_view.src = transparent_placeholder
            remote_video_view.visible = False
            call_timer_text.visible = False
            
            if state.get("logged_in", False):
                show_chat_screen()
            else:
                show_login_screen()
                
        page.run_task(_cleanup_ui)

    # ── Ekran Geçişleri ──────────────────────────────────────────────

    def show_login_screen():
        fab.visible = False
        page.controls.clear()
        page.add(login_view)
        page.update()

    def show_chat_screen():
        fab.visible = False
        if state["recipient"]:
            if state.get("is_group", False):
                chat_info = state["store"].get_chat_info(state["recipient"])
                display_name = chat_info.get("partner", state["recipient"])
                chat_title_text.value = f"Group: {display_name}"
                call_icon_btn.visible = False
                video_call_icon_btn.visible = False
            else:
                chat_title_text.value = f"Chat: {state['recipient']}"
                call_icon_btn.visible = True
                video_call_icon_btn.visible = True
        else:
            chat_title_text.value = "No active chat"
            call_icon_btn.visible = False
            video_call_icon_btn.visible = False
            
        username_text.value = f"User: {state['username']}"
        refresh_recipient_status()
        page.controls.clear()
        page.add(chat_view)
        page.update()

    def show_inbox_screen():
        state["recipient"] = None
        state["is_group"] = False
        search_field.value = ""
        fab.visible = True
        load_inbox_chats()
        page.controls.clear()
        page.add(inbox_view)
        page.update()

    show_login_screen()


if __name__ == "__main__":
    ft.run(main)
