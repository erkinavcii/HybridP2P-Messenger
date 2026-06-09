# 🔐 HybridP2P Messenger: Zero-Knowledge E2EE Messenger

A secure, private, and lightweight hybrid peer-to-peer messaging application designed to withstand network-level surveillance and server compromises. Built with **FastAPI** (Server) and **Flet** (Client, Python-based Flutter), it employs state-of-the-art cryptography (RSA-4096 and AES-256-GCM) to implement true **End-to-End Encryption (E2EE)** for text messages, ephemeral/view-once chats, files, and multi-user groups.

---

## 🛡️ Threat Model & Security Guarantees

The core design philosophy of HybridP2P Messenger is **Zero-Knowledge and Hostile Server Immunity**. We assume the server hosting the relay is actively compromised, monitored by third parties, or run by an untrusted entity ("Big Brother").

### 1. Server Compromise Immunity
* **No Private Keys on Server:** Private keys (`private_key.pem`) are generated locally on the client's device and **never** transmitted to or stored on the server. The server only stores public keys (`public_key.pem`) used for initial handshake routing.
* **Zero Plaintext Logs:** The server acts as a blind relay. Every message payload (text, metadata, file) is encrypted before transit. The server only sees base64-encoded blobs, random nonces, and destination routing tags.
* **Instant Deletion (Zero-Knowledge Storage):** Once a message is delivered to the recipient over WebSockets, it is permanently deleted from the server database. Offline messages are stored encrypted and deleted immediately upon retrieval.
* **Physical Isolation:** If the server is seized or compromised, the attacker cannot read any past or future messages. The cryptographic secrets are isolated entirely on the physical devices of the end-users.

### 2. Resistance to Passive Wiretapping
All communications (WebSockets and REST API) are encrypted end-to-end. Even without TLS/HTTPS at the transport layer, an attacker sniffing the network packets cannot decrypt the payloads because the cryptographic handshake happens strictly peer-to-peer at the application layer.

---

## 🔑 Cryptographic Architecture

We combine asymmetric and symmetric cryptography (hybrid encryption) to achieve optimal performance and security:

```
[ Sender Client ]                                                [ Recipient Client ]
  │                                                                ▲
  ├── 1. Generate random AES-256-GCM Session Key                    │
  ├── 2. Encrypt Plaintext using AES Session Key                    │
  ├── 3. Encrypt AES Key using Recipient's RSA-4096 Public Key      │
  │                                                                │
  ├── 4. Send Payload: [RSA-Encrypted AES Key] + [AES-Ciphertext]  │
  │                                                                │
  ▼                                                                │
[ UNTRUSTED RELAY SERVER ] ───► (Relays encrypted payload) ───────┘
                                                                   │
                                                                   └── 5. Decrypt AES Key using Private RSA-4096 Key
                                                                   └── 6. Decrypt Ciphertext using AES Key
```

* **RSA-4096 (OAEP with SHA-256 MGF1):** Used for asymmetric key exchange and wrapping symmetric session keys. 4096-bit key length provides military-grade security.
* **RSA-PSS Signatures:** Used for challenge-response connection handshakes and API request verification. We employ **PSS (Probabilistic Signature Scheme)** padding with **SHA-256** hashing to guarantee secure, randomized signatures.
* **AES-256-GCM:** Used for symmetric encryption of message payloads and files. GCM (Galois/Counter Mode) provides **Authenticated Encryption with Associated Data (AEAD)**, ensuring confidentiality, integrity, and authenticity.

---

## 🔒 Authentication & MITM Defenses

To ensure absolute client authenticity without passwords, the messenger employs cryptographic signature-based handshakes, request-bound REST headers, and out-of-band contact sharing.

### 1. WebSocket Challenge-Response Handshake
Whenever a client connects to the WebSocket endpoint (`ws://127.0.0.1:8000/ws/{username}`):
1. The server generates a random UUIDv4 challenge nonce.
2. The client signs this challenge nonce locally using their RSA Private Key.
3. The client sends the Base64 signature back to the server.
4. The server fetches the registered public key of that username and verifies the signature. If valid, the connection is accepted; otherwise, it is disconnected immediately.

### 2. Request-Bound API Signatures
All state-modifying or sensitive REST API calls require signature headers to prevent replay attacks and API abuse:
* **X-Username**: Requester's username.
* **X-Timestamp**: Current ISO UTC timestamp (prevents replay attacks with a 5-minute drift check).
* **X-Signature**: A base64 RSA-PSS signature of the request metadata:
  ```
  METHOD
  PATH
  TIMESTAMP
  BODY_SHA256
  ```
  The server hashes the incoming request body, concatenates it with the method, path, and timestamp, and verifies the signature against the database public key.

