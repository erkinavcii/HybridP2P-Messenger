# 🔐 HybridP2P Messenger: Zero-Knowledge E2EE Messenger

A secure, private, and lightweight hybrid peer-to-peer messaging application designed to withstand network-level surveillance and server compromises. Built with **FastAPI** (Server) and **Flet** (Client, Python-based Flutter), it employs state-of-the-art cryptography (RSA-4096 and AES-256-GCM) to implement true **End-to-End Encryption (E2EE)** for text messages, E2EE read receipts (double green ticks), ephemeral/view-once chats, files, multi-user groups, and voice/video calling.

---

## ✨ Key Features

* **End-to-End Encrypted (E2EE) Messaging:** Full confidentiality and integrity using RSA-4096 and AES-256-GCM.
* **E2EE File & Image Sharing:** Files are encrypted locally on the sender's device before upload, stored as zero-knowledge blobs on the server, and permanently deleted immediately upon download. Supports inline image rendering.
* **E2EE Read Receipts (Double Green Ticks):** Fully encrypted status tracking notifying senders when their messages have been read by the recipient.
* **Ephemeral Chat Mode:** Sync-capable ephemeral messaging that keeps conversations strictly in volatile memory (RAM) and never writes them to disk.
* **View-Once Messages & Files:** Individual text messages or files that can be opened only once before being permanently deleted from RAM and UI.
* **E2EE VoIP Voice & Video Calling (STUN-First P2P):**
  * **Zero Server Cost (STUN-First):** Uses public STUN servers to negotiate direct peer-to-peer UDP connections for ~90% of calls.
  * **End-to-End Cryptography:** Media stream is fully E2EE using DTLS-SRTP, ensuring neither the ISP nor the server can intercept audio/video.
  * **TURN Fallback (Alternative):** Supports seamless fallback to a TURN server (e.g., coturn or Metered TURN) if STUN direct P2P fails due to symmetric NATs.
  * **Performance Optimization:** Prioritizes hardware-accelerated H.264, dynamically adapts resolution/FPS (720p to 360p) based on network quality, and leverages audio priority and WebRTC audio processing filters (AEC/ANS/AGC).
* **Zero-Knowledge Multi-User Groups:** Employs a Shared Group Symmetric Key architecture with dynamic cryptographic rekeying (forward secrecy) upon member additions/removals, keeping upload bandwidth constant at $O(1)$.
* **Secure Passwordless Authentication:** Connections and modifying API requests are authenticated using cryptographic challenges and RSA-PSS signatures.

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

### 3. Web Client Security Discrepancy & Self-Hosting
* **Web Client Security Caveat:** Unlike the native desktop client (`client.py`) which runs code locally, the web client (`static/index.html`) is served directly by the relay server. If the server is compromised, an attacker could inject malicious JavaScript to intercept private keys or plaintexts. Thus, the Web client is for convenience, while the desktop client should be used for maximum security.
* **Self-Hosting as a Private Node:** Anyone can run `server.py` on their private computer to instantly designate it as a private central messaging node and server. This keeps all metadata and encrypted database storage under your direct physical custody.

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

## ☑️ E2EE Read Receipts (Seen Status)

To prevent the server from gathering metadata about when a message was read, read receipts are fully encrypted and signed at the application layer:
* **Status States:**
  * **Single Gray Tick (Sent):** The message has been encrypted and uploaded to the server, and is either waiting in the recipient's offline queue or the recipient is connected but has not opened the specific chat.
  * **Double Green Ticks (Read/Seen):** The recipient has opened the chat window, decrypted the message, and transmitted a signed `read_receipt` back to the sender.
* **Cryptographic Delivery:**
  * When a user opens a chat, the client sends a `read_receipt` message type via WebSockets containing `{recipient, sender, timestamp}`.
  * Like regular messages, if the sender is offline when the receipt is sent, the server queues it and delivers it as soon as the sender connects, updating the local database state and UI in real-time.

---

## 📞 E2EE VoIP Voice & Video Calling (STUN-First P2P)

HybridP2P Messenger features a military-grade, secure, and low-latency voice and video calling infrastructure designed to operate with zero server media overhead.

### 1. Peer-to-Peer STUN-First Architecture
To bypass firewall constraints and establish direct device-to-device streaming without expensive relay server costs:
* **STUN Signaling:** The clients query public, free STUN servers (e.g., Google or Cloudflare) via `GET /api/ice_servers` to discover their external public IP addresses and ports.
* **Direct P2P UDP:** Using the gathered ICE candidates, the clients negotiate a direct UDP session (WebRTC). In over 85-90% of household networks, this allows media traffic (audio/video packets) to flow directly between devices.
* **Zero Server Media Burden:** The main server (`server.py`) acts purely as a routing channel for the initialization handshakes (SDP Offer/Answer and ICE candidates) and maintains no contact with the actual media stream.

