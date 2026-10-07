"""desktop/tls_pin.py — Kendinden imzalı sunucu sertifikasını parmak iziyle sabitleme.

Alan adı OLMAYAN kurulumda (deploy/gen_cert.py) sunucunun sertifikasını hiçbir
sertifika otoritesi imzalamaz; güven, kullanıcının sunucu yöneticisinden güvenli
bir kanalla aldığı SHA-256 parmak izinden gelir.

Akış (kontrol atlanamaz):
  1. Sunucunun sertifikası BİR KEZ doğrulamasız çekilir ve parmak izi beklenenle
     karşılaştırılır. Eşleşmezse PinMismatch — hiçbir uygulama verisi gönderilmez.
  2. Eşleşen sertifika diske yazılır ve sonraki TÜM bağlantılarda (REST ve
     WebSocket) TEK güvenilen sertifika olarak kullanılır. Böylece kontrolü TLS
     el sıkışmasının kendisi yapar: sunucu başka bir sertifika sunarsa bağlantı,
     istek gönderilmeden kurulamaz.
  3. Parmak izi adresle birlikte hatırlanır; sonraki girişlerde yeniden yazmak
     gerekmez. Sertifika değişirse bağlantı reddedilir (olası MITM).
"""

import hashlib
import json
import re
import socket
import ssl
from pathlib import Path


class PinMismatch(Exception):
    """Sunucunun sunduğu sertifika sabitlenen parmak iziyle eşleşmiyor."""

    def __init__(self, expected: str, seen: str):
        self.expected, self.seen = expected, seen
        super().__init__(
            "Sunucu sertifikasının parmak izi beklenenle EŞLEŞMİYOR — bağlantı kesildi. "
            "Araya giren biri (MITM) olabilir ya da sunucu sertifikası değişmiş olabilir; "
            "sunucu yöneticisine güvenli bir kanaldan danışın.\n"
            f"Beklenen: {expected}\nGörülen:  {seen}")


def _store_dir() -> Path:
    from crypto_utils import KEYS_DIR
    return KEYS_DIR / "tls"


def normalize_fingerprint(text: str) -> str:
    """'ab:cd …', 'AB CD …', 'abcd…' → 'AB:CD:…' (SHA-256, 32 bayt). Geçersizse ValueError."""
    hexonly = re.sub(r"[\s:]", "", text or "").upper()
    if not re.fullmatch(r"[0-9A-F]{64}", hexonly):
        raise ValueError("Parmak izi 64 onaltılık karakterli bir SHA-256 değeri olmalı "
                         "(ör. AB:CD:…, 'docker compose logs certgen' çıktısındaki gibi).")
    return ":".join(hexonly[i:i + 2] for i in range(0, 64, 2))


def fingerprint_of(der: bytes) -> str:
    return normalize_fingerprint(hashlib.sha256(der).hexdigest())


def fetch_server_cert(host: str, port: int, timeout: float = 8.0) -> bytes:
    """Sunucunun sertifikasını (DER) doğrulamasız çeker. Yalnızca parmak izi
    karşılaştırması içindir; bu bağlantı üzerinden hiçbir veri gönderilmez."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def _key(host: str, port: int) -> str:
    return f"{host}:{port}"


def _pins_path() -> Path:
    return _store_dir() / "pins.json"


def _load_pins() -> dict:
    try:
        return json.loads(_pins_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remembered_pin(host: str, port: int) -> str | None:
    return _load_pins().get(_key(host, port))


def _cert_path(host: str, port: int) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", _key(host, port))
    return _store_dir() / f"{safe}.pem"


def pin_server(host: str, port: int, expected: str | None = None) -> Path:
    """Sunucuyu parmak iziyle sabitler ve güvenilen sertifikanın yolunu döner.

    expected verilmezse daha önce hatırlanan parmak izi kullanılır; o da yoksa
    LookupError (çağıran taraf normal CA doğrulamasına döner).
    """
    pin = normalize_fingerprint(expected) if expected else remembered_pin(host, port)
    if not pin:
        raise LookupError("bu sunucu için sabitlenmiş parmak izi yok")
    der = fetch_server_cert(host, port)
    seen = fingerprint_of(der)
    if seen != pin:
        raise PinMismatch(pin, seen)

    store = _store_dir()
    store.mkdir(parents=True, exist_ok=True)
    path = _cert_path(host, port)
    path.write_text(ssl.DER_cert_to_PEM_cert(der), encoding="ascii")
    pins = _load_pins()
    pins[_key(host, port)] = pin
    tmp = _pins_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(pins, indent=2), encoding="utf-8")
    tmp.replace(_pins_path())
    return path
