"""desktop/rest_client.py — İmzalı REST istekleri ve anahtar/oturum yardımcıları.

client.py'den taşındı (modülerleştirme): initialize_keys, _canonical_json,
make_auth_headers, signed_get/post/delete, register_with_server,
fetch_recipient_pub_key, sync_chat_settings, fetch_offline_messages.

`net_config.BASE_URL` her zaman modül referansı (`net_config.BASE_URL`) üzerinden
okunur, `from ... import BASE_URL` KULLANILMAZ — çünkü update_server_urls()
BASE_URL'i net_config modülü içinde `global BASE_URL` ile mutasyona uğratır;
bir isim importu, o değişikliği görmeyen donmuş bir kopya alırdı.
"""

import json

import requests

from crypto_utils import (
    generate_rsa_keypair,
    save_keys_to_disk,
    load_keys_from_disk,
    public_key_to_pem_string,
    pem_string_to_public_key,
    decrypt_message,
)
from desktop import net_config


class RestClientMixin:

    def initialize_keys(self, username: str):
        priv, pub = load_keys_from_disk(username)
        if priv and pub:
            self.log_status("Existing keys loaded.")
            return priv, pub
        self.log_status("Generating RSA-4096 keys...")
        self.page.update()
        priv, pub = generate_rsa_keypair()
        save_keys_to_disk(username, priv, pub)
        self.log_status("Keys generated.")
        return priv, pub

    def _canonical_json(self, payload: dict) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def make_auth_headers(self, username: str, private_key, method: str, path: str, body_text: str = "") -> dict:
        if not username or not private_key:
            return {}
        from datetime import datetime, timezone
        import base64
        import hashlib
        from crypto_utils import sign_data

        timestamp = datetime.now(timezone.utc).isoformat()
        body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        data_to_sign = "\n".join([
            username,
            timestamp,
            method.upper(),
            path,
            body_hash,
        ]).encode("utf-8")
        sig = sign_data(private_key, data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        return {
            "X-Username": username,
            "X-Timestamp": timestamp,
            "X-Signature": sig_b64
        }

    def signed_get(self, path: str, timeout: int = 5):
        headers = self.make_auth_headers(self.state["username"], self.state["private_key"], "GET", path)
        return requests.get(f"{net_config.BASE_URL}{path}", headers=headers, timeout=timeout)

    def signed_delete(self, path: str, timeout: int = 5):
        headers = self.make_auth_headers(self.state["username"], self.state["private_key"], "DELETE", path)
        return requests.delete(f"{net_config.BASE_URL}{path}", headers=headers, timeout=timeout)

    def signed_post(self, path: str, payload: dict, timeout: int = 5):
        body_text = self._canonical_json(payload)
        headers = self.make_auth_headers(self.state["username"], self.state["private_key"], "POST", path, body_text)
        headers["Content-Type"] = "application/json"
        return requests.post(f"{net_config.BASE_URL}{path}", data=body_text.encode("utf-8"), headers=headers, timeout=timeout)

    def register_with_server(self, username: str, public_key, private_key) -> bool:
        from datetime import datetime, timezone
        import base64
        from crypto_utils import sign_data

        pem_key = public_key_to_pem_string(public_key)
        timestamp = datetime.now(timezone.utc).isoformat()

        data_to_sign = f"{username}:{timestamp}:{pem_key}".encode("utf-8")
        sig = sign_data(private_key, data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        try:
            resp = requests.post(f"{net_config.BASE_URL}/api/register", json={
                "username": username,
                "public_key": pem_key,
                "timestamp": timestamp,
                "signature": sig_b64
            }, timeout=5)
        except requests.exceptions.Timeout:
            raise Exception("Server request timed out after 5 seconds.")
        except requests.exceptions.ConnectionError as conn_err:
            raise Exception(f"Server did not respond (offline or connection refused): {conn_err}")
        except Exception as ex:
            raise Exception(f"Network error: {ex}")

        if resp.status_code == 200:
            return True

        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text

        raise Exception(f"Server rejected registration ({resp.status_code}): {detail}")

    def fetch_recipient_pub_key(self, recipient: str):
        try:
            resp = self.signed_get(f"/api/public_key/{recipient}", timeout=5)
            if resp.status_code == 200:
                return pem_string_to_public_key(resp.json()["public_key"])
        except: pass
        return None

    def sync_chat_settings(self):
        if not self.state["username"]: return
        try:
            resp = self.signed_get(f"/api/chat_settings/{self.state['username']}", timeout=5)
            if resp.status_code == 200:
                for s in resp.json().get("settings", []):
                    parts   = s["chat_id"].split("_", 1)
                    partner = parts[0] if len(parts) > 1 and parts[1] == self.state["username"] else parts[-1]
                    if self.state["store"]:
                        self.state["store"].set_ephemeral(partner, s["ephemeral"], s.get("changed_by"))
                    if partner == self.state["recipient"]:
                        self.state["ephemeral"] = s["ephemeral"]
                        self._update_ephemeral_ui()
        except: pass

    def fetch_offline_messages(self):
        print(f"[REST] Sunucudan cevrimdisi mesajlar talep ediliyor ({self.state['username']})...")
        try:
            resp = self.signed_get(f"/api/fetch_messages/{self.state['username']}", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data["count"]:
                    self.log_status(f"{data['count']} cevrimdisi mesaj alindi.")
                    print(f"[REST] {data['count']} yeni cevrimdisi mesaj alindi.")
                else:
                    print("[REST] Cevrimdisi mesaj yok.")
                for msg in data["messages"]:
                    try:
                        pt = decrypt_message(msg["encrypted_payload"], self.state["private_key"])
                        print(f"[REST] '{msg['sender']}' kullanicisindan gelen cevrimdisi mesaj basariyla cozuldu.")
                        self._on_incoming_message(msg["sender"], pt, msg.get("timestamp", ""),
                                             bool(msg.get("view_once", False)),
                                             msg["encrypted_payload"])
                    except Exception as ex:
                        print(f"[REST] Mesaj cozme hatasi: {ex}")
                        self._on_incoming_message(msg["sender"], f"[Hata: {ex}]",
                                             msg.get("timestamp", ""))
        except Exception as e:
            self.log_status("Failed to fetch offline messages.")
            print(f"[REST] Cevrimdisi mesajlar sunucudan cekilemedi: {e}")