### 2. TURN Alternative / Fallback
For scenarios where both devices are restricted behind strict symmetric NAT firewalls (common in secure enterprise environments or specific carrier networks) where direct P2P is blocked:
* **Optional TURN Relay:** The application is architected to seamlessly fall back to a TURN server (like a self-hosted `coturn` instance or an external provider like Metered TURN).
* **Zero-Knowledge Relay:** Even when routed through a TURN server, the relay only passes encrypted packets. The server cannot read the media stream as it does not possess the decryption keys.

### 3. Media Cryptography & Privacy (DTLS-SRTP)
* **Standard E2EE:** The media streams are encrypted using **DTLS-SRTP** (Datagram Transport Layer Security - Secure Real-time Transport Protocol), a WebRTC native security suite.
* **ISP Protection:** Internet Service Providers (ISPs) and network operators can only monitor that a connection exists and estimate data volumes. They are mathematically incapable of listening to the calls or accessing video feeds.

### 4. Performance & Bandwidth Optimization
* **H.264 Priority:** Prioritizes hardware-accelerated H.264 constrained baseline profiles on web and mobile devices to conserve battery life and prevent CPU overheating.
* **Adaptive Bitrate & Resolution:** Dynamically adjusts resolution and frame rates between **720p HD @ 30 FPS** (under optimal conditions) down to **360p @ 15 FPS** (on degraded connections) to maintain continuity.
* **Audio Priority Over Video:** Ensures audio tracks are given higher transmission priority (`priority: "high"` in SDP) during bandwidth congestion, preferring the video frame-rate to drop rather than corrupting audio clarity.
* **Acoustic Quality Filters:** Leverages WebRTC's native Acoustic Echo Cancellation (AEC), Acoustic Noise Suppression (ANS), and Automatic Gain Control (AGC) for clear, crisp audio.

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
* When launching the client, the login screen includes a **Server Address** field (defaults to `127.0.0.1:8000`).
* To connect clients across different machines on the same local network (LAN) or over the internet, simply input the server's IP address and port (e.g., `192.168.1.50:8000`) in the login view before clicking **Sign In**.

### 5. Using the E2EE Web Messenger (Web Client)
The server automatically hosts a self-contained, browser-side Zero-Knowledge E2EE Web Client directly at the root path (`/`).

* **Localhost Access:** Simply open your web browser and navigate to `http://127.0.0.1:8000/`.
* **Full Feature Parity:** The Web Client has full feature parity with the Desktop client, including E2EE messaging, file/image sharing (with inline image rendering), view-once media, chat history search, real-time online/offline statuses, and E2EE read receipts.
* **Private Key Import & Export (Device/Account Transfer):**
  * To log in as your existing desktop user on the Web Client, click **Import existing Private Key (.pem)** on the web login screen, and paste your private key PEM. The client will derive your public key using WebCrypto SubtleCrypto and authenticate securely.
  * You can retrieve your private key from the Web Client anytime by clicking the key icon (`🔑`) in the sidebar header to copy/backup it.

### 6. Hosting Your Own E2EE Server (LAN & Internet Access)
You can turn your local PC into an active web messenger server for clients on other networks or mobile/browser devices:

#### Method A: Local Network (LAN) Hosting
1. Find your hosting PC's local IP address (e.g., run `ipconfig` on Windows, or `ifconfig` / `ip a` on Linux/macOS) — let's say it is `192.168.1.100`.
2. Run the server on your PC (`python server.py`).
3. Any device (phone, laptop) on the same Wi-Fi/local network can access the Web Client by entering `http://192.168.1.100:8000/` in their web browser, or configure their desktop client to use `192.168.1.100:8000` as the **Server Address**.

#### Method B: Public Internet Tunneling (Using Ngrok)
To allow users outside your local network (anywhere in the world) to connect to your local server without complex port forwarding configurations:
1. Download and install [ngrok](https://ngrok.com/).
2. Start your local relay server:
   ```bash
   python server.py
   ```
3. Expose port `8000` to the internet:
   ```bash
   ngrok http 8000
   ```
4. Ngrok will generate a secure public HTTPS URL (e.g., `https://xxxx-xx-xx.ngrok-free.app`).
5. Share this URL with your friends! They can open it directly in their browser to load the E2EE Web Client, or type the host address (e.g., `xxxx-xx-xx.ngrok-free.app`) in the **Server Address** field of the desktop client.
   * *Note:* The Web Client automatically connects its WebSockets dynamically to the hosting origin (secure or insecure), allowing zero-configuration E2EE out-of-the-box.
