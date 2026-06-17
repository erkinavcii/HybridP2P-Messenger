"""
server.py — Geçici Röle Sunucusu (Zero-Knowledge Relay Server)
================================================================
Bu sunucu, HybridP2P-Messenger uygulamasının merkezî röle bileşenidir.
Sunucu asla mesajların düz metin halini görmez — yalnızca şifreli
ciphertext'leri geçici olarak depolar.

Mimari:
  • REST API  → Kullanıcı kaydı, public key takası, çevrimdışı mesaj çekme
  • WebSocket → Gerçek zamanlı (online) mesaj iletimi

Veritabanı Tabloları (SQLite):
  • users          → username, public_key_pem, created_at
  • offline_msgs   → id, sender, recipient, encrypted_payload, msg_type, timestamp
  • chat_settings  → chat_id, ephemeral, changed_by, changed_at
                     (chat_id = sorted(user1, user2) aralarında '_' ile birleştirilir)

Güvenlik Prensipleri:
  1. Sunucu hiçbir private key saklamaz.
  2. Mesajlar yalnızca şifreli (ciphertext) olarak tutulur.
  3. Alıcı mesajları çektiğinde veritabanından kalıcı olarak silinir.
"""

import os
import json
import asyncio
import aiosqlite
import uuid as uuid_lib
import base64
import hashlib
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from crypto_utils import verify_signature, pem_string_to_public_key

# Env yükle
load_dotenv()

HOST = os.getenv("HYBRIDP2P_HOST", "0.0.0.0")
PORT = int(os.getenv("HYBRIDP2P_PORT", "8000"))
DB_PATH = os.getenv("HYBRIDP2P_DB_PATH", "relay_server.db")
MAX_FILE_SIZE = int(os.getenv("HYBRIDP2P_MAX_FILE_SIZE", "10485760")) # default 10MB

cors_origins_raw = os.getenv("HYBRIDP2P_CORS_ORIGINS", "*")
CORS_ORIGINS = [orig.strip() for orig in cors_origins_raw.split(",")] if cors_origins_raw else ["*"]

allowed_hosts_raw = os.getenv("HYBRIDP2P_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_raw.split(",")] if allowed_hosts_raw else ["*"]

START_TIME = time.time()

# ╔═══════════════════════════════════════════════════════════════════╗
# ║                      VERİTABANI KATMANI                          ║
# ╚═══════════════════════════════════════════════════════════════════╝


async def init_database():
    """
    Initializes the SQLite database and creates the required tables.
    Can be safely called every time the application starts.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Kullanıcılar tablosu — public key'leri saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username    TEXT PRIMARY KEY,
                public_key  TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Çevrimdışı mesajlar tablosu — şifreli payload'ları geçici saklar
        # msg_type: 'message' | 'ephemeral_toggle' | 'system' | 'group_key_dist' | 'group_message'
        await db.execute("""
            CREATE TABLE IF NOT EXISTS offline_msgs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                sender              TEXT NOT NULL,
                recipient           TEXT NOT NULL,
                encrypted_payload   TEXT NOT NULL DEFAULT '',
                msg_type            TEXT NOT NULL DEFAULT 'message',
                extra_data          TEXT NOT NULL DEFAULT '{}',
                timestamp           TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Chat ayarları — ephemeral mod durumunu saklar
        # chat_id = iki kullanıcı adı alfabetik sırayla birleştirilir (örn: alice_bob)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id     TEXT PRIMARY KEY,
                ephemeral   INTEGER NOT NULL DEFAULT 0,
                changed_by  TEXT,
                changed_at  TEXT
            )
        """)

        # Dosya depolama — sifrelenmis dosya blob'larini gecici saklar
        # download sonrasi silinir (Zero-Knowledge)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS file_store (
                uuid            TEXT PRIMARY KEY,
                sender          TEXT NOT NULL,
                recipient       TEXT NOT NULL,
                encrypted_data  TEXT NOT NULL,
                original_name   TEXT NOT NULL,
                file_type       TEXT NOT NULL DEFAULT 'document',
                timestamp       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Gruplar tablosu — grup bilgilerini saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id    TEXT PRIMARY KEY,
                group_name  TEXT NOT NULL,
                creator     TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Grup üyeleri tablosu — grup üyeliklerini saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id    TEXT NOT NULL,
                username    TEXT NOT NULL,
                PRIMARY KEY (group_id, username),
                FOREIGN KEY (group_id) REFERENCES groups(group_id),
                FOREIGN KEY (username) REFERENCES users(username)
            )
        """)

        await db.commit()


