import base64
import hashlib
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from crypto_utils import verify_signature, pem_string_to_public_key
from server.database import db_session

async def verify_request_signature(request: Request, username: str, timestamp: str, signature: str):
    """İstek başlıklarındaki imzayı doğrular."""
    try:
        req_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if req_dt.tzinfo is None:
            req_dt = req_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs((now - req_dt).total_seconds()) > 300:
            raise HTTPException(status_code=401, detail="Zaman aşımı veya geçersiz timestamp.")
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz timestamp formatı.")

    async with db_session() as db:
        cursor = await db.execute("SELECT public_key FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı.")
        pub_key_pem = row["public_key"]

    try:
        pub_key = pem_string_to_public_key(pub_key_pem)
        sig_bytes = base64.b64decode(signature)
        body_hash = hashlib.sha256(await request.body()).hexdigest()
        data_to_verify = "\n".join([
            username,
            timestamp,
            request.method.upper(),
            request.url.path,
            body_hash,
        ]).encode("utf-8")
        if not verify_signature(pub_key, sig_bytes, data_to_verify):
            raise HTTPException(status_code=401, detail="Geçersiz imza.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Kimlik doğrulama hatası: {e}")
