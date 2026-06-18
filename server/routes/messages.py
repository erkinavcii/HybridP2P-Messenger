import json
import uuid as uuid_lib
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel
from server.config import MAX_FILE_SIZE
from server.database import db_session
from server.auth import verify_request_signature
from server.websocket_manager import manager
from server.limiter import limiter

router = APIRouter()

class SendMessageRequest(BaseModel):
    """REST üzerinden mesaj gönderme isteği (çevrimdışı teslimat)."""
    sender: str
    recipient: str
    encrypted_payload: str  # Base64 kodlu şifreli paket
    view_once: bool = False


class EphemeralToggleRequest(BaseModel):
    """Ephemeral mod toggle istegi."""
    sender: str
    recipient: str
    ephemeral: bool  # True -> gecici mod ac, False -> kapat


class FileUploadRequest(BaseModel):
    """Dosya yukleme istegi — sifrelenmis dosya verisi + metadata."""
    sender: str
    recipient: str
    encrypted_data: str  # Base64 kodlu sifrelenmis dosya
    original_name: str   # Orijinal dosya adi (ornek: foto.jpg)
    file_type: str       # 'image' | 'video' | 'document' | 'audio'


class WsFallbackRequest(BaseModel):
    """WebSocket kopukken tum paket tipleri icin REST fallback model."""
    payload: str


def _make_chat_id(user1: str, user2: str) -> str:
    """İki kullanıcı için tutarlı chat_id üretir (sıralı)."""
    return "_".join(sorted([user1, user2]))


