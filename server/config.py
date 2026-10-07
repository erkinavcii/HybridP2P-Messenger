import os
import time
from dotenv import load_dotenv

# Env yükle
load_dotenv()

HOST = os.getenv("HYBRIDP2P_HOST", "0.0.0.0")
PORT = int(os.getenv("HYBRIDP2P_PORT", "8000"))
DB_PATH = os.getenv("HYBRIDP2P_DB_PATH", "relay_server.db")

def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")

# Geliştirmede dosya değişince otomatik yeniden yükleme. Üretimde KAPALI olmalı
# (ek süreç, dosya izleme; Docker imajı 0 ayarlar).
RELOAD = _env_flag("HYBRIDP2P_RELOAD", "0")

# Ters vekil (Caddy/nginx) arkasında gerçek istemci IP'si X-Forwarded-For'dan
# okunur; yalnızca buradaki adreslerden gelen başlığa güvenilir. Rate limit
# bu IP'yi kullanır — yanlış ayarda herkes vekilin tek IP'sini paylaşır.
# "*" yalnızca uygulama portu dışarıya kapalıyken (Docker ağı) güvenlidir.
FORWARDED_ALLOW_IPS = os.getenv("HYBRIDP2P_FORWARDED_ALLOW_IPS", "127.0.0.1")
MAX_FILE_SIZE = int(os.getenv("HYBRIDP2P_MAX_FILE_SIZE", "10485760")) # default 10MB

# Tek bir WebSocket / REST-fallback mesajının azami boyutu (karakter).
# Dosyalar bu yoldan değil /api/upload_file ile gider (MAX_FILE_SIZE).
# 256 KB; şifreli metin, SDP, avatar (~25 KB) ve link önizleme (~30 KB) için yeterli pay.
MAX_WS_MESSAGE_SIZE = int(os.getenv("HYBRIDP2P_MAX_WS_MESSAGE_SIZE", "262144"))

cors_origins_raw = os.getenv("HYBRIDP2P_CORS_ORIGINS", "*")
CORS_ORIGINS = [orig.strip() for orig in cors_origins_raw.split(",")] if cors_origins_raw else ["*"]

allowed_hosts_raw = os.getenv("HYBRIDP2P_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_raw.split(",")] if allowed_hosts_raw else ["*"]

START_TIME = time.time()

# ── Aramalar (WebRTC) için STUN/TURN ──────────────────────────────────
# Yalnızca sesli/görüntülü aramaları etkiler; mesajlar her zaman röle WebSocket'inden gider.
#  • STUN: cihaza dış IP:port'unu söyler, ses/görüntü yine DOĞRUDAN cihazdan cihaza akar.
#    STUN sunucusu içeriği görmez ama arayanın IP'sini görür.
#  • TURN: doğrudan yol kurulamazsa (CGNAT, simetrik NAT) akışı aktarır; akış
#    DTLS-SRTP ile şifreli kalır, TURN dinleyemez.

# Herkese açık STUN (varsayılan Google). 0 → hiçbir üçüncü tarafa istek gitmez;
# o zaman kendi coturn'ünüzü (TURN_HOST) kurun, yoksa yalnızca aynı ağdaki aramalar çalışır.
PUBLIC_STUN = _env_flag("HYBRIDP2P_PUBLIC_STUN", "1")
PUBLIC_STUN_URLS = [u.strip() for u in os.getenv(
    "HYBRIDP2P_PUBLIC_STUN_URLS",
    "stun:stun.l.google.com:19302,stun:stun1.l.google.com:19302",
).split(",") if u.strip()]

# Kendi STUN/TURN sunucunuz (docker-compose "turn" profili → coturn)
TURN_HOST       = os.getenv("TURN_HOST", "")
TURN_PORT       = int(os.getenv("TURN_PORT", "3478"))
# TURN-over-TLS (turns:) yalnızca coturn'e sertifika verildiyse açılmalı; boş = kapalı
TURN_TLS_PORT   = os.getenv("TURN_TLS_PORT", "")
TURN_USERNAME   = os.getenv("TURN_USERNAME", "")
TURN_CREDENTIAL = os.getenv("TURN_CREDENTIAL", "")
TURN_SECRET     = os.getenv("TURN_SECRET", "")   # önerilen: kısa ömürlü kimlik bilgisi (HMAC)
