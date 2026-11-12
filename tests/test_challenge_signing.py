"""K1 — istemciler yalnızca UUIDv4 giriş challenge'ı imzalar.

Kimlik anahtarı başka düz metin biçimlerini de imzalar ("gönderen:alıcı:yük"
birebir mesajlar, "hybridp2p-p2p:v2:..." sunucusuz kodlar). Kötü niyetli bir
sunucu bunlardan birini challenge diye yollarsa istemci İMZALAMAMALI.

Üç bölüm:
  1. desktop.ws_client.is_valid_challenge birim testleri
  2. Sahte websockets sunucusuyla masaüstü _ws_listen: geçersiz challenge'da
     "auth" çerçevesi gönderilmez; geçerli UUIDv4'te gönderilir.
  3. Web: static/js/ws.js içindeki isValidChallenge Node.js ile gerçekten
     çalıştırılır ve imzalamadan önce çağrıldığı doğrulanır.
"""

import asyncio
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from conftest import run
from desktop import ws_client
from desktop.ws_client import is_valid_challenge

ROOT = Path(__file__).resolve().parent.parent
WS_JS = ROOT / "static" / "js" / "ws.js"

VALID = [str(uuid.uuid4()) for _ in range(5)]
INVALID = [
    "alice:bob:AAAA",
    "hybridp2p-p2p:v2:offer:chat:alice:1:ab::00",
    "",
    None,
    123,
    str(uuid.uuid4()).upper(),
    str(uuid.uuid4()) + "\n",
    str(uuid.uuid4()) + "x",
    "x" + str(uuid.uuid4()),
    str(uuid.uuid1()),                       # sürüm 1, 4 değil
    "00000000-0000-4000-c000-000000000000",  # varyant [89ab] değil
    b"00000000-0000-4000-8000-000000000000",  # bytes, str değil
]


# ─────────────────────────── 1. Birim ───────────────────────────

@pytest.mark.parametrize("value", VALID)
def test_accepts_uuid4(value):
    assert is_valid_challenge(value)


@pytest.mark.parametrize("value", INVALID, ids=repr)
def test_rejects_non_uuid4(value):
    assert not is_valid_challenge(value)


# ─────────────────────────── 2. Masaüstü + sahte sunucu ───────────────────────────

class _FakeApp(ws_client.WsClientMixin):
    """_ws_listen'i UI olmadan çalıştıran ince sarmalayıcı."""

    def __init__(self, priv):
        self.state = {"logged_in": True, "username": "alice", "private_key": priv}
        self.statuses = []
        self.page = None

    def log_status(self, msg):
        self.statuses.append(msg)
        if "Güvenlik" in msg:
            self.state["logged_in"] = False  # döngü bir kez daha denemeden bitsin

    def update_connection_status(self, ok):
        pass

    def run_on_ui(self, fn, *args):
        pass


@pytest.fixture(scope="module")
def priv():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


async def _run_against_fake_server(app, challenge):
    import websockets
    frames, connections = [], []

    async def handler(ws):
        connections.append(1)
        await ws.send(json.dumps({"type": "challenge", "challenge": challenge}))
        try:
            async for raw in ws:
                frames.append(json.loads(raw))
                # Geçerli durumda auth geldi: testi bitir
                app.state["logged_in"] = False
                await ws.close()
        except websockets.exceptions.ConnectionClosed:
            pass

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        old_url = ws_client.net_config.WS_URL
        ws_client.net_config.WS_URL = f"ws://127.0.0.1:{port}"
        try:
            await asyncio.wait_for(app._ws_listen(), timeout=15)
        finally:
            ws_client.net_config.WS_URL = old_url
    return frames, connections


@pytest.mark.parametrize("bad", [
    "alice:bob:AAAA",
    "hybridp2p-p2p:v2:offer:chat:alice:1:ab::00",
    123,
])
def test_desktop_never_signs_invalid_challenge(priv, bad, monkeypatch):
    monkeypatch.setattr(ws_client, "INVALID_CHALLENGE_RETRY_DELAY", 0)
    app = _FakeApp(priv)
    frames, connections = run(_run_against_fake_server(app, bad))
    assert not any(f.get("type") == "auth" for f in frames)
    assert frames == []
    assert len(connections) == 1  # sıkı döngüde yeniden bağlanmadı
    assert any("geçersiz bir kimlik doğrulama" in s for s in app.statuses)


def test_desktop_signs_valid_uuid4_challenge(priv):
    from crypto_utils import verify_signature
    import base64
    app = _FakeApp(priv)
    challenge = str(uuid.uuid4())
    frames, _ = run(_run_against_fake_server(app, challenge))
    assert len(frames) == 1 and frames[0]["type"] == "auth"
    sig = base64.b64decode(frames[0]["signature"])
    assert verify_signature(priv.public_key(), sig, challenge.encode("utf-8"))


# ─────────────────────────── 3. Web (ws.js) ───────────────────────────

def _ws_js():
    return WS_JS.read_text(encoding="utf-8")


def test_web_checks_challenge_before_signing():
    src = _ws_js()
    block = src[src.index('data.type === "challenge"'):]
    check = block.index("isValidChallenge(challenge)")
    sign = block.index("signDataJS(")
    assert check < sign, "UUID kontrolü imzalamadan önce yapılmalı"
    # Kontrol başarısızsa soket kapatılıp imzalamadan çıkılmalı
    between = block[check:sign]
    assert "ws.close()" in between and "return;" in between


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js yok")
def test_web_is_valid_challenge_in_node():
    src = _ws_js()
    m = re.search(r"export function isValidChallenge\(value\) \{.*?\n\}", src, re.S)
    assert m, "isValidChallenge bulunamadı"
    fn_src = m.group(0).replace("export ", "", 1)
    cases = [v for v in VALID + INVALID if not isinstance(v, bytes)]
    script = (fn_src + "\nconst cases = " + json.dumps(cases) + ";\n"
              "console.log(JSON.stringify(cases.map(isValidChallenge)));\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got == [is_valid_challenge(v) for v in cases]
    assert got[:len(VALID)] == [True] * len(VALID)
    assert not any(got[len(VALID):])
