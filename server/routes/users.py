import base64
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel
from crypto_utils import pem_string_to_public_key, verify_signature
from server.database import db_session
from server.auth import verify_request_signature
from server.websocket_manager import manager
from server.limiter import limiter

router = APIRouter()

class UserRegisterRequest(BaseModel):
    """Kullanıcı kayıt isteği — username, public key PEM, timestamp ve imza."""
    username: str
    public_key: str  # PEM formatında public key
    timestamp: str
    signature: str   # base64 signature of "username:timestamp:public_key"


@router.post("/api/register")
@limiter.limit("20/minute")
async def register_user(request: Request, req: UserRegisterRequest):
    """
    Yeni kullanıcı kaydeder veya mevcut kullanıcının public key'ini günceller.
    """
    try:
        req_dt = datetime.fromisoformat(req.timestamp.replace("Z", "+00:00"))
        if req_dt.tzinfo is None:
            req_dt = req_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs((now - req_dt).total_seconds()) > 300:
            raise HTTPException(status_code=401, detail="Kayıt isteği zaman aşımına uğradı.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz timestamp formatı.")

    async with db_session() as db:
        # Kullanıcı zaten var mı kontrol et
        cursor = await db.execute(
            "SELECT username, public_key FROM users WHERE username = ?",
            (req.username,)
        )
        existing = await cursor.fetchone()

        data_to_verify = f"{req.username}:{req.timestamp}:{req.public_key}".encode("utf-8")

        if existing:
            # Public key güncelleme / giriş testi:
            if existing["public_key"] == req.public_key:
                pub_key = pem_string_to_public_key(existing["public_key"])
                sig_bytes = base64.b64decode(req.signature)
                if not verify_signature(pub_key, sig_bytes, data_to_verify):
                    raise HTTPException(status_code=401, detail="Kimlik doğrulama başarısız (geçersiz imza).")
                return {"status": "ok", "message": f"'{req.username}' zaten kayıtlı ve doğrulandı."}
            else:
                # Farklı bir key ile güncelleme talebi
                pub_key = pem_string_to_public_key(existing["public_key"])
                sig_bytes = base64.b64decode(req.signature)
                if not verify_signature(pub_key, sig_bytes, data_to_verify):
                    raise HTTPException(status_code=401, detail="Kullanıcı adı zaten kullanımda. Anahtar güncelleme için eski özel anahtarla imza gerekiyor.")
                
                # Güncelle
                await db.execute(
                    "UPDATE users SET public_key = ? WHERE username = ?",
                    (req.public_key, req.username)
                )
                await db.commit()
                return {"status": "updated", "message": f"'{req.username}' kullanıcısının public key'i güncellendi."}
        else:
            # Yeni kayıt
            pub_key = pem_string_to_public_key(req.public_key)
            sig_bytes = base64.b64decode(req.signature)
            if not verify_signature(pub_key, sig_bytes, data_to_verify):
                raise HTTPException(status_code=401, detail="Geçersiz imza. Gönderilen açık anahtarla eşleşen özel anahtar kanıtlanamadı.")
            
            await db.execute(
                "INSERT INTO users (username, public_key) VALUES (?, ?)",
                (req.username, req.public_key)
            )
            await db.commit()
            return {"status": "created", "message": f"'{req.username}' başarıyla kaydedildi."}


@router.get("/api/public_key/{username}")
async def get_public_key(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Belirtilen kullanıcının public key'ini döndürür.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute(
            "SELECT public_key FROM users WHERE username = ?",
            (username,)
        )
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"'{username}' adlı kullanıcı bulunamadı."
            )

        return {"username": username, "public_key": row["public_key"]}


@router.get("/api/status/{username}")
async def get_user_status(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Belirtilen kullanıcının online durumunu sorgular.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute("SELECT username FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
            
    return {"username": username, "online": manager.is_online(username)}


@router.get("/api/users")
async def list_users(
    request: Request,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Kayıtlı tüm kullanıcıları listeler.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute("SELECT username, created_at FROM users")
        rows = await cursor.fetchall()
        return {
            "users": [
                {"username": r["username"], "created_at": r["created_at"]}
                for r in rows
            ]
        }
