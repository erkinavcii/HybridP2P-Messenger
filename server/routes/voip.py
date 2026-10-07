import time
import hmac
import hashlib
import base64
from fastapi import APIRouter, Request, Header
from server.auth import verify_request_signature
from server import config

router = APIRouter()

TURN_CREDENTIAL_TTL = 6 * 3600   # kısa ömürlü TURN kimlik bilgisi (saniye)


def _host(h: str) -> str:
    return f"[{h}]" if ":" in h else h


def build_ice_servers(username: str) -> list[dict]:
    """Sunucu ayarlarına göre WebRTC ICE listesi.

    Sıra: önce kendi sunucumuz (coturn, STUN+TURN), sonra — açıksa — herkese açık
    STUN. Hiçbiri yoksa boş liste: yalnızca yerel ağdaki aramalar kurulabilir.
    """
    servers: list[dict] = []

    if config.TURN_HOST:
        host = _host(config.TURN_HOST)
        # coturn aynı portta STUN da sunar: üçüncü tarafa gerek kalmaz
        servers.append({"urls": f"stun:{host}:{config.TURN_PORT}"})
        if config.TURN_SECRET:
            # coturn "use-auth-secret" (REST API) şeması: kullanıcı = "<bitiş>:<ad>",
            # parola = base64(HMAC-SHA1(secret, kullanıcı)). Sızsa bile 6 saatte geçersizleşir.
            turn_username = f"{int(time.time()) + TURN_CREDENTIAL_TTL}:{username}"
            dig = hmac.new(config.TURN_SECRET.encode("utf-8"), turn_username.encode("utf-8"),
                           hashlib.sha1).digest()
            turn_credential = base64.b64encode(dig).decode("utf-8")
        else:
            turn_username, turn_credential = config.TURN_USERNAME, config.TURN_CREDENTIAL
        urls = [f"turn:{host}:{config.TURN_PORT}?transport=udp",
                f"turn:{host}:{config.TURN_PORT}?transport=tcp"]
        if config.TURN_TLS_PORT:
            urls.append(f"turns:{host}:{config.TURN_TLS_PORT}")
        servers.append({"urls": urls, "username": turn_username, "credential": turn_credential})

    if config.PUBLIC_STUN:
        servers.extend({"urls": u} for u in config.PUBLIC_STUN_URLS)

    return servers


@router.get("/api/ice_servers")
async def get_ice_servers(
    request: Request,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """WebRTC ICE yapılandırmasını döndürür (imzalı istek; TURN kimliği kişiye özel)."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    return {"ice_servers": build_ice_servers(x_username)}