@router.get("/api/chat_settings/{username}")
async def get_chat_settings(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Kullanıcının tüm chat ephemeral ayarlarını döndürür.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != username:
        raise HTTPException(status_code=403, detail="Yetkisiz erisim.")
    async with db_session() as db:
        # Bu kullanıcıyı içeren tüm chat_settings satırlarını al
        cursor = await db.execute(
            """SELECT chat_id, ephemeral, changed_by, changed_at
               FROM chat_settings
               WHERE chat_id LIKE ? OR chat_id LIKE ?""",
            (f"{username}_%", f"%_{username}")
        )
        rows = await cursor.fetchall()
        return {
            "settings": [
                {
                    "chat_id": r["chat_id"],
                    "ephemeral": bool(r["ephemeral"]),
                    "changed_by": r["changed_by"],
                    "changed_at": r["changed_at"],
                }
                for r in rows
            ]
        }


@router.post("/api/ephemeral_toggle")
async def rest_ephemeral_toggle(
    request: Request,
    req: EphemeralToggleRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    REST üzerinden ephemeral toggle (WebSocket bağlantısı yokken fallback).
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != req.sender:
        raise HTTPException(status_code=403, detail="Yetkisiz erisim.")
    chat_id = _make_chat_id(req.sender, req.recipient)
    ts = datetime.now(timezone.utc).isoformat()
    async with db_session() as db:
        # chat_settings güncelle
        await db.execute(
            """INSERT INTO chat_settings (chat_id, ephemeral, changed_by, changed_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   ephemeral=excluded.ephemeral,
                   changed_by=excluded.changed_by,
                   changed_at=excluded.changed_at""",
            (chat_id, 1 if req.ephemeral else 0, req.sender, ts)
        )
        await db.commit()

        toggle_msg = {
            "type": "ephemeral_toggle",
            "sender": req.sender,
            "ephemeral": req.ephemeral,
            "timestamp": ts,
        }

        # Alıcı online ise doğrudan WebSocket ile ilet
        if manager.is_online(req.recipient):
            await manager.send_to_user(req.recipient, toggle_msg)
        else:
            # Offline → kuyruğa ekle
            await db.execute(
                """INSERT INTO offline_msgs
                   (sender, recipient, msg_type, extra_data, timestamp)
                   VALUES (?, ?, 'ephemeral_toggle', ?, ?)""",
                (req.sender, req.recipient, json.dumps({"ephemeral": req.ephemeral}), ts)
            )
            await db.commit()

        return {"status": "ok", "ephemeral": req.ephemeral, "chat_id": chat_id}


@router.post("/api/upload_file")
@limiter.limit("20/minute")
async def upload_file(
    request: Request,
    req: FileUploadRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Sifrelenmis dosyayi alir, UUID ile veritabanina kaydeder.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != req.sender:
        raise HTTPException(status_code=403, detail="Yetkisiz erisim.")
        
    estimated_size = (len(req.encrypted_data) * 3) // 4
    if estimated_size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE / (1024 * 1024)
        raise HTTPException(
            status_code=413, 
            detail=f"Dosya boyutu çok büyük! Maksimum limit {max_mb:.1f} MB'tır."
        )

    file_uuid = str(uuid_lib.uuid4())
    async with db_session() as db:
        await db.execute(
            """INSERT INTO file_store
               (uuid, sender, recipient, encrypted_data, original_name, file_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_uuid, req.sender, req.recipient,
             req.encrypted_data, req.original_name, req.file_type)
        )
        await db.commit()
        return {"uuid": file_uuid, "status": "uploaded"}


@router.get("/api/download_file/{file_uuid}")
async def download_file(
    request: Request,
    file_uuid: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Sifrelenmis dosyayi verir ve veritabanindan siler (Zero-Knowledge).
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute(
            "SELECT * FROM file_store WHERE uuid = ?", (file_uuid,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="Dosya bulunamadi veya zaten indirildi."
            )
        if row["recipient"] != x_username:
            raise HTTPException(status_code=403, detail="Bu dosyayi indirme yetkiniz yok.")
        result = {
            "uuid":           row["uuid"],
            "sender":         row["sender"],
            "encrypted_data": row["encrypted_data"],
            "original_name":  row["original_name"],
            "file_type":      row["file_type"],
            "timestamp":      row["timestamp"],
        }
        # Zero-Knowledge: indirme sonrasi kalici sil
        await db.execute("DELETE FROM file_store WHERE uuid = ?", (file_uuid,))
        await db.commit()
        return result


@router.post("/api/send_offline")
@limiter.limit("60/minute")
async def send_offline_message(
    request: Request,
    req: SendMessageRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Alıcı çevrimdışıyken mesajı veritabanına kaydeder. Alıcı çevrimiçiyse anında iletir.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != req.sender:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
        
    async with db_session() as db:
        cursor = await db.execute(
            "SELECT username FROM users WHERE username = ?",
            (req.recipient,)
        )
        recipient = await cursor.fetchone()
        if not recipient:
            raise HTTPException(
                status_code=404,
                detail=f"Alıcı '{req.recipient}' kayıtlı değil."
            )

    ts = datetime.now(timezone.utc).isoformat()
    if manager.is_online(req.recipient):
        await manager.send_to_user(req.recipient, {
            "type": "message",
            "sender": req.sender,
            "encrypted_payload": req.encrypted_payload,
            "view_once": req.view_once,
            "timestamp": ts,
        })
        print(f"[Server] Relayed offline message (REST -> WS) from '{req.sender}' to '{req.recipient}'")
        return {"status": "delivered", "message": "Mesaj alıcıya WebSocket üzerinden iletildi."}
    else:
        async with db_session() as db:
            await db.execute(
                """INSERT INTO offline_msgs (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                   VALUES (?, ?, ?, 'message', ?, ?)""",
                (req.sender, req.recipient, req.encrypted_payload,
                 json.dumps({"view_once": req.view_once}), ts)
            )
            await db.commit()
        print(f"[Server] Stored offline message (REST) from '{req.sender}' to '{req.recipient}'")
        return {"status": "stored", "message": "Mesaj şifreli olarak saklandı."}


@router.post("/api/send_ws_fallback")
@limiter.limit("60/minute")
async def send_ws_fallback(
    request: Request,
    req: WsFallbackRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    WebSocket bağlantısı yokken istemcinin tüm paket tiplerini iletebileceği REST fallback.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    
    try:
        message = json.loads(req.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz JSON payload.")
        
    msg_type = message.get("type", "")
    message["sender"] = x_username
    ts = datetime.now(timezone.utc).isoformat()
    
    if msg_type == "message":
        recipient = message.get("recipient", "")
        encrypted_payload = message.get("encrypted_payload", "")
        view_once = bool(message.get("view_once", False))
        timestamp = message.get("timestamp") or ts
        
        if manager.is_online(recipient):
            await manager.send_to_user(recipient, {
                "type": "message",
                "sender": x_username,
                "encrypted_payload": encrypted_payload,
                "view_once": view_once,
                "timestamp": timestamp,
            })
        else:
            async with db_session() as db:
                await db.execute(
                    """INSERT INTO offline_msgs (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                       VALUES (?, ?, ?, 'message', ?, ?)""",
                    (x_username, recipient, encrypted_payload,
                     json.dumps({"view_once": view_once}), timestamp)
                )
                await db.commit()
                
    elif msg_type == "file_message":
        recipient = message.get("recipient", "")
        file_uuid = message.get("file_uuid", "")
        original_name = message.get("original_name", "dosya")
        file_type = message.get("file_type", "document")
        view_once = bool(message.get("view_once", False))
        timestamp = message.get("timestamp") or ts
        
        file_msg = {
            "type":          "file_message",
            "sender":        x_username,
            "file_uuid":     file_uuid,
            "original_name": original_name,
            "file_type":     file_type,
            "view_once":     view_once,
            "timestamp":     timestamp,
        }
        
        if manager.is_online(recipient):
            await manager.send_to_user(recipient, file_msg)
        else:
            async with db_session() as db:
                await db.execute(
                    """INSERT INTO offline_msgs
                       (sender, recipient, msg_type, extra_data, timestamp)
                       VALUES (?, ?, 'file_message', ?, ?)""",
                    (x_username, recipient, json.dumps(file_msg), timestamp)
                )
                await db.commit()
                
    elif msg_type == "ephemeral_toggle":
        recipient = message.get("recipient", "")
        ephemeral = bool(message.get("ephemeral", False))
        chat_id = _make_chat_id(x_username, recipient)
        timestamp = message.get("timestamp") or ts
        
        async with db_session() as db:
            await db.execute(
                """INSERT INTO chat_settings (chat_id, ephemeral, changed_by, changed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       ephemeral=excluded.ephemeral,
                       changed_by=excluded.changed_by,
                       changed_at=excluded.changed_at""",
                (chat_id, 1 if ephemeral else 0, x_username, timestamp)
            )
            await db.commit()
            
            toggle_payload = {
                "type": "ephemeral_toggle",
                "sender": x_username,
                "ephemeral": ephemeral,
                "timestamp": timestamp,
            }
            
            if manager.is_online(recipient):
                await manager.send_to_user(recipient, toggle_payload)
            else:
                await db.execute(
                    """INSERT INTO offline_msgs
                       (sender, recipient, msg_type, extra_data, timestamp)
                       VALUES (?, ?, 'ephemeral_toggle', ?, ?)""",
                    (x_username, recipient, json.dumps({"ephemeral": ephemeral}), timestamp)
                )
                await db.commit()
                
    elif msg_type == "group_key_dist":
        recipient = message.get("recipient", "")
        encrypted_payload = message.get("encrypted_payload", "")
        group_id = message.get("group_id", "")
        timestamp = message.get("timestamp") or ts
        
        dist_payload = {
            "type": "group_key_dist",
            "sender": x_username,
            "group_id": group_id,
            "encrypted_payload": encrypted_payload,
            "timestamp": timestamp,
        }
        
        if manager.is_online(recipient):
            await manager.send_to_user(recipient, dist_payload)
        else:
            async with db_session() as db:
                await db.execute(
                    """INSERT INTO offline_msgs
                       (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                       VALUES (?, ?, ?, 'group_key_dist', ?, ?)""",
                    (x_username, recipient, encrypted_payload, json.dumps({"group_id": group_id}), timestamp)
                )
                await db.commit()
                
    elif msg_type == "group_message":
        group_id = message.get("group_id", "")
        encrypted_payload = message.get("encrypted_payload", "")
        signature = message.get("signature", "")
        timestamp = message.get("timestamp") or ts
        
        async with db_session() as db:
            cursor = await db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            )
            sender_member = await cursor.fetchone()
            if not sender_member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
                
            cursor = await db.execute(
                "SELECT username FROM group_members WHERE group_id = ?", (group_id,)
            )
            members = await cursor.fetchall()
            
        group_msg_payload = {
            "type": "group_message",
            "sender": x_username,
            "group_id": group_id,
            "encrypted_payload": encrypted_payload,
            "signature": signature,
            "timestamp": timestamp,
        }
        
        async with db_session() as db:
            for m in members:
                recipient = m["username"]
                if recipient == x_username:
                    continue
                if manager.is_online(recipient):
                    await manager.send_to_user(recipient, group_msg_payload)
                else:
                    await db.execute(
                        """INSERT INTO offline_msgs
                           (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                           VALUES (?, ?, ?, 'group_message', ?, ?)""",
                        (x_username, recipient, encrypted_payload,
                         json.dumps({"group_id": group_id, "signature": signature}), timestamp)
                    )
            await db.commit()

    elif msg_type == "read_receipt":
        recipient = message.get("recipient", "")
        timestamp = message.get("timestamp", "")
        
        receipt_payload = {
            "type": "read_receipt",
            "sender": x_username,
            "timestamp": timestamp,
        }
        
        if manager.is_online(recipient):
            await manager.send_to_user(recipient, receipt_payload)
        else:
            async with db_session() as db:
                await db.execute(
                    """INSERT INTO offline_msgs (sender, recipient, msg_type, extra_data, timestamp)
                       VALUES (?, ?, 'read_receipt', ?, ?)""",
                    (x_username, recipient, json.dumps({"timestamp": timestamp}), timestamp)
                )
                await db.commit()
            
    return {"status": "ok", "delivered_or_stored": True}


@router.get("/api/fetch_messages/{username}")
async def fetch_offline_messages(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Kullanıcının bekleyen çevrimdışı mesajlarını döndürür ve
    veritabanından kalıcı olarak siler (Zero-Knowledge prensibi).
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != username:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    async with db_session() as db:
        cursor = await db.execute(
            "SELECT id, sender, encrypted_payload, extra_data, timestamp FROM offline_msgs WHERE recipient = ? AND msg_type = 'message' ORDER BY timestamp ASC",
            (username,)
        )
        rows = await cursor.fetchall()

        messages = []
        for r in rows:
            extra = json.loads(r["extra_data"] or "{}")
            messages.append({
                "id": r["id"],
                "sender": r["sender"],
                "encrypted_payload": r["encrypted_payload"],
                "view_once": extra.get("view_once", False),
                "timestamp": r["timestamp"],
            })

        if messages:
            await db.execute(
                "DELETE FROM offline_msgs WHERE recipient = ? AND msg_type = 'message'",
                (username,)
            )
            await db.commit()

        return {"messages": messages, "count": len(messages)}
