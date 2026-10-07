"""desktop/ws_client.py — WebSocket dinleyicisi, dispatch tablosu ve REST fallback.

client.py'den taşındı (modülerleştirme): start_websocket_listener, _run_ws_loop,
_ws_listen, is_ws_connected, _ws_send_raw, send_ws_message_with_fallback,
send_message_via_ws, send_group_message_via_ws, sync_user_groups_from_server.

Önemli bağımlılık: `_ws_listen`'in dispatch tablosu `call_offer`/`call_answer`/
`call_end` mesajlarında `self.show_call_screen()`/`self.cleanup_call()` çağırır
(CallScreenMixin) ve `state["ws_loop"]` burada kurulan asyncio event loop'udur —
VoIP ve Pure P2P kodu bu loop'u `asyncio.run_coroutine_threadsafe` ile yeniden
kullanır. Bu iki yönlü bağımlılık kasıtlıdır, MessengerApp tüm mixin'leri aynı
sınıfta birleştirdiği için sorun teşkil etmez.

`net_config.WS_URL` her zaman modül referansı üzerinden okunur (bkz. rest_client.py
başlığındaki BASE_URL notu — aynı gerekçe WS_URL için de geçerli).
"""

import asyncio
import base64
import json
import threading
from datetime import datetime

from aiortc import RTCSessionDescription, RTCIceCandidate

from crypto_utils import (
    decrypt_message,
    decrypt_symmetric,
    pem_string_to_public_key,
    public_key_to_pem_string,
    get_public_key_fingerprint,
)
from desktop import net_config


