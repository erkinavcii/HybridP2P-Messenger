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
  4. Bu yaklaşım "Zero-Knowledge Server" mimarisini sağlar.
"""

import os
import json
import asyncio
import sqlite3
import uuid as uuid_lib
import base64
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from crypto_utils import verify_signature, pem_string_to_public_key


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                      VERİTABANI KATMANI                          ║
# ╚═══════════════════════════════════════════════════════════════════╝

DB_PATH = "relay_server.db"


def init_database():
    """
    SQLite veritabanını başlatır ve gerekli tabloları oluşturur.
    Uygulama her başlatıldığında güvenli bir şekilde çağrılabilir
    (IF NOT EXISTS sayesinde).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Kullanıcılar tablosu — public key'leri saklar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username    TEXT PRIMARY KEY,
            public_key  TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Çevrimdışı mesajlar tablosu — şifreli payload'ları geçici saklar
    # msg_type: 'message' | 'ephemeral_toggle' | 'system' | 'group_key_dist' | 'group_message'
    cursor.execute("""
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id     TEXT PRIMARY KEY,
            ephemeral   INTEGER NOT NULL DEFAULT 0,
            changed_by  TEXT,
            changed_at  TEXT
        )
    """)

    # Dosya depolama — sifrelenmis dosya blob'larini gecici saklar
    # download sonrasi silinir (Zero-Knowledge)
    cursor.execute("""
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            group_id    TEXT PRIMARY KEY,
            group_name  TEXT NOT NULL,
            creator     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Grup üyeleri tablosu — grup üyeliklerini saklar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id    TEXT NOT NULL,
            username    TEXT NOT NULL,
            PRIMARY KEY (group_id, username),
            FOREIGN KEY (group_id) REFERENCES groups(group_id),
            FOREIGN KEY (username) REFERENCES users(username)
        )
    """)

    conn.commit()
    conn.close()


def get_db():
    """Thread-safe veritabanı bağlantısı döndürür."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Sütun ismiyle erişim için
    return conn


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                  FastAPI UYGULAMA YAŞAM DÖNGÜSÜ                  ║
# ╚═══════════════════════════════════════════════════════════════════╝

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama başlarken veritabanını oluşturur."""
    init_database()
    print("[DB] Database ready. Relay server started.")
    yield
    print("[Server] Relay server shutting down.")