@asynccontextmanager
async def db_session():
    """Asenkron veritabanı bağlantısı ve context manager sağlar."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row  # Sütun ismiyle erişim için
    try:
        yield db
    finally:
        await db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                  FastAPI UYGULAMA YAŞAM DÖNGÜSÜ                  ║
# ╚═══════════════════════════════════════════════════════════════════╝

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama başlarken veritabanını oluşturur."""
    await init_database()
    print("[DB] Database ready. Relay server started.")
    yield
    print("[Server] Relay server shutting down.")


app = FastAPI(
    title="HybridP2P Messenger — Röle Sunucusu",
    description="Zero-Knowledge Store-and-Forward Relay Server",
    version="1.0.0",
    lifespan=lifespan,
)

# Rate Limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Trusted Host Middleware
if ALLOWED_HOSTS and ALLOWED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# CORS — Dinamik cors origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                    Pydantic Veri Modelleri                        ║
# ╚═══════════════════════════════════════════════════════════════════╝

class UserRegisterRequest(BaseModel):
    """Kullanıcı kayıt isteği — username, public key PEM, timestamp ve imza."""
    username: str
    public_key: str  # PEM formatında public key
    timestamp: str
    signature: str   # base64 signature of "username:timestamp:public_key"


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
    encrypted_data: str  # Base64 kodlu sifrelenmis dosya (encrypt_bytes ciktisi)
    original_name: str   # Orijinal dosya adi (ornek: foto.jpg)
    file_type: str       # 'image' | 'video' | 'document' | 'audio'


class GroupCreateRequest(BaseModel):
    """Grup olusturma istegi."""
    group_id: str
    group_name: str
    creator: str
    members: list[str]  # Grup uyelerinin adlari


class GroupAddMemberRequest(BaseModel):
    """Gruba uye ekleme istegi."""
    username: str


class WsFallbackRequest(BaseModel):
    """WebSocket kopukken tum paket tipleri icin REST fallback model."""
    payload: str


# ╔═══════════════════════════════════════════════════════════════════╗
# ║              REST API — KULLANICI ve ANAHTAR YÖNETİMİ            ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/register")
@limiter.limit("20/minute")
async def register_user(request: Request, req: UserRegisterRequest):
    """
    Yeni kullanıcı kaydeder veya mevcut kullanıcının public key'ini günceller.

    Bu endpoint istemci ilk açıldığında çağrılır:
      1. İstemci RSA anahtar çifti üretir
      2. Public key'i bu endpoint'e gönderir
      3. Sunucu public key'i veritabanına kaydeder
    """
    # 1. Verify timestamp to prevent replay attacks (allow 5 min drift)
    try:
        req_dt = datetime.fromisoformat(req.timestamp.replace("Z", "+00:00"))
        if req_dt.tzinfo is None:
            req_dt = req_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs((now - req_dt).total_seconds()) > 300:
            raise HTTPException(status_code=401, detail="Kayıt isteği zaman aşımına uğradı.")
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
            # Eğer gönderilen key eskisiyle aynıysa, mevcut key ile imzayı doğrula
            if existing["public_key"] == req.public_key:
                pub_key = pem_string_to_public_key(existing["public_key"])
                sig_bytes = base64.b64decode(req.signature)
                if not verify_signature(pub_key, sig_bytes, data_to_verify):
                    raise HTTPException(status_code=401, detail="Kimlik doğrulama başarısız (geçersiz imza).")
                return {"status": "ok", "message": f"'{req.username}' zaten kayıtlı ve doğrulandı."}
            else:
                # Farklı bir key ile güncelleme talebi: Eski anahtarla imza kanıtlanmalı.
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
            # Yeni kayıt. Gönderilen yeni key ile imzayı doğrula.
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
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Kimlik doğrulama hatası: {e}")


@app.get("/api/public_key/{username}")
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


@app.get("/api/status/{username}")
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


@app.get("/api/users")
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


