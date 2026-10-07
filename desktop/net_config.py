"""desktop/net_config.py — Sunucu adresi ve dosya tipi yardımcıları.

client.py'den taşındı (modülerleştirme): SERVER_HOST/PORT, BASE_URL, WS_URL,
update_server_urls, FILE_ICONS, IMAGE_EXTENSIONS, _guess_file_type.
"""

import mimetypes
from pathlib import Path

import flet as ft

# ── Sunucu Ayarları ─────────────────────────────────────────────────
# Adres biçimleri (giriş ekranı):
#   127.0.0.1:8000              → http / ws   (eski biçim, geliştirme)
#   https://mesaj.example.com   → https / wss, port 443 (alan adı + Let's Encrypt)
#   https://203.0.113.5         → https / wss + parmak izi sabitleme (yalnızca IP modu)
#   sunucu:443                  → https / wss (443 her zaman TLS kabul edilir)
SERVER_HOST = "127.0.0.1"
SERVER_PORT  = 8000
USE_TLS      = False
BASE_URL     = f"http://{SERVER_HOST}:{SERVER_PORT}"
WS_URL       = f"ws://{SERVER_HOST}:{SERVER_PORT}"
# requests'in verify= değeri: True (sistem CA'ları) ya da sabitlenen sertifikanın yolu
TLS_VERIFY   = True
PINNED_CERT  = None


def parse_server_address(text: str) -> tuple[bool, str, int]:
    """Giriş metnini (tls_mi, host, port) olarak çözer."""
    text = (text or "").strip() or "127.0.0.1:8000"
    scheme = None
    if "://" in text:
        scheme, text = text.split("://", 1)
        scheme = scheme.lower()
        if scheme not in ("http", "https", "ws", "wss"):
            raise ValueError(f"Desteklenmeyen adres şeması: {scheme}://")
    text = text.split("/", 1)[0]
    if text.startswith("["):                      # [IPv6]:port
        host, _, rest = text[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, port_text = text.split(":", 1)
    else:
        host, port_text = text, ""
    if not host:
        raise ValueError("Sunucu adresi boş.")
    if port_text and not port_text.isdigit():
        raise ValueError(f"Geçersiz port: {port_text}")

    if scheme in ("https", "wss"):
        tls = True
    elif scheme in ("http", "ws"):
        tls = False
    else:
        tls = port_text == "443"                   # şemasız: yalnızca 443 TLS
    port = int(port_text) if port_text else (443 if tls else 8000)
    return tls, host, port


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def update_server_urls(host_port_str: str, pin: str = ""):
    """Sunucu adresini (ve varsa TLS parmak izini) uygular.

    Ağ işlemi yapabilir (parmak izi doğrulaması) — arayüz iş parçacığından değil,
    arka plan iş parçacığından çağırın. Hata durumunda anlaşılır bir istisna atar.
    """
    global BASE_URL, WS_URL, SERVER_HOST, SERVER_PORT, USE_TLS, TLS_VERIFY, PINNED_CERT
    from desktop import tls_pin

    tls, host, port = parse_server_address(host_port_str)
    pin = (pin or "").strip()
    if pin and not tls:
        raise ValueError("Sertifika parmak izi yalnızca https:// adresleriyle kullanılabilir.")

    pinned = None
    if tls:
        try:
            pinned = tls_pin.pin_server(host, port, pin or None)
        except LookupError:
            pinned = None                          # parmak izi yok → normal CA doğrulaması
    SERVER_HOST, SERVER_PORT, USE_TLS = host, port, tls
    PINNED_CERT = pinned
    TLS_VERIFY = str(pinned) if pinned else True
    http_scheme, ws_scheme = ("https", "wss") if tls else ("http", "ws")
    default_port = 443 if tls else None
    netloc = _url_host(host) + ("" if port == default_port else f":{port}")
    BASE_URL = f"{http_scheme}://{netloc}"
    WS_URL = f"{ws_scheme}://{netloc}"


def ws_ssl_context():
    """websockets.connect için ssl argümanı: düz ws'de None; sabitlemede yalnızca
    o sertifikaya güvenen bağlam; aksi halde sistem CA'ları."""
    import ssl
    if not USE_TLS:
        return None
    if PINNED_CERT:
        return ssl.create_default_context(cafile=str(PINNED_CERT))
    return ssl.create_default_context()


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