app = FastAPI(
    title="HybridP2P Messenger — Röle Sunucusu",
    description="Zero-Knowledge Store-and-Forward Relay Server",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — Farklı kökenlerden gelen isteklere izin ver (geliştirme için)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


# ╔═══════════════════════════════════════════════════════════════════╗
# ║              REST API — KULLANICI ve ANAHTAR YÖNETİMİ            ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/register")
async def register_user(req: UserRegisterRequest):
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

    db = get_db()
    try:
        # Kullanıcı zaten var mı kontrol et
        existing = db.execute(
            "SELECT username, public_key FROM users WHERE username = ?",
            (req.username,)
        ).fetchone()

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
                db.execute(
                    "UPDATE users SET public_key = ? WHERE username = ?",
                    (req.public_key, req.username)
                )
                db.commit()
                return {"status": "updated", "message": f"'{req.username}' kullanıcısının public key'i güncellendi."}
        else:
            # Yeni kayıt. Gönderilen yeni key ile imzayı doğrula.
            pub_key = pem_string_to_public_key(req.public_key)
            sig_bytes = base64.b64decode(req.signature)
            if not verify_signature(pub_key, sig_bytes, data_to_verify):
                raise HTTPException(status_code=401, detail="Geçersiz imza. Gönderilen açık anahtarla eşleşen özel anahtar kanıtlanamadı.")
            
            db.execute(
                "INSERT INTO users (username, public_key) VALUES (?, ?)",
                (req.username, req.public_key)
            )
            db.commit()
            return {"status": "created", "message": f"'{req.username}' başarıyla kaydedildi."}
    finally:
        db.close()


def verify_request_signature(username: str, timestamp: str, signature: str):
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

    db = get_db()
    try:
        row = db.execute("SELECT public_key FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı.")
        pub_key_pem = row["public_key"]
    finally:
        db.close()

    try:
        pub_key = pem_string_to_public_key(pub_key_pem)
        sig_bytes = base64.b64decode(signature)
        data_to_verify = f"{username}:{timestamp}".encode("utf-8")
        if not verify_signature(pub_key, sig_bytes, data_to_verify):
            raise HTTPException(status_code=401, detail="Geçersiz imza.")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Kimlik doğrulama hatası: {e}")


@app.get("/api/public_key/{username}")
async def get_public_key(
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Belirtilen kullanıcının public key'ini döndürür.
    """
    verify_request_signature(x_username, x_timestamp, x_signature)
    db = get_db()
    try:
        row = db.execute(
            "SELECT public_key FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"'{username}' adlı kullanıcı bulunamadı."
            )

        return {"username": username, "public_key": row["public_key"]}
    finally:
        db.close()


@app.get("/api/users")
async def list_users(
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Kayıtlı tüm kullanıcıları listeler.
    """
    verify_request_signature(x_username, x_timestamp, x_signature)
    db = get_db()
    try:
        rows = db.execute("SELECT username, created_at FROM users").fetchall()
        return {
            "users": [
                {"username": r["username"], "created_at": r["created_at"]}
                for r in rows
            ]
        }
    finally:
        db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║               REST API — CHAT AYARLARI (EPHEMERAL)               ║
# ╚═══════════════════════════════════════════════════════════════════╝

def _make_chat_id(user1: str, user2: str) -> str:
    """İki kullanıcı için tutarlı chat_id üretir (sıralı)."""
    return "_".join(sorted([user1, user2]))


@app.get("/api/chat_settings/{username}")
async def get_chat_settings(username: str):
    """
    Kullanıcının tüm chat ephemeral ayarlarını döndürür.
    İstemci bağlandığında bu endpoint'i çağırarak yerel durumunu senkronize eder.
    """
    db = get_db()
    try:
        # Bu kullanıcıyı içeren tüm chat_settings satırlarını al
        rows = db.execute(
            """SELECT chat_id, ephemeral, changed_by, changed_at
               FROM chat_settings
               WHERE chat_id LIKE ? OR chat_id LIKE ?""",
            (f"{username}_%", f"%_{username}")
        ).fetchall()
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
    finally:
        db.close()


@app.post("/api/ephemeral_toggle")
async def rest_ephemeral_toggle(req: EphemeralToggleRequest):
    """
    REST üzerinden ephemeral toggle (WebSocket bağlantısı yokken fallback).
    Online kullanıcıya WebSocket üzerinden de iletir.
    """
    chat_id = _make_chat_id(req.sender, req.recipient)
    ts = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        # chat_settings güncelle
        db.execute(
            """INSERT INTO chat_settings (chat_id, ephemeral, changed_by, changed_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   ephemeral=excluded.ephemeral,
                   changed_by=excluded.changed_by,
                   changed_at=excluded.changed_at""",
            (chat_id, 1 if req.ephemeral else 0, req.sender, ts)
        )
        db.commit()

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
            db.execute(
                """INSERT INTO offline_msgs
                   (sender, recipient, msg_type, extra_data, timestamp)
                   VALUES (?, ?, 'ephemeral_toggle', ?, ?)""",
                (req.sender, req.recipient, json.dumps({"ephemeral": req.ephemeral}), ts)
            )
            db.commit()

        return {"status": "ok", "ephemeral": req.ephemeral, "chat_id": chat_id}
    finally:
        db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                DOSYA YUKLEME / INDIRME (E2EE)                    ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/upload_file")
async def upload_file(req: FileUploadRequest):
    """
    Sifrelenmis dosyayi alir, UUID ile veritabanina kaydeder.
    Sunucu dosya icerigini gormez — sadece sifrelenmis blob saklar (Zero-Knowledge).
    Dosya max ~10MB olmali (base64 encode ~%33 buyutur).
    """
    file_uuid = str(uuid_lib.uuid4())
    db = get_db()
    try:
        db.execute(
            """INSERT INTO file_store
               (uuid, sender, recipient, encrypted_data, original_name, file_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_uuid, req.sender, req.recipient,
             req.encrypted_data, req.original_name, req.file_type)
        )
        db.commit()
        return {"uuid": file_uuid, "status": "uploaded"}
    finally:
        db.close()


@app.get("/api/download_file/{file_uuid}")
async def download_file(file_uuid: str):
    """
    Sifrelenmis dosyayi verir ve veritabanindan siler (Zero-Knowledge).
    Her dosya yalnizca bir kez indirilebilir.
    """
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM file_store WHERE uuid = ?", (file_uuid,)
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="Dosya bulunamadi veya zaten indirildi."
            )
        result = {
            "uuid":           row["uuid"],
            "sender":         row["sender"],
            "encrypted_data": row["encrypted_data"],
            "original_name":  row["original_name"],
            "file_type":      row["file_type"],
            "timestamp":      row["timestamp"],
        }
        # Zero-Knowledge: indirme sonrasi kalici sil
        db.execute("DELETE FROM file_store WHERE uuid = ?", (file_uuid,))
        db.commit()
        return result
    finally:
        db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║          REST API — ÇEVRİMDIŞI MESAJ DEPOLAMA ve TESLİMAT        ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/send_offline")
async def send_offline_message(
    req: SendMessageRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Alıcı çevrimdışıyken mesajı veritabanına kaydeder.
    """
    verify_request_signature(x_username, x_timestamp, x_signature)
    if x_username != req.sender:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    db = get_db()
    try:
        # Alıcının kayıtlı olup olmadığını kontrol et
        recipient = db.execute(
            "SELECT username FROM users WHERE username = ?",
            (req.recipient,)
        ).fetchone()

        if not recipient:
            raise HTTPException(
                status_code=404,
                detail=f"Alıcı '{req.recipient}' kayıtlı değil."
            )

        # Şifreli mesajı veritabanına kaydet
        db.execute(
            "INSERT INTO offline_msgs (sender, recipient, encrypted_payload, timestamp) VALUES (?, ?, ?, ?)",
            (req.sender, req.recipient, req.encrypted_payload, datetime.now(timezone.utc).isoformat())
        )
        db.commit()
        print(f"[Server] Stored offline message (REST) from '{req.sender}' to '{req.recipient}'")
        return {"status": "stored", "message": "Mesaj şifreli olarak saklandı."}
    finally:
        db.close()


@app.get("/api/fetch_messages/{username}")
async def fetch_offline_messages(
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """
    Kullanıcının bekleyen çevrimdışı mesajlarını döndürür ve
    veritabanından kalıcı olarak siler (Zero-Knowledge prensibi).
    """
    verify_request_signature(x_username, x_timestamp, x_signature)
    if x_username != username:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, sender, encrypted_payload, timestamp FROM offline_msgs WHERE recipient = ? ORDER BY timestamp ASC",
            (username,)
        ).fetchall()

        messages = [
            {
                "id": r["id"],
                "sender": r["sender"],
                "encrypted_payload": r["encrypted_payload"],
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]

        # ✅ Mesajlar teslim edildi → veritabanından kalıcı olarak sil
        if messages:
            db.execute(
                "DELETE FROM offline_msgs WHERE recipient = ?",
                (username,)
            )
            db.commit()

        return {"messages": messages, "count": len(messages)}
    finally:
        db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                  REST API — GRUP YÖNETİMİ                         ║
# ╚═══════════════════════════════════════════════════════════════════╝

@app.post("/api/groups")
async def create_group(
    req: GroupCreateRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Grup oluşturur ve kurucu dahil belirtilen tüm üyeleri gruba ekler."""
    verify_request_signature(x_username, x_timestamp, x_signature)
    if x_username != req.creator:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    db = get_db()
    try:
        # Grubu oluştur
        db.execute(
            "INSERT INTO groups (group_id, group_name, creator) VALUES (?, ?, ?)",
            (req.group_id, req.group_name, req.creator)
        )
        # Kurucuyu üye olarak ekle
        db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
            (req.group_id, req.creator)
        )
        # Diğer üyeleri ekle
        for member in req.members:
            db.execute(
                "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
                (req.group_id, member)
            )
        db.commit()
        return {"status": "ok", "group_id": req.group_id, "message": f"'{req.group_name}' grubu oluşturuldu."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@app.post("/api/groups/{group_id}/members")
async def add_group_member(
    group_id: str,
    req: GroupAddMemberRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Gruba yeni üye ekler."""
    verify_request_signature(x_username, x_timestamp, x_signature)
    db = get_db()
    try:
        # Grubun varlığını kontrol et
        group = db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
        
        # Verify x_username is in the group (either creator or member)
        if group["creator"] != x_username:
            member = db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            ).fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
        
        # Üyeyi gruba ekle
        db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
            (group_id, req.username)
        )
        db.commit()
        return {"status": "ok", "message": f"'{req.username}' gruba eklendi."}
    finally:
        db.close()


@app.delete("/api/groups/{group_id}/members/{username}")
async def remove_group_member(
    group_id: str,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Gruptan bir üyeyi çıkarır."""
    verify_request_signature(x_username, x_timestamp, x_signature)
    db = get_db()
    try:
        group = db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
            
        # Only group creator can remove others, but any member can leave (delete themselves)
        if x_username != username and group["creator"] != x_username:
            raise HTTPException(status_code=403, detail="Sadece grup kurucusu üyeleri çıkarabilir veya kendi kendinize gruptan çıkabilirsiniz.")
            
        db.execute(
            "DELETE FROM group_members WHERE group_id = ? AND username = ?",
            (group_id, username)
        )
        db.commit()
        return {"status": "ok", "message": f"'{username}' gruptan çıkarıldı."}
    finally:
        db.close()


@app.get("/api/groups/{username}")
async def list_user_groups(
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Belirtilen kullanıcının dahil olduğu grupları listeler."""
    verify_request_signature(x_username, x_timestamp, x_signature)
    if x_username != username:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    db = get_db()
    try:
        rows = db.execute(
            """SELECT g.group_id, g.group_name, g.creator, g.created_at
               FROM groups g
               JOIN group_members gm ON g.group_id = gm.group_id
               WHERE gm.username = ?""",
               (username,)
        ).fetchall()
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
    finally:
        db.close()


@app.get("/api/groups/{group_id}/members")
async def list_group_members(
    group_id: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Bir grubun tüm üyelerini ve public key'lerini döndürür (anahtar dağıtımı için)."""
    verify_request_signature(x_username, x_timestamp, x_signature)
    db = get_db()
    try:
        group = db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
            
        # Verify x_username is in the group (either creator or member)
        if group["creator"] != x_username:
            member = db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            ).fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
                
        rows = db.execute(
            """SELECT u.username, u.public_key
               FROM users u
               JOIN group_members gm ON u.username = gm.username
               WHERE gm.group_id = ?""",
            (group_id,)
        ).fetchall()
        return {
            "members": [
                {
                    "username": r["username"],
                    "public_key": r["public_key"]
                }
                for r in rows
            ]
        }
    finally:
        db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║           WEBSOCKET — GERÇEK ZAMANLI MESAJ İLETİMİ                ║
# ╚═══════════════════════════════════════════════════════════════════╝

class ConnectionManager:
    """
    Aktif WebSocket bağlantılarını yöneten sınıf.

    Her kullanıcı bağlandığında username → WebSocket eşlemesi yapılır.
    Mesaj geldiğinde alıcı bağlıysa doğrudan iletilir,
    bağlı değilse çevrimdışı kuyruğa (SQLite) yazılır.
    """

    def __init__(self):
        # Aktif bağlantılar: {username: WebSocket}
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, username: str, websocket: WebSocket):
        """Yeni WebSocket bağlantısını kabul eder ve kayıt altına alır."""
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"[WS] '{username}' connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, username: str):
        """Bağlantıyı kapatır ve listeden çıkarır."""
        self.active_connections.pop(username, None)
        print(f"[WS] '{username}' disconnected. Active connections: {len(self.active_connections)}")

    def is_online(self, username: str) -> bool:
        """Kullanıcının şu anda bağlı olup olmadığını kontrol eder."""
        return username in self.active_connections

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
        db = get_db()
        try:
            row = db.execute("SELECT public_key FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                await websocket.send_json({"type": "auth_result", "status": "failed", "message": "Kullanıcı bulunamadı."})
                await websocket.close(code=4001)
                return
            pub_key_pem = row["public_key"]
        finally:
            db.close()
            
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
                sender = message.get("sender", username)
                encrypted_payload = message.get("encrypted_payload", "")
                view_once = bool(message.get("view_once", False))

                if manager.is_online(recipient):
                    await manager.send_to_user(recipient, {
                        "type": "message",
                        "sender": sender,
                        "encrypted_payload": encrypted_payload,
                        "view_once": view_once,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    await manager.send_to_user(sender, {
                        "type": "delivery_ack",
                        "recipient": recipient,
                        "status": "delivered_online",
                    })
                else:
                    db = get_db()
                    try:
                        db.execute(
                            """INSERT INTO offline_msgs
                               (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                               VALUES (?, ?, ?, 'message', ?, ?)""",
                            (sender, recipient, encrypted_payload,
                             json.dumps({"view_once": view_once}),
                             datetime.now(timezone.utc).isoformat())
                        )
                        db.commit()
                        print(f"[Server] Stored offline message from '{sender}' to '{recipient}'")
                    finally:
                        db.close()
                    await manager.send_to_user(sender, {
                        "type": "delivery_ack",
                        "recipient": recipient,
                        "status": "stored_offline",
                    })

            elif msg_type == "file_message":
                # Dosya mesaji: sunucu sadece metadata'yi iletir (dosya zaten upload edildi)
                recipient    = message.get("recipient", "")
                sender       = message.get("sender", username)
                file_uuid    = message.get("file_uuid", "")
                original_name = message.get("original_name", "dosya")
                file_type    = message.get("file_type", "document")
                view_once    = bool(message.get("view_once", False))

                file_msg = {
                    "type":          "file_message",
                    "sender":        sender,
                    "file_uuid":     file_uuid,
                    "original_name": original_name,
                    "file_type":     file_type,
                    "view_once":     view_once,
                    "timestamp":     datetime.now(timezone.utc).isoformat(),
                }

                if manager.is_online(recipient):
                    await manager.send_to_user(recipient, file_msg)
                else:
                    db = get_db()
                    try:
                        db.execute(
                            """INSERT INTO offline_msgs
                               (sender, recipient, msg_type, extra_data, timestamp)
                               VALUES (?, ?, 'file_message', ?, ?)""",
                            (sender, recipient, json.dumps(file_msg),
                             datetime.now(timezone.utc).isoformat())
                        )
                        db.commit()
                    finally:
                        db.close()

            elif msg_type == "ephemeral_toggle":
                # ── Ephemeral mod toggle: hem DB'yi güncelle hem alıcıya ilet ──
                recipient = message.get("recipient", "")
                sender = message.get("sender", username)
                ephemeral = bool(message.get("ephemeral", False))
                chat_id = _make_chat_id(sender, recipient)
                ts = datetime.now(timezone.utc).isoformat()

                db = get_db()
                try:
                    # Sunucu tarafında chat_settings güncelle
                    db.execute(
                        """INSERT INTO chat_settings (chat_id, ephemeral, changed_by, changed_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(chat_id) DO UPDATE SET
                               ephemeral=excluded.ephemeral,
                               changed_by=excluded.changed_by,
                               changed_at=excluded.changed_at""",
                        (chat_id, 1 if ephemeral else 0, sender, ts)
                    )
                    db.commit()

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
                        db.execute(
                            """INSERT INTO offline_msgs
                               (sender, recipient, msg_type, extra_data, timestamp)
                               VALUES (?, ?, 'ephemeral_toggle', ?, ?)""",
                            (sender, recipient, json.dumps({"ephemeral": ephemeral}), ts)
                        )
                        db.commit()
                finally:
                    db.close()

            elif msg_type == "group_key_dist":
                recipient = message.get("recipient", "")
                sender = message.get("sender", username)
                encrypted_payload = message.get("encrypted_payload", "")
                group_id = message.get("group_id", "")

                dist_payload = {
                    "type": "group_key_dist",
                    "sender": sender,
                    "group_id": group_id,
                    "encrypted_payload": encrypted_payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                if manager.is_online(recipient):
                    await manager.send_to_user(recipient, dist_payload)
                else:
                    db = get_db()
                    try:
                        db.execute(
                            """INSERT INTO offline_msgs
                               (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                               VALUES (?, ?, ?, 'group_key_dist', ?, ?)""",
                            (sender, recipient, encrypted_payload, json.dumps({"group_id": group_id}),
                             datetime.now(timezone.utc).isoformat())
                        )
                        db.commit()
                    finally:
                        db.close()

            elif msg_type == "group_message":
                group_id = message.get("group_id", "")
                sender = message.get("sender", username)
                encrypted_payload = message.get("encrypted_payload", "")

                db = get_db()
                try:
                    members = db.execute(
                        "SELECT username FROM group_members WHERE group_id = ?", (group_id,)
                    ).fetchall()
                finally:
                    db.close()

                group_msg_payload = {
                    "type": "group_message",
                    "sender": sender,
                    "group_id": group_id,
                    "encrypted_payload": encrypted_payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                for m in members:
                    recipient = m["username"]
                    if recipient == sender:
                        continue
                    if manager.is_online(recipient):
                        await manager.send_to_user(recipient, group_msg_payload)
                    else:
                        db = get_db()
                        try:
                            db.execute(
                                """INSERT INTO offline_msgs
                                   (sender, recipient, encrypted_payload, msg_type, extra_data, timestamp)
                                   VALUES (?, ?, ?, 'group_message', ?, ?)""",
                                (sender, recipient, encrypted_payload, json.dumps({"group_id": group_id}),
                                 datetime.now(timezone.utc).isoformat())
                            )
                            db.commit()
                        finally:
                            db.close()

            elif msg_type == "ping":
                # Bağlantı canlılık kontrolü
                await manager.send_to_user(username, {"type": "pong"})

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
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, sender, encrypted_payload, msg_type, extra_data, timestamp FROM offline_msgs WHERE recipient = ? ORDER BY timestamp ASC",
            (username,)
        ).fetchall()

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
                    "timestamp": row["timestamp"],
                })

        # ✅ Teslim edilen tüm kuyruğu sil (Zero-Knowledge)
        if rows:
            db.execute(
                "DELETE FROM offline_msgs WHERE recipient = ?",
                (username,)
            )
            db.commit()
            print(f"[Server] Delivered and deleted {len(rows)} pending messages for '{username}'.")
    finally:
        db.close()


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                        SUNUCU BAŞLATMA                            ║
# ╚═══════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  HybridP2P Messenger - Relay Server")
    print("  REST API: http://127.0.0.1:8000/docs")
    print("  WebSocket: ws://127.0.0.1:8000/ws/{username}")
    print("=" * 60)

    uvicorn.run(
        "server:app",
        host="0.0.0.0",       # Tüm arayüzlerden erişilebilir
        port=8000,
        reload=True,           # Geliştirme modunda otomatik yeniden yükleme
        log_level="info",
    )
