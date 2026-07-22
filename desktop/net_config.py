"""desktop/net_config.py — Sunucu adresi ve dosya tipi yardımcıları.

client.py'den taşındı (modülerleştirme): SERVER_HOST/PORT, BASE_URL, WS_URL,
update_server_urls, FILE_ICONS, IMAGE_EXTENSIONS, _guess_file_type.
"""

import mimetypes
from pathlib import Path

import flet as ft

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
