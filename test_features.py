"""
Entegrasyon Testi — Dosya Yukleme/Indirme + Grup Mesaj Imza Dogrulama
Sunucu calisirken bu testi calistirin.
"""
import requests
import json
import hashlib
import time
import base64
from crypto_utils import (
    generate_rsa_keypair,
    public_key_to_pem_string,
    pem_string_to_public_key,
    encrypt_message,
    decrypt_message,
    encrypt_bytes,
    decrypt_bytes,
    sign_data,
    verify_signature,
)

BASE = "http://127.0.0.1:8000"
SUFFIX = str(int(time.time()))
ALICE = f"feat_alice_{SUFFIX}"
BOB = f"feat_bob_{SUFFIX}"

print("=" * 60)
print("  Detayli Ozellik Testleri: Dosya Depolama & Grup Imzalama")
print("=" * 60)

# 1. Anahtarlari Uret
print("\n[1] RSA anahtarlari uretiliyor...")
alice_priv, alice_pub = generate_rsa_keypair(2048)
bob_priv, bob_pub = generate_rsa_keypair(2048)
print("    OK")

def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))

def make_auth_headers(username: str, private_key, method: str, path: str, body_text: str = "") -> dict:
    from datetime import datetime, timezone
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

def register_user(username: str, public_key, private_key):
    from datetime import datetime, timezone
    pem_key = public_key_to_pem_string(public_key)
    timestamp = datetime.now(timezone.utc).isoformat()
    data_to_sign = f"{username}:{timestamp}:{pem_key}".encode("utf-8")
    sig = sign_data(private_key, data_to_sign)
    sig_b64 = base64.b64encode(sig).decode("ascii")
    
    return requests.post(f"{BASE}/api/register", json={
        "username": username,
        "public_key": pem_key,
        "timestamp": timestamp,
        "signature": sig_b64
    })

# 2. Kaydet
print("[2] Kullanicilar kaydediliyor...")
register_user(ALICE, alice_pub, alice_priv)
register_user(BOB, bob_pub, bob_priv)
print("    Kullanicilar kaydedildi.")

# 3. Dosya Yukleme / Indirme Testi
print("\n[3] E2EE Dosya Yukleme ve Indirme Testi...")
original_file_data = b"Bu cok gizli ve hassas bir belgedir. E2EE testidir."
print(f"    Orijinal dosya verisi: {original_file_data.decode('utf-8')}")

# Alice dosyayı Bob'un public key'i ile şifreler
encrypted_file_b64 = encrypt_bytes(original_file_data, bob_pub)

# Sunucuya upload et
path = "/api/upload_file"
payload = {
    "sender": ALICE,
    "recipient": BOB,
    "encrypted_data": encrypted_file_b64,
    "original_name": "gizli_belge.txt",
    "file_type": "document"
}
body = canonical_json(payload)
headers = make_auth_headers(ALICE, alice_priv, "POST", path, body)
headers["Content-Type"] = "application/json"
r_upload = requests.post(f"{BASE}{path}", data=body.encode("utf-8"), headers=headers)
assert r_upload.status_code == 200, "Dosya yukleme basarisiz!"
file_uuid = r_upload.json()["uuid"]
print(f"    Dosya basariyla yuklendi. UUID: {file_uuid}")

# Bob dosyayı indirir
path_download = f"/api/download_file/{file_uuid}"
headers_bob = make_auth_headers(BOB, bob_priv, "GET", path_download)
r_download = requests.get(f"{BASE}{path_download}", headers=headers_bob)
assert r_download.status_code == 200, "Dosya indirme basarisiz!"
downloaded_data = r_download.json()

# Bob dosya şifresini çözer
decrypted_file_data = decrypt_bytes(downloaded_data["encrypted_data"], bob_priv)
print(f"    Bob tarafından cozulen dosya: {decrypted_file_data.decode('utf-8')}")
assert original_file_data == decrypted_file_data, "Dosya verileri eslesmedi!"
print("    OK: Dosya E2EE ile basariyla tasindi.")

# İkinci kez indirmeyi dene (Silinmiş olmalı)
r_download_again = requests.get(f"{BASE}{path_download}", headers=headers_bob)
assert r_download_again.status_code == 404, "HATA: Dosya sunucudan silinmemis!"
print("    OK: Dosya sunucudan kalici olarak silindi (Zero-Knowledge).")

# 4. Grup Taklit Koruması (İmza) Testi
print("\n[4] Grup Taklit Koruması (RSA İmza) Testi...")
group_id = f"group_{SUFFIX}"
# Simetrik grup anahtarı oluştur (32 byte)
import os
group_key = os.urandom(32)

# Alice gruptaki Ahmet gibi davranmaya çalışan Bob'u doğrulamak istiyor.
# Bob bir grup mesajı hazırlar ve simetrik anahtarla şifreler
group_message_text = "Selam grup üyeleri!"
from crypto_utils import encrypt_symmetric, decrypt_symmetric
encrypted_group_msg = encrypt_symmetric(group_message_text, group_key)

# Bob bu mesajı kendi private key'i ile imzalar
data_to_sign = f"{BOB}:{group_id}:{encrypted_group_msg}".encode("utf-8")
sig = sign_data(bob_priv, data_to_sign)
sig_b64 = base64.b64encode(sig).decode("ascii")

# Alice, Bob'un imzalı grup mesajını aldığında doğrular
local_contact_bob_pub = bob_pub  # Alice Bob'un public key'ine sahip
data_to_verify = f"{BOB}:{group_id}:{encrypted_group_msg}".encode("utf-8")
sig_bytes = base64.b64decode(sig_b64)
verified = verify_signature(local_contact_bob_pub, sig_bytes, data_to_verify)
assert verified is True, "Gecerli grup imzasi dogrulanamadi!"
print("    OK: Gecerli imza basariyla dogrulandi.")

# Kötü niyetli biri (Mallory), Bob'un adını kullanarak sahte imza atar
mallory_priv, mallory_pub = generate_rsa_keypair(2048)
fake_sig = sign_data(mallory_priv, data_to_verify) # Mallory kendi anahtarıyla imzalar
fake_sig_b64 = base64.b64encode(fake_sig).decode("ascii")

# Alice, bu sahte imzalı mesajı Bob'un public key'i ile doğrulamaya çalışır
fake_sig_bytes = base64.b64decode(fake_sig_b64)
verified_fake = verify_signature(local_contact_bob_pub, fake_sig_bytes, data_to_verify)
assert verified_fake is False, "HATA: Sahte imza dogrulandi!"
print("    OK: Taklit imza tesbit edildi ve engellendi.")

print("\n" + "=" * 60)
print("  Tum detayli ozellik testleri basariyla gecti!")
print("=" * 60)