class WsClientMixin:

    def start_websocket_listener(self):
        threading.Thread(target=self._run_ws_loop, daemon=True, name="ws-listener").start()
        self.log_status("Establishing WebSocket connection...")

    def _run_ws_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._ws_listen())

    async def _ws_listen(self):
        import websockets
        self.state["ws_loop"] = asyncio.get_running_loop()
        reconnect_delay = 2
        while self.state.get("logged_in", False):
            try:
                # wss: sabitlenmiş sertifika varsa yalnızca ona güvenen TLS bağlamı
                ssl_ctx = net_config.ws_ssl_context()
                connect_kwargs = {"ssl": ssl_ctx} if ssl_ctx is not None else {}
                async with websockets.connect(f"{net_config.WS_URL}/ws/{self.state['username']}",
                                              **connect_kwargs) as ws:
                    if not self.state.get("logged_in", False):
                        break
                    # Challenge-Response Handshake:
                    # 1. Receive challenge nonce
                    challenge_raw = await ws.recv()
                    challenge_msg = json.loads(challenge_raw)
                    if challenge_msg.get("type") != "challenge":
                        raise Exception("Handshake error: No challenge received from server.")

                    challenge = challenge_msg["challenge"]

                    # 2. Sign challenge using private key
                    from crypto_utils import sign_data
                    sig = sign_data(self.state["private_key"], challenge.encode("utf-8"))
                    sig_b64 = base64.b64encode(sig).decode("ascii")

                    # 3. Send signature back to server
                    await ws.send(json.dumps({
                        "type": "auth",
                        "signature": sig_b64
                    }))

                    # 4. Receive auth result
                    auth_res_raw = await ws.recv()
                    auth_res = json.loads(auth_res_raw)
                    if auth_res.get("type") != "auth_result" or auth_res.get("status") != "success":
                        err_msg = auth_res.get("message", "Authentication failed.")
                        self.log_status(f"Kimlik doğrulama hatası: {err_msg}")
                        raise Exception(f"Kimlik doğrulama hatası: {err_msg}")

                    self.state["ws"] = ws
                    self.log_status("Connection established.")
                    self.update_connection_status(True)
                    self.run_on_ui(self.page.update)
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            t    = data.get("type", "")

                            if t == "message":
                                sender    = data.get("sender", "?")
                                enc       = data.get("encrypted_payload", "")
                                ts        = data.get("timestamp", "")
                                vo        = bool(data.get("view_once", False))
                                sig       = data.get("signature", "")

                                if not self.verify_direct_message(sender, enc, sig):
                                    self.warn_blocked_message(sender)
                                    continue

                                uid = self.verified_msg_uid(sender, enc, data.get("msg_uid", ""),
                                                            data.get("uid_sig", ""))
                                prev = None if vo else self.verified_preview(
                                    sender, enc, data.get("encrypted_preview", ""), data.get("preview_sig", ""))
                                try:
                                    pt = decrypt_message(enc, self.state["private_key"])
                                    self._on_incoming_message(sender, pt, ts, vo, enc, msg_uid=uid,
                                                              preview=prev)
                                except Exception as ex:
                                    self._on_incoming_message(sender, f"[Hata:{ex}]", ts)

                            elif t == "file_message":
                                self._on_incoming_file(
                                    sender=data.get("sender","?"),
                                    file_uuid=data.get("file_uuid",""),
                                    original_name=data.get("original_name","dosya"),
                                    file_type=data.get("file_type","document"),
                                    timestamp=data.get("timestamp",""),
                                    view_once=bool(data.get("view_once", False)),
                                )

                            elif t == "ephemeral_toggle":
                                self.run_on_ui(
                                    self._on_ephemeral_toggle_received,
                                    data.get("sender","?"),
                                    bool(data.get("ephemeral", False)),
                                    data.get("timestamp",""),
                                )

                            elif t == "group_key_dist":
                                sender = data.get("sender", "?")
                                group_id = data.get("group_id", "")
                                enc = data.get("encrypted_payload", "")
                                try:
                                    group_key_hex = decrypt_message(enc, self.state["private_key"])
                                    self.state["store"].save_group_key(group_id, group_key_hex)
                                    self.log_status(f"'{sender}' sizi gruba ekledi. Anahtar alindi.")
                                    self.sync_user_groups_from_server()
                                except Exception as ex:
                                    print(f"Grup anahtari cozme hatasi: {ex}")

                            elif t == "group_message":
                                sender = data.get("sender", "?")
                                group_id = data.get("group_id", "")
                                enc = data.get("encrypted_payload", "")
                                ts = data.get("timestamp", "")
                                sig_b64 = data.get("signature", "")
                                is_active = (self.state["recipient"] == group_id)

                                # Grup Taklit Koruması (İmza Doğrulama)
                                from crypto_utils import verify_signature
                                verified = False
                                if sig_b64:
                                    pub_key = self._resolve_sender_public_key(sender)
                                    if pub_key:
                                        try:
                                            sig_bytes = base64.b64decode(sig_b64)
                                            data_to_verify = f"{sender}:{group_id}:{enc}".encode("utf-8")
                                            verified = verify_signature(pub_key, sig_bytes, data_to_verify)
                                        except Exception as sig_ex:
                                            print(f"Grup imza dogrulama hatasi: {sig_ex}")

                                if not verified:
                                    print(f"HATA: '{sender}' kullanicisinin grup imza dogrulamasi basarisiz!")
                                    if self.state["recipient"] == group_id:
                                        self.run_on_ui(self.add_system_event, f"UYARI: '{sender}' adli kullanicinin kimligi dogrulanamadi (Taklit Tesebbusu)!")
                                    continue

                                group_key = self.state["store"].get_group_key(group_id)
                                if group_key:
                                    try:
                                        pt = decrypt_symmetric(enc, group_key)
                                        self.state["store"].save_message(
                                            partner=group_id,
                                            sender=sender,
                                            content=pt,
                                            is_mine=False,
                                            timestamp=ts,
                                            is_read=(1 if is_active else 0)
                                        )
                                        if self.state["recipient"] == group_id:
                                            def _group_ui():
                                                self.add_message_to_chat(
                                                    sender=sender,
                                                    text=pt,
                                                    is_mine=False,
                                                    time_str=ts,
                                                    save=False
                                                )
                                                self.load_inbox_chats()
                                            self.run_on_ui(_group_ui)
                                        else:
                                            chat_info = self.state["store"].get_chat_info(group_id)
                                            gname = chat_info.get("partner", group_id)
                                            self.log_status(f"Grup '{gname}'dan yeni mesaj!")
                                            self.run_on_ui(self.load_inbox_chats)
                                    except Exception as ex:
                                        print(f"Grup mesaji cozme hatasi: {ex}")

                            elif t in ("message_edit", "message_delete"):
                                sender = data.get("sender", "")
                                if self.receive_message_change(t, sender, data.get("msg_uid", ""),
                                                               data.get("encrypted_payload", ""),
                                                               data.get("signature", "")):
                                    self.run_on_ui(self.on_message_changed, sender)

                            elif t == "avatar_update":
                                if self.receive_avatar(data.get("sender", ""),
                                                       data.get("encrypted_payload", ""),
                                                       data.get("signature", "")):
                                    self.run_on_ui(self.refresh_avatars)

                            elif t == "typing":
                                self.run_on_ui(self._on_typing_received,
                                               data.get("sender", ""),
                                               bool(data.get("is_typing", False)))

                            elif t == "error":
                                # Sunucu bir mesajı reddetti (örn. boyut sınırı, bozuk format)
                                code = data.get("code", "")
                                print(f"[WS] Sunucu hatasi: {code} — {data.get('message', '')}")
                                if code == "payload_too_large":
                                    self.log_status("Mesaj çok büyük olduğu için gönderilemedi!")
                                else:
                                    self.log_status(f"Sunucu mesajı reddetti: {data.get('message', code)}")

                            elif t == "delivery_ack":
                                s = data.get("status","")
                                r = data.get("recipient","")
                                if s == "delivered_online":
                                    self.log_status(f"'{r}' adlisina iletildi.")
                                elif s == "stored_offline":
                                    self.log_status(f"'{r}' cevrimdisi. Mesaj saklandı.")

                            elif t == "read_receipt":
                                sender = data.get("sender", "?")
                                ts = data.get("timestamp", "")
                                if self.state["store"]:
                                    self.state["store"].mark_sent_messages_as_read(sender, ts)
                                if self.state["recipient"] == sender:
                                    self.run_on_ui(self.load_history_to_chat)

                            elif t == "call_offer":
                                caller = data.get("caller", "?")
                                cid = data.get("call_id", "")
                                ctype = data.get("call_type", "audio")
                                sdp = data.get("sdp_offer", "")
                                if self.state.get("active_call_id") is not None:
                                    await ws.send(json.dumps({
                                        "type": "call_reject",
                                        "recipient": caller,
                                        "call_id": cid,
                                        "reason": "busy"
                                    }))
                                else:
                                    self.state["active_call_id"] = cid
                                    self.state["call_role"] = "callee"
                                    self.state["call_partner"] = caller
                                    self.state["call_type"] = ctype
                                    self.state["call_state"] = "ringing"
                                    self.state["remote_sdp"] = sdp
                                    self.run_on_ui(self.show_call_screen)

                            elif t == "call_answer":
                                callee = data.get("callee", "?")
                                cid = data.get("call_id", "")
                                sdp = data.get("sdp_answer", "")
                                if self.state.get("active_call_id") == cid and self.state.get("call_role") == "caller":
                                    pc = self.state.get("active_pc")
                                    if pc:
                                        try:
                                            await pc.setRemoteDescription(RTCSessionDescription(
                                                sdp=sdp,
                                                type="answer"
                                            ))
                                        except Exception as ex:
                                            print("Error setting remote answer description:", ex)
                                            self.cleanup_call()

                            elif t == "call_reject":
                                cid = data.get("call_id", "")
                                reason = data.get("reason", "rejected")
                                if self.state.get("active_call_id") == cid:
                                    self.log_status(f"Arama reddedildi ({reason})")
                                    self.cleanup_call()

                            elif t == "call_end":
                                cid = data.get("call_id", "")
                                if self.state.get("active_call_id") == cid:
                                    self.log_status("Arama sonlandırıldı.")
                                    self.cleanup_call()

                            elif t == "ice_candidate":
                                cid = data.get("call_id", "")
                                if self.state.get("active_call_id") == cid:
                                    pc = self.state.get("active_pc")
                                    if pc:
                                        try:
                                            await pc.addIceCandidate(RTCIceCandidate(
                                                sdpMid=data.get("sdp_mid"),
                                                sdpMLineIndex=data.get("sdp_mline_index"),
                                                candidate=data.get("candidate")
                                            ))
                                        except Exception as ex:
                                            print("Error adding ice candidate:", ex)

                        except json.JSONDecodeError: pass
            except Exception as ex:
                import websockets
                if isinstance(ex, (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError)):
                    print(f"[WS Connection Status] Baglanti kapandi/koptu (URL: {net_config.WS_URL}/ws/{self.state.get('username')}): {ex}")
                else:
                    print(f"[WS Unexpected Error] Beklenmedik hata: {ex}")
                    import traceback
                    traceback.print_exc()
                self.state["ws"] = None
                if not self.state.get("logged_in", False):
                    break
                self.log_status(f"WS disconnected. Reconnecting in {reconnect_delay}s...")
                self.update_connection_status(False)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def is_ws_connected(self) -> bool:
        ws = self.state.get("ws")
        loop = self.state.get("ws_loop")
        ws_not_none = ws is not None
        loop_not_none = loop is not None
        loop_running = loop.is_running() if loop_not_none else False

        ws_open = False
        if ws_not_none:
            # websockets v14+ deprecated and removed the .open property.
            # We check if ws.state is websockets.protocol.State.OPEN.
            try:
                from websockets.protocol import State
                ws_open = (getattr(ws, "state", None) == State.OPEN)
            except Exception:
                ws_open = getattr(ws, "open", False)

        return ws_not_none and loop_not_none and loop_running and ws_open

    def _ws_send_raw(self, raw_json: str):
        ws = self.state.get("ws")
        loop = self.state.get("ws_loop")
        if self.is_ws_connected():
            print("[WS] Mesaj sunucuya WebSocket uzerinden gonderiliyor...")
            asyncio.run_coroutine_threadsafe(ws.send(raw_json), loop)
        else:
            print("[WS] HATA: WebSocket baglantisi aktif degil!")

    def send_ws_message_with_fallback(self, msg_dict: dict):
        raw_json = json.dumps(msg_dict)
        if self.is_ws_connected():
            self._ws_send_raw(raw_json)
        else:
            t = msg_dict.get("type", "")
            print(f"[REST] WebSocket kapali. Mesaj '{t}' REST API ile gonderiliyor...")
            try:
                def do_rest():
                    try:
                        r = self.signed_post("/api/send_ws_fallback", {"payload": raw_json}, timeout=5)
                        if r.status_code == 200:
                            print(f"[REST] Fallback ile '{t}' basariyla gonderildi.")
                            if t == "message":
                                self.log_status("Message delivered via REST.")
                        else:
                            print(f"[REST] HATA: Fallback basarisiz ({r.status_code}): {r.text}")
                            if t == "message":
                                self.log_status("Message could not be delivered!")
                    except Exception as ex:
                        print(f"[REST] HATA: Fallback baglanti hatasi: {ex}")
                        if t == "message":
                            self.log_status("Message could not be delivered!")
                threading.Thread(target=do_rest, daemon=True).start()
            except Exception as ex:
                print(f"[REST] Thread baslatma hatasi: {ex}")

    def _resolve_sender_public_key(self, sender: str):
        """Gönderenin public key'ini yerel rehberden, yoksa sunucudan alır.

        Sunucudan alındıysa rehbere kaydeder (mevcut TOFU davranışı).
        İmza doğrulaması hem grup hem birebir mesajlarda bunu kullanır.
        """
        local_contact = self.state["store"].get_contact(sender)
        if local_contact:
            return pem_string_to_public_key(local_contact["public_key"])

        pub_key = self.fetch_recipient_pub_key(sender)
        if pub_key:
            self.state["store"].save_contact(
                sender,
                public_key_to_pem_string(pub_key),
                get_public_key_fingerprint(pub_key),
            )
        return pub_key

    def _sign_direct_message(self, recipient: str, encrypted_payload: str) -> str:
        """Birebir mesaj için RSA-PSS imzası üretir (base64).

        İmzalanan veri alıcıyı da içerir; böylece ele geçirilmiş bir sunucu
        aynı mesajı başka birine yeniden yönlendiremez.
        """
        from crypto_utils import sign_data
        data = f"{self.state['username']}:{recipient}:{encrypted_payload}".encode("utf-8")
        return base64.b64encode(sign_data(self.state["private_key"], data)).decode("ascii")

    def verify_direct_message(self, sender: str, encrypted_payload: str, signature_b64: str) -> bool:
        """Gelen birebir mesajın imzasını doğrular.

        Dönüş: True = kabul edilebilir, False = reddedilmeli.

        Kurallar:
          • İmza varsa doğrulanır; geçersizse reddedilir (taklit girişimi).
          • İlk geçerli imzada kişi "imzalıyor" olarak işaretlenir.
          • İmza yoksa: kişi daha önce imzalamışsa reddedilir (downgrade
            saldırısı), hiç imzalamamışsa kabul edilir (eski/web istemcisi).
        """
        from crypto_utils import verify_signature
        store = self.state["store"]

        if signature_b64:
            pub_key = self._resolve_sender_public_key(sender)
            if not pub_key:
                print(f"[Signature] '{sender}' public key'i alinamadi, imza dogrulanamadi.")
                return False
            try:
                data = f"{sender}:{self.state['username']}:{encrypted_payload}".encode("utf-8")
                if verify_signature(pub_key, base64.b64decode(signature_b64), data):
                    if not store.contact_signs_messages(sender):
                        store.mark_contact_signs_messages(sender)
                    return True
                print(f"[Signature] '{sender}' imzasi GECERSIZ — mesaj reddedildi.")
                return False
            except Exception as ex:
                print(f"[Signature] '{sender}' imza dogrulama hatasi: {ex}")
                return False

        # İmza yok — kişi daha önce imza attıysa bu bir downgrade denemesidir
        if store.contact_signs_messages(sender):
            print(f"[Signature] '{sender}' normalde imzaliyor ama bu mesaj IMZASIZ — reddedildi.")
            return False
        return True

    # ── E2EE profil fotoğrafı ──────────────────────────────────────────

    def send_avatar_to(self, username: str) -> bool:
        """Kendi avatarımızı bir kişiye şifreli + imzalı gönderir (ağ işlemi —
        arka plan thread'inden çağırın). Gönderildiyse sürüm özeti kaydedilir."""
        from crypto_utils import encrypt_bytes, sign_data
        from desktop import avatar
        me = self.state["username"]
        jpeg = avatar.load_own(me)
        if not jpeg or username == me:
            return False
        pub = self._resolve_sender_public_key(username)
        if not pub:
            return False
        payload = encrypt_bytes(jpeg, pub)
        sig = base64.b64encode(sign_data(self.state["private_key"],
                                         avatar.signed_data(me, username, payload))).decode("ascii")
        self.send_ws_message_with_fallback({
            "type": "avatar_update", "recipient": username,
            "encrypted_payload": payload, "signature": sig,
        })
        self.state["store"].set_avatar_sent_hash(username, avatar.digest(jpeg))
        return True

    def maybe_send_avatar(self, username: str):
        """Bu kişi avatarımızın güncel sürümünü almadıysa arka planda gönderir."""
        from desktop import avatar
        jpeg = avatar.load_own(self.state["username"])
        store = self.state.get("store")
        if not jpeg or not store or store.get_avatar_sent_hash(username) == avatar.digest(jpeg):
            return
        threading.Thread(target=self.send_avatar_to, args=(username,), daemon=True).start()

    def broadcast_avatar(self):
        """Avatar değişince rehberdeki herkese gönderir (arka planda)."""
        def _run():
            sent = 0
            for c in self.state["store"].get_all_contacts():
                try:
                    sent += bool(self.send_avatar_to(c["username"]))
                except Exception as ex:
                    print(f"[Avatar] '{c['username']}' kisisine gonderilemedi: {ex}")
            self.log_status(f"Profil fotoğrafı {sent} kişiye şifreli gönderildi.")
        threading.Thread(target=_run, daemon=True).start()

    def receive_avatar(self, sender: str, encrypted_payload: str, signature_b64: str) -> bool:
        """Gelen avatarı doğrular, çözer, yeniden kodlar ve saklar. Başarılıysa True.

        İmza ZORUNLU: yeni bir tip olduğu için imzasız gönderen eski istemci yok;
        imzasız/geçersiz avatar = ele geçirilmiş sunucunun sahte fotoğraf denemesi.
        """
        from crypto_utils import decrypt_bytes, verify_signature
        from desktop import avatar
        if not signature_b64:
            print(f"[Avatar] '{sender}' avatari imzasiz — reddedildi.")
            return False
        pub = self._resolve_sender_public_key(sender)
        if not pub:
            return False
        try:
            data = avatar.signed_data(sender, self.state["username"], encrypted_payload)
            if not verify_signature(pub, base64.b64decode(signature_b64), data):
                print(f"[Avatar] '{sender}' avatar imzasi GECERSIZ — reddedildi.")
                return False
            raw = decrypt_bytes(encrypted_payload, self.state["private_key"])
            jpeg = avatar.normalize(raw, max_bytes=avatar.MAX_RECEIVED_BYTES)
        except Exception as ex:
            print(f"[Avatar] '{sender}' avatari islenemedi: {ex}")
            return False
        self.state["store"].set_contact_avatar(sender, base64.b64encode(jpeg).decode("ascii"))
        return True

    # ── Mesaj kimliği (msg_uid) ve düzenleme/silme ─────────────────────
    # İmzalanan veriler alan ayırıcılıdır ("uid:", "edit:", "delete:"), böylece
    # bir türün imzası başka bir türün yerine kullanılamaz.

    @staticmethod
    def _uid_sig_data(sender, recipient, msg_uid, payload) -> bytes:
        return f"uid:{sender}:{recipient}:{msg_uid}:{payload}".encode("utf-8")

    @staticmethod
    def _change_sig_data(kind, sender, recipient, msg_uid, payload="") -> bytes:
        if kind == "message_edit":
            return f"edit:{sender}:{recipient}:{msg_uid}:{payload}".encode("utf-8")
        return f"delete:{sender}:{recipient}:{msg_uid}".encode("utf-8")

    def _sign(self, data: bytes) -> str:
        from crypto_utils import sign_data
        return base64.b64encode(sign_data(self.state["private_key"], data)).decode("ascii")

    def _verify_from(self, sender: str, data: bytes, signature_b64: str) -> bool:
        from crypto_utils import verify_signature
        if not signature_b64:
            return False
        pub = self._resolve_sender_public_key(sender)
        if not pub:
            return False
        try:
            return verify_signature(pub, base64.b64decode(signature_b64), data)
        except Exception:
            return False

    def verified_msg_uid(self, sender: str, encrypted_payload: str, msg_uid: str, uid_sig: str):
        """Gelen mesajın msg_uid'sini yalnızca gönderenin imzasıyla bağlıysa kabul eder.

        İmzasız/geçersizse None döner: mesaj yine gösterilir ama düzenlenemez/
        silinemez. (Aksi halde ele geçirilmiş sunucu iki mesajın kimliğini
        değiştirip, bir mesajın silinmesini başka bir mesaja yönlendirebilirdi.)
        """
        if not msg_uid:
            return None
        data = self._uid_sig_data(sender, self.state["username"], msg_uid, encrypted_payload)
        if self._verify_from(sender, data, uid_sig):
            return msg_uid
        print(f"[Edit] '{sender}' mesajinin msg_uid imzasi gecersiz — kimlik yok sayildi.")
        return None

    def send_message_change(self, kind: str, recipient: str, msg_uid: str, new_text: str = ""):
        """Kendi mesajımızı karşı tarafta düzenler (message_edit) veya siler (message_delete)."""
        from crypto_utils import encrypt_message
        payload = ""
        if kind == "message_edit":
            pub = self.state.get("recipient_pub_key") if self.state.get("recipient") == recipient else None
            pub = pub or self._resolve_sender_public_key(recipient)
            if not pub:
                return False
            payload = encrypt_message(new_text, pub)
        me = self.state["username"]
        self.send_ws_message_with_fallback({
            "type": kind, "recipient": recipient, "msg_uid": msg_uid,
            "encrypted_payload": payload,
            "signature": self._sign(self._change_sig_data(kind, me, recipient, msg_uid, payload)),
        })
        return True

    def receive_message_change(self, kind: str, sender: str, msg_uid: str,
                               encrypted_payload: str, signature_b64: str) -> bool:
        """Gelen düzenleme/silmeyi uygular. İmza ZORUNLU; yalnızca gönderenin kendi
        mesajını etkiler (store sorgusu sender ile sınırlı). Değiştiyse True."""
        from crypto_utils import decrypt_message
        data = self._change_sig_data(kind, sender, self.state["username"], msg_uid, encrypted_payload)
        if not self._verify_from(sender, data, signature_b64):
            print(f"[Edit] '{sender}' {kind} imzasi gecersiz/eksik — reddedildi.")
            return False
        store = self.state["store"]
        if kind == "message_delete":
            return store.delete_message(sender, msg_uid, sender)
        try:
            new_text = decrypt_message(encrypted_payload, self.state["private_key"])
        except Exception as ex:
            print(f"[Edit] Duzenleme cozulemedi: {ex}")
            return False
        return store.edit_message(sender, msg_uid, sender, new_text)

    def encrypt_preview_for(self, recipient: str, encrypted_payload: str, preview: dict):
        """Önizlemeyi alıcıya şifreler ve o mesaja bağlı imzalar → (enc, sig)."""
        from crypto_utils import encrypt_message
        from desktop import linkpreview
        pub = self.state.get("recipient_pub_key") if self.state.get("recipient") == recipient else None
        pub = pub or self._resolve_sender_public_key(recipient)
        enc = encrypt_message(json.dumps(preview, ensure_ascii=False), pub)
        sig = self._sign(linkpreview.signed_data(self.state["username"], recipient, encrypted_payload, enc))
        return enc, sig

    def verified_preview(self, sender: str, encrypted_payload: str, encrypted_preview: str,
                         preview_sig: str):
        """Gelen önizlemeyi doğrular/çözer/temizler → JSON metni veya None.

        İmza önizlemeyi o mesaja bağlar: sunucu bir mesajın önizlemesini başka
        mesaja takamaz. Geçersizse önizleme atılır, mesajın kendisi etkilenmez.
        """
        from crypto_utils import decrypt_message
        from desktop import linkpreview
        if not encrypted_preview:
            return None
        data = linkpreview.signed_data(sender, self.state["username"], encrypted_payload, encrypted_preview)
        if not self._verify_from(sender, data, preview_sig):
            print(f"[LinkPreview] '{sender}' onizleme imzasi gecersiz — atildi.")
            return None
        try:
            clean = linkpreview.sanitize(decrypt_message(encrypted_preview, self.state["private_key"]))
        except Exception as ex:
            print(f"[LinkPreview] onizleme cozulemedi: {ex}")
            return None
        return json.dumps(clean, ensure_ascii=False) if clean else None

    def send_message_via_ws(self, recipient: str, encrypted_payload: str, view_once: bool,
                            timestamp: str = None, msg_uid: str = None, preview: dict = None):
        from datetime import timezone
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
        msg = {
            "type":              "message",
            "sender":            self.state["username"],
            "recipient":         recipient,
            "encrypted_payload": encrypted_payload,
            "view_once":         view_once,
            "signature":         self._sign_direct_message(recipient, encrypted_payload),
            "timestamp":         timestamp,
        }
        if msg_uid:
            msg["msg_uid"] = msg_uid
            msg["uid_sig"] = self._sign(self._uid_sig_data(
                self.state["username"], recipient, msg_uid, encrypted_payload))
        if preview:
            try:
                msg["encrypted_preview"], msg["preview_sig"] = self.encrypt_preview_for(
                    recipient, encrypted_payload, preview)
            except Exception as ex:
                print(f"[LinkPreview] onizleme eklenemedi, mesaj onizlemesiz gidiyor: {ex}")
        self.send_ws_message_with_fallback(msg)

    def send_group_message_via_ws(self, group_id: str, encrypted_payload: str, timestamp: str = None):
        from datetime import timezone
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
        from crypto_utils import sign_data
        data_to_sign = f"{self.state['username']}:{group_id}:{encrypted_payload}".encode("utf-8")
        sig = sign_data(self.state["private_key"], data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        msg = {
            "type":              "group_message",
            "sender":            self.state["username"],
            "group_id":          group_id,
            "encrypted_payload": encrypted_payload,
            "signature":         sig_b64,
            "timestamp":         timestamp,
        }
        self.send_ws_message_with_fallback(msg)

    def sync_user_groups_from_server(self):
        if not self.state["username"] or not self.state["store"]: return
        try:
            resp = self.signed_get(f"/api/groups/{self.state['username']}", timeout=5)
            if resp.status_code == 200:
                groups = resp.json().get("groups", [])
                for g in groups:
                    self.state["store"].get_or_create_group_chat(g["group_id"], g["group_name"])
        except Exception as ex:
            print(f"Grup senkronizasyon hatasi: {ex}")
