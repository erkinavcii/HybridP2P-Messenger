import os
import json
import time
import asyncio
import uuid as uuid_lib
import base64
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from crypto_utils import verify_signature, pem_string_to_public_key
from server.config import (
    HOST, PORT, CORS_ORIGINS, ALLOWED_HOSTS, START_TIME
)
from server.database import init_database, db_session
from server.websocket_manager import manager
from server.limiter import limiter

# Router'lar
from server.routes.users import router as users_router
from server.routes.messages import router as messages_router, _make_chat_id
from server.routes.groups import router as groups_router
from server.routes.voip import router as voip_router

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

# Rate Limiter konfigürasyonu
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Trusted Host Middleware
if ALLOWED_HOSTS and ALLOWED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route'ları ekle
app.include_router(users_router)
app.include_router(messages_router)
app.include_router(groups_router)
app.include_router(voip_router)


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
                await manager.send_to_user(username, extra)
            elif row_type == "ephemeral_toggle":
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
                    "signature": extra.get("signature", ""),
                    "timestamp": row["timestamp"],
                })
            elif row_type == "read_receipt":
                extra = json.loads(row["extra_data"] or "{}")
                await manager.send_to_user(username, {
                    "type": "read_receipt",
                    "sender": row["sender"],
                    "timestamp": extra.get("timestamp", ""),
                })

        if rows:
            await db.execute(
                "DELETE FROM offline_msgs WHERE recipient = ?",
                (username,)
            )
            await db.commit()
            print(f"[Server] Delivered and deleted {len(rows)} pending messages for '{username}'.")


@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    """
    WebSocket uç noktası — gerçek zamanlı mesajlaşma ve challenge-response auth.
    """
    await websocket.accept()

    try:
        # Challenge-Response auth flow:
        challenge = str(uuid_lib.uuid4())
        await websocket.send_json({"type": "challenge", "challenge": challenge})
        
        auth_data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        auth_msg = json.loads(auth_data)
        
        if auth_msg.get("type") != "auth" or "signature" not in auth_msg:
            await websocket.send_json({"type": "auth_result", "status": "failed", "message": "Geçersiz kimlik doğrulama paketi."})
            await websocket.close(code=4003)
            return
            
        signature = auth_msg["signature"]
        
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
            
        # Register connection
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

    await _deliver_pending_messages(username)

    try:
        while True:
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
                        await manager.send_to_user(recipient, toggle_payload)
                    else:
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
                    "signature": signature,
                    "timestamp": timestamp,
                }

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
                await manager.send_to_user(username, {"type": "pong"})

            elif msg_type == "call_offer":
                recipient  = message.get("recipient", "")
                call_id    = message.get("call_id", str(uuid_lib.uuid4()))
                call_type  = message.get("call_type", "audio")
                sdp_offer  = message.get("sdp_offer", "")
                ts         = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                if not manager.is_online(recipient):
                    await manager.send_to_user(username, {
                        "type": "call_reject",
                        "call_id": call_id,
                        "reason": "unavailable",
                        "timestamp": ts,
                    })

                elif manager.is_in_call(username):
                    await manager.send_to_user(username, {
                        "type": "call_reject",
                        "call_id": call_id,
                        "reason": "busy_caller",
                        "timestamp": ts,
                    })

                elif manager.is_in_call(recipient):
                    await manager.send_to_user(username, {
                        "type": "call_reject",
                        "call_id": call_id,
                        "reason": "busy",
                        "timestamp": ts,
                    })

                else:
                    manager.start_call(username, recipient, call_id)
                    await manager.send_to_user(recipient, {
                        "type": "call_offer",
                        "caller": username,
                        "call_id": call_id,
                        "call_type": call_type,
                        "sdp_offer": sdp_offer,
                        "timestamp": ts,
                    })

                    async def _call_timeout(cid: str, caller: str, callee: str):
                        await asyncio.sleep(30)
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
                recipient  = message.get("recipient", "")
                call_id    = message.get("call_id", "")
                sdp_answer = message.get("sdp_answer", "")
                ts         = message.get("timestamp") or datetime.now(timezone.utc).isoformat()

                task = manager.call_timers.pop(call_id, None)
                if task and not task.done():
                    task.cancel()

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

                manager.end_call(call_id)
                await manager.send_to_user(recipient, {
                    "type": "call_end",
                    "call_id": call_id,
                    "duration_seconds": duration_seconds,
                    "timestamp": ts,
                })
                print(f"[VoIP] call_end relayed: {username} → {recipient} (duration={duration_seconds}s)")

            elif msg_type == "ice_candidate":
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

# Statik dosyaları sun
try:
    os.makedirs("static", exist_ok=True)
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    print("[Server] Static web client mounted successfully at '/'")
except Exception as mount_ex:
    print(f"[Warning] Static client mount error: {mount_ex}")
