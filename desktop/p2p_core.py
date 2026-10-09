"""desktop/p2p_core.py — Sunucusuz ("telsiz") modun arayüzden bağımsız çekirdeği.

İki cihaz aynı anda çevrimiçiyken, aralarında hiçbir sunucu olmadan doğrudan
bağlanır (WebRTC). Bağlantı bilgisi (SDP) kullanıcıların kendi taşıdığı bir
"bağlantı kodu"yla değiş tokuş edilir: kopyala-yapıştır ya da QR. Kodlar tek
kullanımlıktır (her oturumda yeni şifreleme anahtarları üretilir).

GÜVENLİK MODELİ
  Kodu taşıyan kanal (SMS, başka bir uygulama…) güvenilir DEĞİLDİR. Araya giren
  biri kodu kendi koduyla değiştirirse, iki taraf da farkında olmadan onunla
  konuşurdu. Bunu önlemek için kod (sürüm "h2") gönderenin KİMLİK anahtarıyla
  imzalanır ve imza SDP'nin tamamını kapsar — SDP, bağlantının DTLS şifreleme
  parmak izini içerdiği için imza, şifreli kanalı bu kişiye bağlar. Cevap kodu
  ayrıca hangi teklife cevap verdiğini (teklifin özeti) imzalar.

  Karşı taraf rehberdeyse anahtarı rehberdekiyle karşılaştırılır:
    VERIFIED     — imza geçerli, anahtar rehberdekiyle aynı
    NEW          — imza geçerli, kişi rehberde yok (parmak izi gösterilir; TOFU)
    KEY_CHANGED  — rehberdeki anahtardan FARKLI (olası MITM) → engellenir
    INVALID      — imza geçersiz / alanlar bozuk → engellenir
    LEGACY       — eski (z1/v1) kod, kimlik yok → yalnızca uyarıyla, yalnızca arama

  Bağlantı kurulduktan sonra veri kanalındaki mesajlar DTLS ile uçtan uca
  şifrelidir; ek uygulama katmanı şifrelemesi gerekmez.
"""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization

from crypto_utils import (
    get_public_key_fingerprint,
    pem_string_to_public_key,
    public_key_to_pem_string,
    sign_data,
    verify_signature,
)

PREFIX_V2 = "h2:"
MODES = ("chat", "audio", "video")
MAX_CODE_CHARS = 16_000            # yapıştırılan kodun üst sınırı (bozuk/kötü niyetli girdi)
MAX_CODE_AGE_SEC = 24 * 3600       # bayat kodlar reddedilir
MAX_CLOCK_SKEW_SEC = 15 * 60       # karşı tarafın saati ileride olabilir
CHANNEL_LABEL = "hp2p"             # veri kanalı adı (sohbet + dosya)
MAX_TEXT_CHARS = 8000
FILE_FRAME_TYPES = ("file_offer", "file_accept", "file_reject", "file_end", "file_cancel")
MAX_FRAME_BYTES = 256 * 1024

VERIFIED, NEW, KEY_CHANGED, INVALID, LEGACY = "verified", "new", "key_changed", "invalid", "legacy"


class P2PCodeError(ValueError):
    """Kod çözülemedi / biçim hatalı."""


@dataclass
class PeerIdentity:
    status: str
    username: str = ""
    public_key_pem: str = ""
    fingerprint: str = ""
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.status in (KEY_CHANGED, INVALID)

    @property
    def can_save_history(self) -> bool:
        return self.status == VERIFIED


@dataclass
class Envelope:
    type: str            # "offer" | "answer"
    mode: str            # "chat" | "audio" | "video"
    sdp: str
    version: int = 2
    username: str = ""
    public_key_pem: str = ""
    ts: int = 0
    nonce: str = ""
    offer_hash: str = ""
    signature: str = ""


# ─────────────────────────── yardımcılar ───────────────────────────

def sdp_hash(sdp: str) -> str:
    return hashlib.sha256(sdp.encode("utf-8")).hexdigest()


