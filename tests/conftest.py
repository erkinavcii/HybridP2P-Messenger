"""Ortak test altyapısı.

- `server`: oturum başına bir kez, boş geçici veritabanıyla gerçek bir röle
  sunucusu (uvicorn alt süreç) başlatır. Gerçek relay_server.db'ye dokunulmaz.
- `make_user`: sunucuya kayıtlı, bellekte anahtarı olan test kullanıcısı üretir.
- `isolated_keys_dir`: MessageStore'un ~/.hybridp2p_messenger yerine geçici bir
  klasör kullanmasını sağlar.

Testler RSA-2048 kullanır (4096 üretimi yavaş). Sunucu anahtar boyutu kontrol
etmediği için davranış aynıdır.
"""

import asyncio
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crypto_utils import generate_rsa_keypair, public_key_to_pem_string, sign_data  # noqa: E402

# Testlerin sunucuyu başlattığı sınır. server/config.py varsayılanıyla aynı tutulur.
MAX_WS_MESSAGE_SIZE = 262144


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class TestServer:
    base: str
    ws: str
    max_ws: int


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    port = _free_port()
    db_path = tmp_path_factory.mktemp("srv") / "test_relay.db"
    env = dict(os.environ,
               HYBRIDP2P_DB_PATH=str(db_path),
               HYBRIDP2P_MAX_WS_MESSAGE_SIZE=str(MAX_WS_MESSAGE_SIZE),
               # Oturum çok sayıda kullanıcı kaydeder; 20/dk kayıt sınırı burada kapalı.
               # Sınırın kendisi test_rate_limit.py'de ayrı bir sunucuyla doğrulanır.
               HYBRIDP2P_RATE_LIMIT="0",
               PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", "127.0.0.1", "--port", str(port),
         # server.py ile aynı protokol sınırı
         "--ws-max-size", str(MAX_WS_MESSAGE_SIZE * 2),
         "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(f"{base}/health", timeout=0.5).status_code == 200:
                break
        except requests.RequestException:
            pass
        if proc.poll() is not None:
            raise RuntimeError("Test sunucusu başlayamadı:\n" + proc.stdout.read().decode(errors="replace"))
        time.sleep(0.15)
    else:
        proc.kill()
        raise RuntimeError("Test sunucusu 15 sn içinde hazır olmadı")

    yield TestServer(base=base, ws=f"ws://127.0.0.1:{port}", max_ws=MAX_WS_MESSAGE_SIZE)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@dataclass
class User:
    name: str
    priv: object
    pub: object
    server: TestServer

    @property
    def pem(self) -> str:
        return public_key_to_pem_string(self.pub)

    def auth_headers(self, method: str, path: str, body_text: str = "") -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        body_hash = hashlib.sha256(body_text.encode()).hexdigest()
        data = "\n".join([self.name, ts, method.upper(), path, body_hash]).encode()
        sig = base64.b64encode(sign_data(self.priv, data)).decode()
        return {"X-Username": self.name, "X-Timestamp": ts, "X-Signature": sig}

    def get(self, path: str):
        return requests.get(f"{self.server.base}{path}",
                            headers=self.auth_headers("GET", path), timeout=5)

    def post(self, path: str, payload: dict):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        headers = self.auth_headers("POST", path, body)
        headers["Content-Type"] = "application/json"
        return requests.post(f"{self.server.base}{path}", data=body.encode(),
                             headers=headers, timeout=5)

    async def connect(self):
        """Challenge-response ile kimliği doğrulanmış bir WebSocket döndürür."""
        ws = await websockets.connect(f"{self.server.ws}/ws/{self.name}",
                                      max_size=self.server.max_ws * 4)
        challenge = json.loads(await ws.recv())["challenge"]
        await ws.send(json.dumps({
            "type": "auth",
            "signature": base64.b64encode(sign_data(self.priv, challenge.encode())).decode(),
        }))
        res = json.loads(await ws.recv())
        assert res.get("status") == "success", res
        return ws


def register(server: TestServer, name: str, priv, pub) -> requests.Response:
    pem = public_key_to_pem_string(pub)
    ts = datetime.now(timezone.utc).isoformat()
    sig = base64.b64encode(sign_data(priv, f"{name}:{ts}:{pem}".encode())).decode()
    return requests.post(f"{server.base}/api/register", json={
        "username": name, "public_key": pem, "timestamp": ts, "signature": sig,
    }, timeout=5)


@pytest.fixture(scope="session")
def keypair_pool():
    """Anahtar üretimi pahalı; oturum boyunca birkaç çift üretip yeniden kullanırız."""
    return [generate_rsa_keypair(key_size=2048) for _ in range(4)]


@pytest.fixture(scope="session")
def make_user(server, keypair_pool):
    counter = {"i": 0}

    def _make(prefix: str = "u") -> User:
        i = counter["i"]
        counter["i"] += 1
        priv, pub = keypair_pool[i % len(keypair_pool)]
        name = f"{prefix}_{uuid.uuid4().hex[:8]}"
        r = register(server, name, priv, pub)
        assert r.status_code == 200, r.text
        return User(name=name, priv=priv, pub=pub, server=server)

    return _make


@pytest.fixture
def isolated_keys_dir(tmp_path, monkeypatch):
    import message_store
    monkeypatch.setattr(message_store, "KEYS_DIR", tmp_path)
    return tmp_path


async def recv_until(ws, msg_type: str, timeout: float = 5.0, match=None) -> dict:
    """Belirli tipte (ve varsa `match(data)` koşulunu sağlayan) bir çerçeve gelene
    kadar okur; arada gelen diğerlerini atlar. Önceki testlerden kalan kuyruk
    mesajları bu sayede sonucu etkilemez."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"'{msg_type}' tipi çerçeve gelmedi")
        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if data.get("type") == msg_type and (match is None or match(data)):
            return data


def run(coro):
    return asyncio.run(coro)