### 3. Server-Side Sender & Group Broadcast Enforcement
* **Anti-Spoofing**: The server completely overrides the `sender` field in all incoming WebSocket packet payloads to the authenticated connection username.
* **Group Broadcast Membership Checks**: The server verifies that the sending username is a registered member of the target group before BroadCasting or queueing any group message.

### 4. Out-of-Band Contact Cards & MITM Detection
Users can share their **Contact Cards** (containing username, public key PEM, and a SHA-256 fingerprint) out-of-band:
* **Contact Cards**: Clicking the **Kimliği Kopyala (Contact Card)** button copies a structured JSON contact card. Pasting this JSON directly into the recipient field imports and saves the contact locally.
* **First-Time Connection Alert (TOFU)**: If a user connects to a recipient for the first time without their contact card, the client fetches the public key from the server and prompts the user with a dialog to verify the key fingerprint.
* **MITM warning popup**: If a recipient's public key changes on the server, the client detects the difference against the local contacts store, blocks connection, and warns the user with a dialog.

---

## 👥 Group Chat Architecture & Rekeying Protocol

In E2EE, group messaging presents a major cryptographic scaling challenge. We implement a **Shared Group Symmetric Key** (a custom variant of Signal's Sender Keys approach) to balance client-side performance, network bandwidth, and strict security properties.

### Why not Naïve N-RSA (Multi-Encryption)?
In a naïve E2EE group chat implementation:
1. To send a single group message to $N$ members, the sender must encrypt the message's AES key $N-1$ times using each member's individual RSA Public Key.
2. The client must upload $N-1$ different payloads, or a single massive payload containing all $N-1$ wrapped keys.
3. This scales linearly $O(N)$ in terms of client CPU cycles and upload bandwidth. In a group of 10 members, a client wastes battery and bandwidth encrypting and sending the same message 9 times.

### Our Solution: Shared Group Symmetric Key
Instead of multi-encrypting every message, we decouple key distribution from message transmission.

1. **Group Creation & Key Setup:**
   * When a group is created, the creator client generates a cryptographically secure, random 256-bit AES key: the `Group Symmetric Key`.
   * The creator registers the group on the server (`POST /api/groups`) with its member list.
   * The creator distributes this `Group Symmetric Key` to the other $N-1$ members by encrypting it individually with their RSA Public Keys and sending a special `group_key_dist` system message.
   * Every member decrypts the key and stores it in their local SQLite database (`group_keys` table).
2. **Normal Message Flow:**
   * When a member sends a message to the group, they encrypt the text **once** with the `Group Symmetric Key` using AES-256-GCM.
   * The client sends a single ciphertext packet to the server via WebSockets.
   * The server queries the group member list and replicates (fans out) the same ciphertext to all online/offline members.
   * Each recipient retrieves the `Group Symmetric Key` from their local database and decrypts the message.
   * **Result:** Client upload bandwidth is $O(1)$ (minimal load). The server handles replication, which is computationally trivial since the server does not decrypt anything.

### Cryptographic Key Rotation (Rekeying)
To maintain security boundaries during membership changes, we enforce strict key rotation rules:

#### A. Member Join (Minimal Overhead)
* When a new member is added to the group, the client performing the addition registers the user on the server.
* The adding client fetches the current `Group Symmetric Key` from their local store, encrypts it using the new member's RSA Public Key, and sends it to them.
* *Security Trade-off:* This assumes trust in the new member regarding future messages. Since the server deletes delivered messages immediately (Zero-Knowledge), the new member cannot fetch and decrypt past historical messages even if they hold the current key, preserving practical privacy.

#### B. Member Leave / Removal (Strict Forward Secrecy)
* If a member leaves or is removed, we must ensure they can **never** decrypt future group messages.
* **Rekeying Protocol:**
  1. The client performing the removal (or the group creator) generates a brand new `Group Symmetric Key`.
  2. The client updates their own local database with this new key.
  3. The client calls the server API to remove the member from the group database.
  4. The client encrypts the **new** `Group Symmetric Key` individually for all **remaining** group members (using their respective RSA Public Keys) and distributes it via `group_key_dist`.
  5. The removed member is excluded from this distribution. All remaining members update their local store with the new key.
  6. Subsequent group messages are encrypted with the new key. The removed member has no access to the new key and cannot read any new traffic.

#### C. Group Impersonation Defense (RSA Signature Verification)
* To prevent a compromised server or any unauthorized user from broadcasting fake messages under a group member's name, every group message payload is signed with the sender's individual RSA Private Key.
* Specifically, the client signs a payload containing `{sender_username}:{group_id}:{encrypted_symmetric_payload}`.
* Receiving clients fetch the sender's public key (either from their local contacts store or dynamically from the server) and verify the RSA-PSS signature before decrypting the message. If the signature is invalid, the message is flagged as a potential impersonation attempt and rejected.

---

## 🔄 Ephemeral & View-Once Mechanics

### Ephemeral Mode (Temporary Chat)
* When either user toggles Ephemeral Mode, a sync signal is sent via WebSockets (or queued offline).
* Once active, incoming and outgoing messages are stored only in volatile memory (RAM) on the client side and are never committed to the local SQLite database (`messages.db`).
* When the chat window is closed or the app is terminated, these messages vanish forever.

### View-Once Messages
* Users can flag individual messages (text or files) as "View-Once" by toggling the eye icon (`👁️`).
* Regardless of the chat's local database settings, view-once messages are **never** stored on disk.
* Upon clicking the view-once chat bubble, a pop-up dialog opens. Once the dialog is closed (by clicking the close icon, clicking the close button, or clicking outside), the message plaintext is immediately wiped from memory and the chat bubble is permanently removed from the UI.

### E2EE File & Image Sharing
* **Client-Side Symmetric Encryption:** Files and images are encrypted locally on the sender's device using AES-256-GCM before upload.
* **Zero-Knowledge Server Storage:** The encrypted file blob is uploaded to the server's database (`file_store` table) and associated with a unique UUID. The server has no knowledge of the decryption keys or the original contents.
* **Automatic Deletion on Delivery:** Once the recipient successfully downloads the file, the server immediately and permanently deletes the encrypted blob from its disk (`Zero-Knowledge` delivery).
* **Inline Image Thumbnails:** If the file type is detected as an image, it renders as an inline thumbnail within the chat view upon decryption. Other files can be downloaded directly to the client's local `~/Downloads` directory.
* **View-Once File Protection:** If a file/image is marked as view-once, the decrypted content is displayed inside a pop-up dialog (or saved to disk for non-images). Once closed, the dialog is dismissed and the bubble is permanently removed from the chat history.

---

## 🛠️ Tech Stack & Directory Structure

* **Client:** Python 3.x, [Flet](https://flet.dev) (UI Framework based on Flutter), `requests` (REST), `websockets` (real-time).
* **Server:** Python 3.x, [FastAPI](https://fastapi.tiangolo.com) (Asynchronous Web Framework), Uvicorn (ASGI Server), SQLite (Relay and Offline Queue).
* **Cryptography:** `cryptography` (PyCA - Python Cryptography Authority).

### File Structure
```
HybridP2P-Messenger/
├── server.py              # FastAPI server, SQLite DB manager, WebSocket relay
├── client.py              # Flet UI, WebSocket connection manager, REST client
├── crypto_utils.py        # RSA/AES key pair generation, E2EE encryption/decryption
├── message_store.py       # Client-side SQLite for message logs and keys
├── requirements.txt       # Project dependencies
├── progress.md            # Feature checklist and current status
└── futures.md             # Long-term feature roadmap
```

---

## 🚀 Getting Started

### 1. Installation
Install the required packages:
```bash
pip install -r requirements.txt
```

### 2. Running the Relay Server
Start the FastAPI server:
```bash
python server.py
```
The server will run on `http://127.0.0.1:8000`. You can inspect the interactive documentation at `http://127.0.0.1:8000/docs`.

### 3. Running the Client
Open two separate terminals and launch the messenger client for different users:
```bash
# In terminal 1 (User 1)
python client.py

# In terminal 2 (User 2)
python client.py
```
The clients will automatically generate their RSA-4096 keys under `~/.hybridp2p_messenger/` on the first launch, register their public keys to the server, and open the chat dashboard.

### 4. Dynamic Server Configuration (GUI)
* When launching the client, the login screen includes a **Sunucu Adresi (Server Address)** field (defaults to `127.0.0.1:8000`).
* To connect clients across different machines on the same local network (LAN) or over the internet, simply input the server's IP address and port (e.g., `192.168.1.50:8000`) in the login view before clicking **Giriş Yap**.