def _der(pub_or_pem) -> bytes:
    pub = pem_string_to_public_key(pub_or_pem) if isinstance(pub_or_pem, str) else pub_or_pem
    return pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _signed_data(env: Envelope) -> bytes:
    # Alan ayırıcılı ve sürümlü: başka bir imza türüyle karıştırılamaz
    return (f"hybridp2p-p2p:v2:{env.type}:{env.mode}:{env.username}:{env.ts}:"
            f"{env.nonce}:{env.offer_hash}:{sdp_hash(env.sdp)}").encode("utf-8")


# ─────────────────────────── kod üretme / çözme ───────────────────────────

def make_envelope(sdp_type: str, mode: str, sdp: str, username: str, private_key, public_key,
                  offer_sdp: str = "") -> str:
    """İmzalı, sıkıştırılmış bağlantı kodu (h2:…) üretir."""
    if sdp_type not in ("offer", "answer") or mode not in MODES:
        raise ValueError("geçersiz tür/mod")
    env = Envelope(type=sdp_type, mode=mode, sdp=sdp, username=username,
                   public_key_pem=public_key_to_pem_string(public_key),
                   ts=int(time.time()), nonce=secrets.token_hex(8),
                   offer_hash=sdp_hash(offer_sdp) if sdp_type == "answer" else "")
    env.signature = base64.b64encode(sign_data(private_key, _signed_data(env))).decode("ascii")
    payload = {
        "v": 2, "t": env.type, "m": env.mode, "s": env.sdp, "u": env.username,
        "k": base64.b64encode(_der(public_key)).decode("ascii"),
        "ts": env.ts, "n": env.nonce, "oh": env.offer_hash, "sig": env.signature,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return PREFIX_V2 + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")


def _decompress_limited(data: bytes, limit: int = 256 * 1024) -> bytes:
    d = zlib.decompressobj()
    out = d.decompress(data, limit)
    if d.unconsumed_tail:
        raise P2PCodeError("kod çok büyük")      # sıkıştırma bombası koruması
    return out


def parse_code(code: str) -> Envelope:
    """Bağlantı kodunu çözer (h2 ve eski z1/v1 biçimleri). İmzayı DOĞRULAMAZ —
    bunun için identify_peer() kullanın."""
    code = "".join((code or "").split())          # QR/kopyalamada araya giren boşluklar
    if not code:
        raise P2PCodeError("kod boş")
    if len(code) > MAX_CODE_CHARS:
        raise P2PCodeError("kod çok uzun")
    try:
        if code.startswith(PREFIX_V2):
            p = json.loads(_decompress_limited(base64.urlsafe_b64decode(code[len(PREFIX_V2):])))
            if p.get("v") != 2:
                raise P2PCodeError("desteklenmeyen kod sürümü")
            der = base64.b64decode(p["k"])
            pem = serialization.load_der_public_key(der).public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
            return Envelope(type=p["t"], mode=p["m"], sdp=p["s"], version=2, username=p["u"],
                            public_key_pem=pem, ts=int(p["ts"]), nonce=p["n"],
                            offer_hash=p.get("oh", ""), signature=p["sig"])
        # Eski biçim (imzasız): z1 = zlib+base64, v1 = base64
        if code.startswith("z1:"):
            p = json.loads(_decompress_limited(base64.b64decode(code[3:])))
        elif code.startswith("v1:"):
            p = json.loads(base64.b64decode(code[3:]))
        else:
            raise P2PCodeError("tanınmayan kod biçimi")
        mode = p.get("call_type", "audio")
        return Envelope(type=p["type"], mode=mode if mode in MODES else "audio", sdp=p["sdp"], version=1)
    except P2PCodeError:
        raise
    except Exception as ex:
        raise P2PCodeError(f"kod çözülemedi: {ex}") from ex


def identify_peer(env: Envelope, contact_lookup, expected_type: str, own_offer_sdp: str = "",
                  now: float | None = None) -> PeerIdentity:
    """Koddaki kimliği doğrular ve rehberle karşılaştırır.

    contact_lookup(username) → rehberdeki PEM ya da None.
    expected_type: bu tarafın beklediği kod türü ("offer" ya da "answer").
    own_offer_sdp: cevap kodu doğrulanırken bizim gönderdiğimiz teklifin SDP'si.
    """
    if env.type != expected_type:
        return PeerIdentity(INVALID, detail=f"beklenen '{expected_type}' kodu, gelen '{env.type}'")
    if env.mode not in MODES:
        return PeerIdentity(INVALID, detail="geçersiz mod")
    if env.version == 1:
        return PeerIdentity(LEGACY, detail="eski biçim kod: kimlik bilgisi yok, doğrulanamaz")

    try:
        pub = pem_string_to_public_key(env.public_key_pem)
        fp = get_public_key_fingerprint(pub)
    except Exception:
        return PeerIdentity(INVALID, detail="koddaki açık anahtar okunamadı")
    ident = PeerIdentity(INVALID, username=env.username, public_key_pem=env.public_key_pem, fingerprint=fp)

    if not _valid_username(env.username):
        ident.detail = "geçersiz kullanıcı adı"
        return ident
    try:
        ok = verify_signature(pub, base64.b64decode(env.signature), _signed_data(env))
    except Exception:
        ok = False
    if not ok:
        ident.detail = "imza geçersiz — kod değiştirilmiş olabilir"
        return ident

    now = time.time() if now is None else now
    if env.ts < now - MAX_CODE_AGE_SEC or env.ts > now + MAX_CLOCK_SKEW_SEC:
        ident.detail = "kod bayat ya da saati ileri (tek kullanımlıktır; yeni kod isteyin)"
        return ident
    if expected_type == "answer" and env.offer_hash != sdp_hash(own_offer_sdp):
        ident.detail = "bu cevap sizin teklifinize ait değil"
        return ident

    known = contact_lookup(env.username) if contact_lookup else None
    if not known:
        ident.status, ident.detail = NEW, "kişi rehberinizde yok — parmak izini karşı tarafla karşılaştırın"
        return ident
    try:
        same = _der(known) == _der(pub)
    except Exception:
        same = False
    if same:
        ident.status, ident.detail = VERIFIED, "rehberdeki anahtarla eşleşiyor"
    else:
        ident.status = KEY_CHANGED
        ident.detail = ("anahtar rehberdekinden FARKLI — araya giren biri olabilir. "
                        "Bağlantı engellendi; kişiyle başka bir kanaldan doğrulayın.")
    return ident


def _valid_username(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]{2,32}", name or ""))


# ─────────────────────────── veri kanalı protokolü ───────────────────────────
# Her çerçeve tek bir JSON metnidir. Bilinmeyen türler yok sayılır (ileri uyumluluk).

def text_frame(text: str, msg_id: str | None = None) -> str:
    text = (text or "")[:MAX_TEXT_CHARS]
    return json.dumps({"t": "msg", "id": msg_id or secrets.token_hex(16), "text": text,
                       "ts": datetime.now(timezone.utc).isoformat()})


def ack_frame(msg_id: str) -> str:
    return json.dumps({"t": "ack", "id": msg_id})


def parse_frame(raw) -> dict | None:
    """Gelen çerçeveyi doğrular. Geçersiz/bilinmeyen → None."""
    if isinstance(raw, bytes):
        return None                               # ikili çerçeveler dosya parçasıdır (p2p_files)
    if not isinstance(raw, str) or len(raw) > MAX_FRAME_BYTES:
        return None
    try:
        f = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(f, dict):
        return None
    t = f.get("t")
    if t == "msg" and isinstance(f.get("id"), str) and isinstance(f.get("text"), str):
        ts = f.get("ts") if isinstance(f.get("ts"), str) else ""
        return {"t": "msg", "id": f["id"][:64], "text": f["text"][:MAX_TEXT_CHARS], "ts": ts}
    if t == "ack" and isinstance(f.get("id"), str):
        return {"t": "ack", "id": f["id"][:64]}
    # Dosya aktarımı (desktop/p2p_files.py): fid = 32 onaltılık karakter
    fid = f.get("fid")
    if t in FILE_FRAME_TYPES and isinstance(fid, str) and re.fullmatch(r"[0-9a-f]{32}", fid):
        if t == "file_offer":
            name, size, digest = f.get("name"), f.get("size"), f.get("sha256")
            if (isinstance(name, str) and isinstance(size, int) and not isinstance(size, bool)
                    and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
                return {"t": t, "fid": fid, "name": name[:255], "size": size, "sha256": digest}
            return None
        return {"t": t, "fid": fid}
    return None


# ─────────────────────────── bağlantı yardımcısı (STUN/TURN) seçimi ───────────────────────────
# STUN sunucusu cihaza "dışarıdan şu adresle görünüyorsun" der; içeriği görmez ama
# bağlananın IP'sini görür. Seçim iki tarafta bağımsızdır. static/serverless.html'deki
# P2PCore.iceServersFor ile birebir aynı kurallar (tests/test_serverless_html.py).

ICE_DEFAULT = "google+cloudflare"
ICE_PRESETS = {
    "google+cloudflare": "Google + Cloudflare (varsayılan)",
    "cloudflare": "Yalnızca Cloudflare",
    "google": "Yalnızca Google",
    "custom": "Özel sunucu (kendi STUN/TURN'ünüz)",
    "lan": "Yalnızca yerel ağ (dış sunucu yok)",
}
_PUBLIC_STUN = {
    "google": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"],
    "cloudflare": ["stun:stun.cloudflare.com:3478"],
}
MAX_CUSTOM_ICE = 8
_ICE_URL_RE = re.compile(
    r"(stun|turns?):(\[[0-9A-Fa-f:.]{2,45}\]|[A-Za-z0-9.-]{1,253})(?::([0-9]{1,5}))?(\?transport=(?:udp|tcp))?")
_ICE_CRED_RE = re.compile(r"[\x21-\x7e]{1,128}")


class IceConfigError(ValueError):
    """Özel STUN/TURN listesi hatalı."""


def parse_custom_ice(text: str) -> list[dict]:
    """Özel sunucu listesi: satır başına bir sunucu, '#' ile başlayan satırlar yorum.

        stun:sunucu.ornek.com:3478
        turn:sunucu.ornek.com:3478?transport=udp kullanici sifre
        turns:sunucu.ornek.com:5349 kullanici sifre
    """
    servers = []
    for no, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        m = _ICE_URL_RE.fullmatch(parts[0])
        if not m:
            raise IceConfigError(f"{no}. satır: adres stun:sunucu:port ya da turn:sunucu:port biçiminde olmalı")
        scheme, port, query = m.group(1), m.group(3), m.group(4)
        if port is not None and not 1 <= int(port) <= 65535:
            raise IceConfigError(f"{no}. satır: port 1-65535 arasında olmalı")
        if scheme == "stun":
            if query or len(parts) != 1:
                raise IceConfigError(f"{no}. satır: STUN adresi kullanıcı adı/şifre ya da transport almaz")
            servers.append({"urls": parts[0]})
        else:
            if len(parts) != 3 or not all(_ICE_CRED_RE.fullmatch(p) for p in parts[1:]):
                raise IceConfigError(f"{no}. satır: TURN için adresten sonra kullanıcı adı ve şifre yazın "
                                     "(boşlukla ayrılmış)")
            servers.append({"urls": parts[0], "username": parts[1], "credential": parts[2]})
        if len(servers) > MAX_CUSTOM_ICE:
            raise IceConfigError(f"en fazla {MAX_CUSTOM_ICE} sunucu girilebilir")
    return servers


def ice_servers_for(preset: str, custom_text: str = "") -> list[dict]:
    """Seçime göre ICE sunucu listesi ({"urls", ["username", "credential"]}).
    "lan" → boş liste: hiçbir dış sunucuya istek gitmez."""
    if preset == "lan":
        return []
    if preset == "custom":
        servers = parse_custom_ice(custom_text)
        if not servers:
            raise IceConfigError("özel sunucu listesi boş — en az bir sunucu girin ya da başka bir seçenek seçin")
        return servers
    names = {"google": ["google"], "cloudflare": ["cloudflare"]}.get(preset, ["google", "cloudflare"])
    return [{"urls": u} for n in names for u in _PUBLIC_STUN[n]]


# ─────────────────────────── event loop ───────────────────────────
# Sunucusuz mod, sunucu WebSocket'inin loop'una (state["ws_loop"]) bağlı olmamalı:
# sunucu yokken de çalışır. aiortc nesneleri bu ayrı arka plan loop'unda yaşar.

_loop = None
_loop_lock = threading.Lock()


def get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True, name="p2p-loop").start()
        return _loop


def run(coro):
    """Coroutine'i P2P loop'unda çalıştırır (concurrent.futures.Future döner)."""
    return asyncio.run_coroutine_threadsafe(coro, get_loop())
