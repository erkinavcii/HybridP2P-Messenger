import time
import hmac
import hashlib
import base64
from fastapi import APIRouter, Request, Header
from server.auth import verify_request_signature
from server.config import TURN_HOST, TURN_USERNAME, TURN_CREDENTIAL, TURN_SECRET

router = APIRouter()

@router.get("/api/ice_servers")
async def get_ice_servers(
    request: Request,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    WebRTC ICE yapılandırmasını döndürür.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)

    ice_servers = [
        # Google'ın ücretsiz STUN sunucuları
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
        {"urls": "stun:stun2.l.google.com:19302"},
    ]

    # TURN sunucusu yapılandırılmışsa ekle
    if TURN_HOST:
        if TURN_SECRET:
            # 6 saatlik geçerlilik
            expiry = int(time.time()) + 21600
            turn_username = f"{expiry}:{x_username}"
            
            dig = hmac.new(
                TURN_SECRET.encode("utf-8"),
                turn_username.encode("utf-8"),
                hashlib.sha1
            ).digest()
            turn_credential = base64.b64encode(dig).decode("utf-8")
        else:
            turn_username = TURN_USERNAME
            turn_credential = TURN_CREDENTIAL

        ice_servers.append({
            "urls": [
                f"turn:{TURN_HOST}:3478?transport=udp",
                f"turn:{TURN_HOST}:3478?transport=tcp",
                f"turns:{TURN_HOST}:5349",  # TLS üzerinden TURN
            ],
            "username": turn_username,
            "credential": turn_credential,
        })

    return {"ice_servers": ice_servers}
