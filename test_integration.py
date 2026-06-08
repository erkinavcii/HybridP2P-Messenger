"""
Entegrasyon Testi — crypto_utils + server REST API
Sunucu çalışırken bu script'i çalıştırın.
"""
import requests
import json
import hashlib
import time
from crypto_utils import (
    generate_rsa_keypair,
    public_key_to_pem_string,
    pem_string_to_public_key,
    encrypt_message,
    decrypt_message,
)

BASE = "http://127.0.0.1:8000"
SUFFIX = str(int(time.time()))
ALICE = f"test_alice_{SUFFIX}"
BOB = f"test_bob_{SUFFIX}"

print("=" * 60)
print("  Entegrasyon Testi: crypto_utils + server")
print("=" * 60)

# 1. Alice ve Bob icin anahtar ciftleri olustur
print("\n[1] Anahtar ciftleri uretiliyor...")
alice_priv, alice_pub = generate_rsa_keypair(2048)  # Hiz icin 2048
bob_priv, bob_pub = generate_rsa_keypair(2048)
print("    OK")

# Helper functions for signing
def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))

def make_test_auth_headers(username: str, private_key, method: str, path: str, body_text: str = "") -> dict:
    from datetime import datetime, timezone
    import base64
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

def register_user_test(username: str, public_key, private_key):
    from datetime import datetime, timezone
    import base64
    from crypto_utils import sign_data, public_key_to_pem_string
    
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

# 2. Sunucuya kayit
print("[2] Kullanicilar sunucuya kaydediliyor...")
r1 = register_user_test(ALICE, alice_pub, alice_priv)
print(f"    Alice: {r1.json()['status']}")

r2 = register_user_test(BOB, bob_pub, bob_priv)
print(f"    Bob:   {r2.json()['status']}")

# 3. Alice, Bob'un public key'ini sunucudan ceker
print("[3] Alice, Bob'un public key'ini aliyor...")
path = f"/api/public_key/{BOB}"
headers_alice = make_test_auth_headers(ALICE, alice_priv, "GET", path)
r3 = requests.get(f"{BASE}{path}", headers=headers_alice)
bob_pub_from_server = pem_string_to_public_key(r3.json()["public_key"])
print("    OK")

# 4. Alice mesaji Bob'un public key'i ile sifreler
original = "Merhaba Bob! Bu bir entegrasyon testi mesajidir."
print(f"[4] Orijinal mesaj: {original}")
encrypted = encrypt_message(original, bob_pub_from_server)
print(f"    Sifreli (ilk 60 chr): {encrypted[:60]}...")

# 5. Sifreli mesaji sunucuya gonder (offline)
print("[5] Sifreli mesaj sunucuya gonderiliyor (offline kuyruk)...")
path = "/api/send_offline"
payload = {
    "sender": ALICE,
    "recipient": BOB,
    "encrypted_payload": encrypted,
}
body = canonical_json(payload)
headers_alice = make_test_auth_headers(ALICE, alice_priv, "POST", path, body)
headers_alice["Content-Type"] = "application/json"
r5 = requests.post(f"{BASE}{path}", data=body.encode("utf-8"), headers=headers_alice)
print(f"    Sonuc: {r5.json()['status']}")

# 6. Bob mesajlari ceker
print("[6] Bob cevrimdisi mesajlari cekiyor...")
path = f"/api/fetch_messages/{BOB}"
headers_bob = make_test_auth_headers(BOB, bob_priv, "GET", path)
r6 = requests.get(f"{BASE}{path}", headers=headers_bob)
data = r6.json()
print(f"    Alinan mesaj sayisi: {data['count']}")

# 7. Bob mesaji kendi private key'i ile cozer
msg = data["messages"][0]
decrypted = decrypt_message(msg["encrypted_payload"], bob_priv)
print(f"[7] Cozulen mesaj: {decrypted}")

# 8. Dogrulama
assert original == decrypted, "HATA: Mesajlar eslesmedi!"
print("\n    BASARILI: Uctan uca sifreleme + sunucu ileti calisti!")

# 9. Mesajlarin silindigini dogrula (Zero-Knowledge)
path = f"/api/fetch_messages/{BOB}"
headers_bob = make_test_auth_headers(BOB, bob_priv, "GET", path)
r9 = requests.get(f"{BASE}{path}", headers=headers_bob)
assert r9.json()["count"] == 0, "HATA: Mesajlar silinmemis!"
print("    BASARILI: Teslim edilen mesajlar veritabanindan silindi (Zero-Knowledge)")

print("\n" + "=" * 60)
print("  Tum entegrasyon testleri gecti!")
print("=" * 60)
