import requests
import json
import hashlib
import time
from crypto_utils import (
    generate_rsa_keypair,
    public_key_to_pem_string,
    pem_string_to_public_key,
)

BASE = "http://127.0.0.1:8000"
SUFFIX = str(int(time.time()))
ALICE = f"rr_alice_{SUFFIX}"
BOB = f"rr_bob_{SUFFIX}"

print("=" * 60)
print("  Read Receipt Fallback Test")
print("=" * 60)

# 1. Generate keys
print("[1] Generating keys...")
alice_priv, alice_pub = generate_rsa_keypair(2048)
bob_priv, bob_pub = generate_rsa_keypair(2048)
print("    OK")

# Helpers
def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))

def make_auth_headers(username: str, private_key, method: str, path: str, body_text: str = "") -> dict:
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

def register_user(username: str, public_key, private_key):
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

# 2. Register users
print("[2] Registering users on server...")
register_user(ALICE, alice_pub, alice_priv)
register_user(BOB, bob_pub, bob_priv)
print("    OK")

# 3. Bob sends a read receipt via REST fallback to Alice
# Since Alice is offline, this should be stored in offline_msgs table.
print("[3] Bob sends read receipt via REST fallback...")
path = "/api/send_ws_fallback"
payload = {
    "type": "read_receipt",
    "recipient": ALICE,
    "timestamp": "2026-06-11T02:30:00.000000+00:00"
}
body = json.dumps({
    "payload": json.dumps(payload)
})
headers_bob = make_auth_headers(BOB, bob_priv, "POST", path, body)
headers_bob["Content-Type"] = "application/json"
resp = requests.post(f"{BASE}{path}", data=body.encode("utf-8"), headers=headers_bob)
print(f"    Fallback Response: {resp.status_code} - {resp.json()}")

# 4. Check if the read receipt was stored offline on the server
# We can do this by inspecting database directly or querying the database.
import sqlite3
conn = sqlite3.connect("relay_server.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM offline_msgs WHERE recipient = ? AND msg_type = 'read_receipt'", (ALICE,)).fetchall()
print("[4] Checking server database for offline read receipts...")
print(f"    Found {len(rows)} offline read receipts:")
for r in rows:
    print(f"    {dict(r)}")

assert len(rows) == 1, "HATA: Read receipt should be stored offline!"
assert json.loads(rows[0]["extra_data"])["timestamp"] == "2026-06-11T02:30:00.000000+00:00", "HATA: Timestamp mismatch!"
print("\n    SUCCESS: Read receipt stored offline via REST fallback with correct timestamp!")
conn.close()
