"""desktop/avatar.py — E2EE profil fotoğrafı: işleme, yerel saklama, imza verisi.

Akış:
  • Kendi fotoğrafın: FilePicker → normalize() → 128x128 JPEG → yerelde
    ~/.hybridp2p_messenger/{user}/avatar.jpg
  • Dağıtım: her kişiye kendi public key'iyle encrypt_bytes + RSA-PSS imza,
    `avatar_update` WS çerçevesiyle. Sunucu fotoğrafı göremez.
  • Alma: imza ZORUNLU (yeni tip, eski istemci yok); çözülen bayt normalize()
    ile yeniden kodlanır — gelen dosya diske asla ham yazılmaz.
"""

import base64
import hashlib
import io

from PIL import Image

SIZE = 128
MAX_INPUT_BYTES = 15 * 1024 * 1024       # seçilen dosya için üst sınır
MAX_RECEIVED_BYTES = 256 * 1024          # karşıdan gelen (zaten 128px olmalı)
# Decompression bomb koruması: küçük dosya, devasa piksel sayısına açılamaz
Image.MAX_IMAGE_PIXELS = 40_000_000


def normalize(raw: bytes, max_bytes: int = MAX_INPUT_BYTES) -> bytes:
    """Herhangi bir resmi ortadan kare kırpıp 128x128 JPEG'e çevirir.

    Geçersiz/aşırı büyük girdide ValueError fırlatır. Yeniden kodlama, olası
    gömülü veriyi ve metadata'yı (EXIF konum vb.) da atar.
    """
    if not raw or len(raw) > max_bytes:
        raise ValueError("resim boş ya da çok büyük")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as ex:
        raise ValueError(f"geçersiz resim: {ex}") from ex

    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue()


def to_data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def digest(jpeg: bytes) -> str:
    return hashlib.sha256(jpeg).hexdigest()


def signed_data(sender: str, recipient: str, encrypted_payload: str) -> bytes:
    """İmzalanan veri. "avatar:" alan ayırıcısı, bir avatar imzasının birebir
    mesaj imzası ({sender}:{recipient}:{payload}) yerine kullanılmasını önler."""
    return f"avatar:{sender}:{recipient}:{encrypted_payload}".encode("utf-8")


def _own_path(username: str):
    from message_store import KEYS_DIR
    return KEYS_DIR / username / "avatar.jpg"


def save_own(username: str, jpeg: bytes):
    p = _own_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(jpeg)


def load_own(username: str) -> bytes | None:
    p = _own_path(username)
    return p.read_bytes() if p.exists() else None