@app.get("/health")
@limiter.limit("30/minute")
async def health_check(request: Request):
    """
    Sunucu sağlık durumu, uptime, db durumu ve bağlantı istatistiklerini döner.
    """
    uptime_seconds = int(time.time() - START_TIME)
    db_ok = False
    try:
        async with db_session() as db:
            await db.execute("SELECT 1")
            db_ok = True
    except Exception as e:
        print(f"[Health Check] DB error: {e}")
        
    return {
        "status": "healthy" if db_ok else "unhealthy",
        "uptime_seconds": uptime_seconds,
        "database": "connected" if db_ok else "disconnected",
        "active_connections": len(manager.active_connections),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ╔═══════════════════════════════════════════════════════════════════╗
# ║               REST API — CHAT AYARLARI (EPHEMERAL)               ║
# ╚═══════════════════════════════════════════════════════════════════╝

def _make_chat_id(user1: str, user2: str) -> str:
    """İki kullanıcı için tutarlı chat_id üretir (sıralı)."""
    return "_".join(sorted([user1, user2]))


@app.get("/api/chat_settings/{username}")
async def get_chat_settings(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Kullanıcının tüm chat ephemeral ayarlarını döndürür.
    İstemci bağlandığında bu endpoint'i çağırarak yerel durumunu senkronize eder.
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


@app.post("/api/ephemeral_toggle")
async def rest_ephemeral_toggle(
    request: Request,
    req: EphemeralToggleRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    REST üzerinden ephemeral toggle (WebSocket bağlantısı yokken fallback).
    Online kullanıcıya WebSocket üzerinden de iletir.
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


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                DOSYA YUKLEME / INDIRME (E2EE)                    ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/upload_file")
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
    Sunucu dosya icerigini gormez — sadece sifrelenmis blob saklar (Zero-Knowledge).
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


@app.get("/api/download_file/{file_uuid}")
async def download_file(
    request: Request,
    file_uuid: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Sifrelenmis dosyayi verir ve veritabanindan siler (Zero-Knowledge).
    Her dosya yalnizca bir kez indirilebilir.
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


# ╔═══════════════════════════════════════════════════════════════════╗
# ║          REST API — ÇEVRİMDIŞI MESAJ DEPOLAMA ve TESLİMAT        ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/send_offline")
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


@app.post("/api/send_ws_fallback")
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


@app.get("/api/fetch_messages/{username}")
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
        # Sadece 'message' tipindeki mesajları getir
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

        # ✅ Sadece teslim edilen 'message' tipindeki satırları sil
        if messages:
            await db.execute(
                "DELETE FROM offline_msgs WHERE recipient = ? AND msg_type = 'message'",
                (username,)
            )
            await db.commit()

        return {"messages": messages, "count": len(messages)}


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                REST API — VoIP ICE SUNUCU AYARLARI                ║
# ╚═══════════════════════════════════════════════════════════════════╝

# Kendi VPS'inizde coturn kuruluysa bu ortam değişkenlerini ayarlayın.
# Örnek: TURN_HOST=turn.example.com TURN_USERNAME=relay TURN_CREDENTIAL=gizlisifre
# Ayarlanmazsa sadece Google'ın ücretsiz STUN sunucuları döner.
_TURN_HOST       = os.getenv("TURN_HOST", "")
_TURN_USERNAME   = os.getenv("TURN_USERNAME", "")
_TURN_CREDENTIAL = os.getenv("TURN_CREDENTIAL", "")
_TURN_SECRET     = os.getenv("TURN_SECRET", "")


@app.get("/api/ice_servers")
async def get_ice_servers(
    request: Request,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    WebRTC ICE yapılandırmasını döndürür.

    İstemciler RTCPeerConnection açmadan önce bu endpoint'i çağırır.
    Sunucu asla medya verisine dokunmaz — bu endpoint yalnızca
    P2P bağlantı kurabilmek için gerekli STUN/TURN adreslerini sağlar.

    STUN sunucuları: Google'ın ücretsiz genel STUN sunucuları.
    TURN sunucusu  : Opsiyonel, kendi VPS'iniz. TURN_HOST env değişkeniyle
                     ayarlanmadıysa dönen listede TURN sunucusu bulunmaz.
    """
    await verify_request_signature(request, x_username, x_timestamp, x_signature)

    ice_servers = [
        # Google'ın ücretsiz STUN sunucuları (herhangi bir kayıt veya ücret gerekmez)
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
        {"urls": "stun:stun2.l.google.com:19302"},
    ]

    # Kendi coturn sunucunuz yapılandırılmışsa ekle
    if _TURN_HOST:
        if _TURN_SECRET:
            import time
            import hmac
            import hashlib
            import base64
            
            # 6 hours validity
            expiry = int(time.time()) + 21600
            turn_username = f"{expiry}:{x_username}"
            
            dig = hmac.new(
                _TURN_SECRET.encode("utf-8"),
                turn_username.encode("utf-8"),
                hashlib.sha1
            ).digest()
            turn_credential = base64.b64encode(dig).decode("utf-8")
        else:
            turn_username = _TURN_USERNAME
            turn_credential = _TURN_CREDENTIAL

        ice_servers.append({
            "urls": [
                f"turn:{_TURN_HOST}:3478?transport=udp",
                f"turn:{_TURN_HOST}:3478?transport=tcp",
                f"turns:{_TURN_HOST}:5349",  # TLS üzerinden TURN
            ],
            "username": turn_username,
            "credential": turn_credential,
        })

    return {"ice_servers": ice_servers}


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                  REST API — GRUP YÖNETİMİ                         ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/groups")
async def create_group(
    request: Request,
    req: GroupCreateRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Grup oluşturur ve kurucu dahil belirtilen tüm üyeleri gruba ekler."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != req.creator:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    async with db_session() as db:
        try:
            # Grubu oluştur
            await db.execute(
                "INSERT INTO groups (group_id, group_name, creator) VALUES (?, ?, ?)",
                (req.group_id, req.group_name, req.creator)
            )
            # Kurucuyu üye olarak ekle
            await db.execute(
                "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
                (req.group_id, req.creator)
            )
            # Diğer üyeleri ekle
            for member in req.members:
                await db.execute(
                    "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
                    (req.group_id, member)
                )
            await db.commit()
            return {"status": "ok", "group_id": req.group_id, "message": f"'{req.group_name}' grubu oluşturuldu."}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/groups/{group_id}/members")
async def add_group_member(
    request: Request,
    group_id: str,
    req: GroupAddMemberRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Gruba yeni üye ekler."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        # Grubun varlığını kontrol et
        cursor = await db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,))
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
        
        # Verify x_username is in the group (either creator or member)
        if group["creator"] != x_username:
            cursor = await db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            )
            member = await cursor.fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
        
        # Üyeyi gruba ekle
        await db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
            (group_id, req.username)
        )
        await db.commit()
        return {"status": "ok", "message": f"'{req.username}' gruba eklendi."}


@app.delete("/api/groups/{group_id}/members/{username}")
async def remove_group_member(
    request: Request,
    group_id: str,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Gruptan bir üyeyi çıkarır."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,))
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
            
        # Only group creator can remove others, but any member can leave (delete themselves)
        if x_username != username and group["creator"] != x_username:
            raise HTTPException(status_code=403, detail="Sadece grup kurucusu üyeleri çıkarabilir veya kendi kendinize gruptan çıkabilirsiniz.")
            
        await db.execute(
            "DELETE FROM group_members WHERE group_id = ? AND username = ?",
            (group_id, username)
        )
        await db.commit()
        return {"status": "ok", "message": f"'{username}' gruptan çıkarıldı."}


@app.get("/api/groups/{username}")
async def list_user_groups(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Belirtilen kullanıcının dahil olduğu grupları listeler."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != username:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    async with db_session() as db:
        cursor = await db.execute(
            """SELECT g.group_id, g.group_name, g.creator, g.created_at
               FROM groups g
               JOIN group_members gm ON g.group_id = gm.group_id
               WHERE gm.username = ?""",
               (username,)
        )
        rows = await cursor.fetchall()
        return {
            "groups": [
                {
                    "group_id": r["group_id"],
                    "group_name": r["group_name"],
                    "creator": r["creator"],
                    "created_at": r["created_at"]
                }
                for r in rows
            ]
        }


@app.get("/api/groups/{group_id}/members")
async def list_group_members(
    request: Request,
    group_id: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Bir grubun tüm üyelerini ve public key'lerini döndürür (anahtar dağıtımı için)."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,))
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
            
        # Verify x_username is in the group (either creator or member)
        if group["creator"] != x_username:
            cursor = await db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            )
            member = await cursor.fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
                
        cursor = await db.execute(
            """SELECT u.username, u.public_key
               FROM users u
               JOIN group_members gm ON u.username = gm.username
               WHERE gm.group_id = ?""",
            (group_id,)
        )
        rows = await cursor.fetchall()
        return {
            "members": [
                {
                    "username": r["username"],
                    "public_key": r["public_key"]
                }
                for r in rows
            ]
        }


# ╔═══════════════════════════════════════════════════════════════════╗
# ║           WEBSOCKET — GERÇEK ZAMANLI MESAJ İLETİMİ                ║
# ╚═══════════════════════════════════════════════════════════════════╝

class ConnectionManager:
    """
    Aktif WebSocket bağlantılarını yöneten sınıf.

    Her kullanıcı bağlandığında username → WebSocket eşlemesi yapılır.
    Mesaj geldiğinde alıcı bağlıysa doğrudan iletilir,
    bağlı değilse çevrimdışı kuyruğa (SQLite) yazılır.

    VoIP için ek state:
      active_calls  : { username -> call_id }  — şu an bir aramadaki kullanıcılar
      call_timers   : { call_id -> asyncio.Task } — 30s timeout görevleri
    """

    def __init__(self):
        # Aktif bağlantılar: {username: WebSocket}
        self.active_connections: dict[str, WebSocket] = {}
        # VoIP: arayan/aranan haritası (bellek içi, DB'ye yazılmaz)
        self.active_calls: dict[str, str] = {}       # username -> call_id
        self.call_timers: dict[str, asyncio.Task] = {}  # call_id -> timeout task

    async def connect(self, username: str, websocket: WebSocket):
        """Yeni WebSocket bağlantısını kabul eder ve kayıt altına alır."""
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"[WS] '{username}' connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, username: str):
        """Bağlantıyı kapatır ve listeden çıkarır. Devam eden aramayı da temizler."""
        self.active_connections.pop(username, None)
        # Kullanıcı bir aramanın içindeyse, call_id'yi temizle
        call_id = self.active_calls.pop(username, None)
        if call_id:
            # Timeout task varsa iptal et
            task = self.call_timers.pop(call_id, None)
            if task and not task.done():
                task.cancel()
        print(f"[WS] '{username}' disconnected. Active connections: {len(self.active_connections)}")

    def is_online(self, username: str) -> bool:
        """Kullanıcının şu anda bağlı olup olmadığını kontrol eder."""
        return username in self.active_connections

    def is_in_call(self, username: str) -> bool:
        """Kullanıcının şu anda bir aramada olup olmadığını kontrol eder."""
        return username in self.active_calls

    def start_call(self, caller: str, callee: str, call_id: str):
        """Aramayı başlat — her iki tarafı active_calls'a ekle."""
        self.active_calls[caller] = call_id
        self.active_calls[callee] = call_id

    def end_call(self, call_id: str):
        """Aramayı sonlandır — her iki tarafı active_calls'tan çıkar."""
        # call_id üzerinden her iki kullanıcıyı bul ve sil
        to_remove = [u for u, cid in self.active_calls.items() if cid == call_id]
        for u in to_remove:
            self.active_calls.pop(u, None)
        # Timeout task varsa iptal et
        task = self.call_timers.pop(call_id, None)
        if task and not task.done():
            task.cancel()

    async def send_to_user(self, username: str, message: dict):
        """Belirli bir kullanıcıya JSON mesaj gönderir."""
        ws = self.active_connections.get(username)
        if ws:
            await ws.send_json(message)
            return True
        return False


# Global bağlantı yöneticisi
manager = ConnectionManager()


@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    """
    WebSocket uç noktası — gerçek zamanlı mesajlaşma ve challenge-response auth.
    """
    await websocket.accept()

    try:
        # Challenge-Response auth flow:
        # 1. Generate challenge nonce
        challenge = str(uuid_lib.uuid4())
        # 2. Send challenge to client
        await websocket.send_json({"type": "challenge", "challenge": challenge})
        
        # 3. Wait for client response with timeout of 5 seconds
        auth_data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        auth_msg = json.loads(auth_data)
        
        if auth_msg.get("type") != "auth" or "signature" not in auth_msg:
            await websocket.send_json({"type": "auth_result", "status": "failed", "message": "Geçersiz kimlik doğrulama paketi."})
            await websocket.close(code=4003)
            return
            
        signature = auth_msg["signature"]
        
        # 4. Verify signature using stored public key
        async with db_session() as db:
            cursor = await db.execute("SELECT public_key FROM users WHERE username = ?", (username,))
            row = await cursor.fetchone()
            if not row:
                await websocket.send_json({"type": "auth_result", "status": "failed", "message": "Kullanıcı bulunamadı."})
                await websocket.close(code=4001)
                return
            pub_key_pem = row["public_key"]
            
        try:
            pub_key = pem_string_to_public_key(pub_key_pem)
            sig_bytes = base64.b64decode(signature)
            data_to_verify = challenge.encode("utf-8")
            if not verify_signature(pub_key, sig_bytes, data_to_verify):
                await websocket.send_json({"type": "auth_result", "status": "failed", "message": "Geçersiz imza."})
                await websocket.close(code=4002)
                return
        except Exception as e:
            await websocket.send_json({"type": "auth_result", "status": "failed", "message": f"Doğrulama hatası: {e}"})
            await websocket.close(code=4002)
            return
            
        # 5. Handshake successful, register the socket
        manager.active_connections[username] = websocket
        print(f"[WS] '{username}' authenticated successfully. Active connections: {len(manager.active_connections)}")
        
        await websocket.send_json({"type": "auth_result", "status": "success"})
        
    except asyncio.TimeoutError:
        print(f"[WS] Timeout waiting for challenge response from '{username}'")
        try:
            await websocket.close(code=4008)
        except:
            pass
        return
    except Exception as e:
        print(f"[WS Handshake Error] {e}")
        try:
            await websocket.close(code=4000)
        except:
            pass
        return

    # ── Bağlantı kurulunca bekleyen offline mesajları gönder ──
    await _deliver_pending_messages(username)

    try:
        while True:
            # İstemciden gelen JSON mesajını oku
            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type", "")

            if msg_type == "message":
                recipient = message.get("recipient", "")
                sender = username
                encrypted_payload = message.get("encrypted_payload", "")
                view_once = bool(message.get("view_once", False))
                timestamp = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                if manager.is_online(recipient):
                    await manager.send_to_user(recipient, {
                        "type": "message",
                        "sender": sender,
                        "encrypted_payload": encrypted_payload,
                        "view_once": view_once,
                        "timestamp": timestamp,
                    })
                    await manager.send_to_user(sender, {
                        "type": "delivery_ack",
                        "recipient": recipient,
                        "status": "delivered_online",
                    })
                else:
                    async with db_session() as db:
                        await db.execute(
                            """INSERT INTO offline_msgs
                               (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                               VALUES (?, ?, ?, 'message', ?, ?)""",
                            (sender, recipient, encrypted_payload,
                             json.dumps({"view_once": view_once}),
                             timestamp)
                        )
                        await db.commit()
                        print(f"[Server] Stored offline message from '{sender}' to '{recipient}'")
                    await manager.send_to_user(sender, {
                        "type": "delivery_ack",
                        "recipient": recipient,
                        "status": "stored_offline",
                    })

            elif msg_type == "file_message":
                recipient    = message.get("recipient", "")
                sender       = username
                file_uuid    = message.get("file_uuid", "")
                original_name = message.get("original_name", "dosya")
                file_type    = message.get("file_type", "document")
                view_once    = bool(message.get("view_once", False))
                timestamp    = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                file_msg = {
                    "type":          "file_message",
                    "sender":        sender,
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
                            (sender, recipient, json.dumps(file_msg), timestamp)
                        )
                        await db.commit()

            elif msg_type == "ephemeral_toggle":
                recipient = message.get("recipient", "")
                sender = username
                ephemeral = bool(message.get("ephemeral", False))
                chat_id = _make_chat_id(sender, recipient)
                ts = datetime.now(timezone.utc).isoformat()

                async with db_session() as db:
                    # Sunucu tarafında chat_settings güncelle
                    await db.execute(
                        """INSERT INTO chat_settings (chat_id, ephemeral, changed_by, changed_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(chat_id) DO UPDATE SET
                               ephemeral=excluded.ephemeral,
                               changed_by=excluded.changed_by,
                               changed_at=excluded.changed_at""",
                        (chat_id, 1 if ephemeral else 0, sender, ts)
                    )
                    await db.commit()

                    toggle_payload = {
                        "type": "ephemeral_toggle",
                        "sender": sender,
                        "ephemeral": ephemeral,
                        "timestamp": ts,
                    }

                    if manager.is_online(recipient):
                        # Alıcı online → doğrudan ilet
                        await manager.send_to_user(recipient, toggle_payload)
                    else:
                        # Alıcı offline → kuyruğa ekle (bağlanınca teslim edilecek)
                        async with db_session() as db_inner:
                            await db_inner.execute(
                                """INSERT INTO offline_msgs
                                   (sender, recipient, msg_type, extra_data, timestamp)
                                   VALUES (?, ?, 'ephemeral_toggle', ?, ?)""",
                                (sender, recipient, json.dumps({"ephemeral": ephemeral}), ts)
                            )
                            await db_inner.commit()

            elif msg_type == "group_key_dist":
                recipient = message.get("recipient", "")
                sender = username
                encrypted_payload = message.get("encrypted_payload", "")
                group_id = message.get("group_id", "")
                timestamp = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                dist_payload = {
                    "type": "group_key_dist",
                    "sender": sender,
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
                            (sender, recipient, encrypted_payload, json.dumps({"group_id": group_id}), timestamp)
                        )
                        await db.commit()

            elif msg_type == "read_receipt":
                recipient = message.get("recipient", "")
                sender = username
                timestamp = message.get("timestamp", "")

                receipt_payload = {
                    "type": "read_receipt",
                    "sender": sender,
                    "timestamp": timestamp,
                }

                if manager.is_online(recipient):
                    await manager.send_to_user(recipient, receipt_payload)
                else:
                    async with db_session() as db:
                        await db.execute(
                            """INSERT INTO offline_msgs
                               (sender, recipient, msg_type, extra_data, timestamp)
                               VALUES (?, ?, 'read_receipt', ?, ?)""",
                            (sender, recipient, json.dumps({"timestamp": timestamp}), timestamp)
                        )
                        await db.commit()

            elif msg_type == "group_message":
                group_id = message.get("group_id", "")
                sender = username
                encrypted_payload = message.get("encrypted_payload", "")
                # Alıcı tarafında grup taklit koruması için gönderilen imza:
                signature = message.get("signature", "") 

                async with db_session() as db:
                    cursor = await db.execute(
                        "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                        (group_id, sender)
                    )
                    sender_member = await cursor.fetchone()
                    if not sender_member:
                        await manager.send_to_user(sender, {
                            "type": "delivery_ack",
                            "recipient": group_id,
                            "status": "rejected_not_group_member",
                        })
                        continue
                    
                    cursor = await db.execute(
                        "SELECT username FROM group_members WHERE group_id = ?", (group_id,)
                    )
                    members = await cursor.fetchall()

                timestamp = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                group_msg_payload = {
                    "type": "group_message",
                    "sender": sender,
                    "group_id": group_id,
                    "encrypted_payload": encrypted_payload,
                    "signature": signature,  # Korumayı alıcıya iletiyoruz
                    "timestamp": timestamp,
                }

                # Tek bir db bağlantısı üzerinden offline üyeleri kaydedelim (daha performanslı)
                async with db_session() as db:
                    for m in members:
                        recipient = m["username"]
                        if recipient == sender:
                            continue
                        if manager.is_online(recipient):
                            await manager.send_to_user(recipient, group_msg_payload)
                        else:
                            await db.execute(
                                """INSERT INTO offline_msgs
                                   (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                                   VALUES (?, ?, ?, 'group_message', ?, ?)""",
                                (sender, recipient, encrypted_payload, 
                                 json.dumps({"group_id": group_id, "signature": signature}),
                                 timestamp)
                            )
                    await db.commit()

            elif msg_type == "ping":
                # Bağlantı canlılık kontrolü
                await manager.send_to_user(username, {"type": "pong"})

            # ═══════════════════════════════════════════════════════════
            #  VoIP SİNYAL RELAY (Faz 1)
            #  Sunucu SDP/ICE içeriğini okumaz veya saklamaz.
            #  Sadece paketleri alıcıya yönlendirir (pure relay).
            # ═══════════════════════════════════════════════════════════

            elif msg_type == "call_offer":
                recipient  = message.get("recipient", "")
                call_id    = message.get("call_id", str(uuid_lib.uuid4()))
                call_type  = message.get("call_type", "audio")  # "audio" | "video"
                sdp_offer  = message.get("sdp_offer", "")
                ts         = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                # Aranan kişi çevrimdışı mı?
                if not manager.is_online(recipient):
                    await manager.send_to_user(username, {
                        "type": "call_reject",
                        "call_id": call_id,
                        "reason": "unavailable",
                        "timestamp": ts,
                    })

                # Arayan zaten bir aramada mı?
                elif manager.is_in_call(username):
                    await manager.send_to_user(username, {
                        "type": "call_reject",
                        "call_id": call_id,
                        "reason": "busy_caller",
                        "timestamp": ts,
                    })

                # Aranan zaten bir aramada mı?
                elif manager.is_in_call(recipient):
                    await manager.send_to_user(username, {
                        "type": "call_reject",
                        "call_id": call_id,
                        "reason": "busy",
                        "timestamp": ts,
                    })

                else:
                    # Her iki tarafı da active_calls'a ekle
                    manager.start_call(username, recipient, call_id)

                    # Aramayı karşı tarafa ilet
                    await manager.send_to_user(recipient, {
                        "type": "call_offer",
                        "caller": username,
                        "call_id": call_id,
                        "call_type": call_type,
                        "sdp_offer": sdp_offer,
                        "timestamp": ts,
                    })

                    # 30 saniyelik cevap bekleme zamanlayıcısı
                    async def _call_timeout(cid: str, caller: str, callee: str):
                        await asyncio.sleep(30)
                        # Hâlâ aktif mi?
                        if cid in manager.call_timers:
                            manager.end_call(cid)
                            timeout_ts = datetime.now(timezone.utc).isoformat()
                            await manager.send_to_user(caller, {
                                "type": "call_reject",
                                "call_id": cid,
                                "reason": "timeout",
                                "timestamp": timeout_ts,
                            })
                            await manager.send_to_user(callee, {
                                "type": "call_reject",
                                "call_id": cid,
                                "reason": "timeout",
                                "timestamp": timeout_ts,
                            })
                            print(f"[VoIP] Call {cid} timed out (no answer in 30s).")

                    task = asyncio.create_task(_call_timeout(call_id, username, recipient))
                    manager.call_timers[call_id] = task
                    print(f"[VoIP] call_offer relayed: {username} → {recipient} (call_id={call_id}, type={call_type})")

            elif msg_type == "call_answer":
                recipient  = message.get("recipient", "")  # = arayan (caller)
                call_id    = message.get("call_id", "")
                sdp_answer = message.get("sdp_answer", "")
                ts         = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                # Timeout zamanlayıcısını iptal et — cevap geldi
                task = manager.call_timers.pop(call_id, None)
                if task and not task.done():
                    task.cancel()

                # SDP cevabını arayana ilet
                await manager.send_to_user(recipient, {
                    "type": "call_answer",
                    "callee": username,
                    "call_id": call_id,
                    "sdp_answer": sdp_answer,
                    "timestamp": ts,
                })
                print(f"[VoIP] call_answer relayed: {username} → {recipient} (call_id={call_id})")

            elif msg_type == "call_reject":
                recipient = message.get("recipient", "")
                call_id   = message.get("call_id", "")
                reason    = message.get("reason", "rejected")
                ts        = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                # Aramayı sonlandır
                manager.end_call(call_id)

                await manager.send_to_user(recipient, {
                    "type": "call_reject",
                    "call_id": call_id,
                    "reason": reason,
                    "timestamp": ts,
                })
                print(f"[VoIP] call_reject relayed: {username} → {recipient} (reason={reason})")

            elif msg_type == "call_end":
                recipient        = message.get("recipient", "")
                call_id          = message.get("call_id", "")
                duration_seconds = message.get("duration_seconds", 0)
                ts               = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                # Aramayı sonlandır
                manager.end_call(call_id)

                await manager.send_to_user(recipient, {
                    "type": "call_end",
                    "call_id": call_id,
                    "duration_seconds": duration_seconds,
                    "timestamp": ts,
                })
                print(f"[VoIP] call_end relayed: {username} → {recipient} (duration={duration_seconds}s)")

            elif msg_type == "ice_candidate":
                # ICE candidate değişimi — NAT traversal için
                # Sunucu sadece relay eder, içeriği okumaz
                recipient       = message.get("recipient", "")
                call_id         = message.get("call_id", "")
                candidate       = message.get("candidate", "")
                sdp_mid         = message.get("sdp_mid", "")
                sdp_mline_index = message.get("sdp_mline_index", 0)

                await manager.send_to_user(recipient, {
                    "type": "ice_candidate",
                    "sender": username,
                    "call_id": call_id,
                    "candidate": candidate,
                    "sdp_mid": sdp_mid,
                    "sdp_mline_index": sdp_mline_index,
                })

    except WebSocketDisconnect:
        manager.disconnect(username)
    except Exception as e:
        print(f"[WS Error] WebSocket error for '{username}': {e}")
        manager.disconnect(username)


async def _deliver_pending_messages(username: str):
    """
    Kullanıcı WebSocket'e bağlandığında bekleyen offline
    mesajlarını teslim eder ve veritabanından siler.
    """
    async with db_session() as db:
        cursor = await db.execute(
            "SELECT id, sender, encrypted_payload, msg_type, extra_data, timestamp FROM offline_msgs WHERE recipient = ? ORDER BY timestamp ASC",
            (username,)
        )
        rows = await cursor.fetchall()

        for row in rows:
            row_type = row["msg_type"]

            if row_type == "message":
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, {
                    "type": "message",
                    "sender": row["sender"],
                    "encrypted_payload": row["encrypted_payload"],
                    "view_once": extra.get("view_once", False),
                    "timestamp": row["timestamp"],
                })
            elif row_type == "file_message":
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, extra)  # extra icinde tam file_msg var
            elif row_type == "ephemeral_toggle":
                # Ephemeral toggle sistem mesajı — kullanıcı offline'dayken değişti
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, {
                    "type": "ephemeral_toggle",
                    "sender": row["sender"],
                    "ephemeral": extra.get("ephemeral", False),
                    "timestamp": row["timestamp"],
                })
            elif row_type == "group_key_dist":
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, {
                    "type": "group_key_dist",
                    "sender": row["sender"],
                    "group_id": extra.get("group_id", ""),
                    "encrypted_payload": row["encrypted_payload"],
                    "timestamp": row["timestamp"],
                })
            elif row_type == "group_message":
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, {
                    "type": "group_message",
                    "sender": row["sender"],
                    "group_id": extra.get("group_id", ""),
                    "encrypted_payload": row["encrypted_payload"],
                    "signature": extra.get("signature", ""),  # Offline mesajdan da imzayı iletiyoruz
                    "timestamp": row["timestamp"],
                })
            elif row_type == "read_receipt":
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, {
                    "type": "read_receipt",
                    "sender": row["sender"],
                    "timestamp": extra.get("timestamp", ""),
                })

        # ✅ Teslim edilen tüm kuyruğu sil (Zero-Knowledge)
        if rows:
            await db.execute(
                "DELETE FROM offline_msgs WHERE recipient = ?",
                (username,)
            )
            await db.commit()
            print(f"[Server] Delivered and deleted {len(rows)} pending messages for '{username}'.")

# Yerel static web istemcisini ana dizinde sun
try:
    from fastapi.staticfiles import StaticFiles
    import os
    os.makedirs("static", exist_ok=True)
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    print("[Server] Static web client mounted successfully at '/'")
except Exception as mount_ex:
    print(f"[Warning] Static client mount error: {mount_ex}")

# ╔═══════════════════════════════════════════════════════════════════╗
# ║                        SUNUCU BAŞLATMA                            ║
# ╚═══════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  HybridP2P Messenger - Relay Server")
    print(f"  REST API: http://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/docs")
    print(f"  WebSocket: ws://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/ws/{{username}}")
    print("=" * 60)

    uvicorn.run(
        "server:app",
        host=HOST,
        port=PORT,
        reload=True,           # Geliştirme modunda otomatik yeniden yükleme
        log_level="info",
    )
