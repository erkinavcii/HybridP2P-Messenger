"""E2EE profil fotoğrafı (avatar_update) — sunucu iletimi ve istemci doğrulaması.

Sunucu: yeni WS tipi üç kod yolunun hepsinde (canlı relay, _deliver_pending_messages,
send_ws_fallback) çalışmalı; kuyrukta gönderen→alıcı başına yalnızca son avatar kalmalı.
"""

import asyncio
import json
import uuid

import pytest

from conftest import recv_until, run


@pytest.fixture(scope="module")
def pair(make_user):
    return make_user("aa"), make_user("ab")


def _tag():
    return f"av-{uuid.uuid4().hex}"


def test_avatar_relayed_live(pair):
    alice, bob = pair
    tag = _tag()

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "avatar_update", "recipient": alice.name,
                                        "sender": "sahte", "encrypted_payload": tag, "signature": "s1"}))
            return await recv_until(ws_a, "avatar_update", match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_a.close(); await ws_b.close()
    got = run(go())
    assert got["sender"] == bob.name and got["signature"] == "s1"


def test_offline_queue_keeps_only_latest_avatar(pair):
    alice, bob = pair
    old, new = _tag(), _tag()

    async def go():
        ws_b = await bob.connect()
        try:
            for t in (old, new):
                await ws_b.send(json.dumps({"type": "avatar_update", "recipient": alice.name,
                                            "encrypted_payload": t, "signature": "s"}))
            await asyncio.sleep(0.4)
        finally:
            await ws_b.close()

        ws_a = await alice.connect()
        seen = []
        try:
            while True:
                try:
                    d = await recv_until(ws_a, "avatar_update", timeout=1.5)
                except asyncio.TimeoutError:
                    break
                seen.append(d["encrypted_payload"])
        finally:
            await ws_a.close()
        return seen
    seen = run(go())
    assert new in seen and old not in seen


def test_avatar_via_rest_fallback(pair):
    alice, bob = pair
    tag = _tag()
    raw = json.dumps({"type": "avatar_update", "recipient": alice.name,
                      "encrypted_payload": tag, "signature": "s"})
    assert bob.post("/api/send_ws_fallback", {"payload": raw}).status_code == 200

    async def go():
        ws_a = await alice.connect()
        try:
            return await recv_until(ws_a, "avatar_update", match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_a.close()
    assert run(go())["sender"] == bob.name


# ─────────────────────────── İstemci: işleme ve doğrulama ───────────────────────────

import base64
import io

from PIL import Image

from desktop import avatar


def _png(w=300, h=200, color=(200, 30, 30), exif=False) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    if exif:
        ex = Image.Exif(); ex[0x010F] = "GizliKameraMarkasi"   # Make etiketi
        img.save(buf, format="JPEG", exif=ex)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


def test_normalize_crops_to_square_jpeg():
    out = avatar.normalize(_png(300, 200))
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and img.size == (128, 128)
    assert len(out) < 10_000


def test_normalize_strips_metadata():
    src = _png(exif=True)
    assert b"GizliKameraMarkasi" in src
    assert b"GizliKameraMarkasi" not in avatar.normalize(src)


@pytest.mark.parametrize("raw", [b"", b"bu bir resim degil", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50])
def test_normalize_rejects_garbage(raw):
    with pytest.raises(ValueError):
        avatar.normalize(raw)


def test_normalize_rejects_decompression_bomb():
    # ~100 MP'lik tek renk PNG birkaç yüz KB'a sıkışır; açılırken belleği patlatmamalı
    bomb = _png(10_000, 10_000)
    assert len(bomb) < 2_000_000
    with pytest.raises(ValueError):
        avatar.normalize(bomb)


def test_received_size_limit():
    with pytest.raises(ValueError):
        avatar.normalize(b"x" * (avatar.MAX_RECEIVED_BYTES + 1), max_bytes=avatar.MAX_RECEIVED_BYTES)


class _Peer:
    def __init__(self, username, priv, directory, outbox):
        from desktop.ws_client import WsClientMixin
        from message_store import MessageStore

        class _Impl(WsClientMixin):
            pass
        self.impl = _Impl()
        self.impl.state = {"username": username, "private_key": priv, "store": MessageStore(username)}
        self.impl.fetch_recipient_pub_key = lambda n: directory.get(n)
        self.impl.send_ws_message_with_fallback = outbox.append
        self.impl.log_status = lambda *_: None


@pytest.fixture
def peers(isolated_keys_dir, keypair_pool):
    (a_priv, a_pub), (b_priv, b_pub), (m_priv, _) = keypair_pool[:3]
    directory = {"alice": a_pub, "bob": b_pub}
    out = []
    return (_Peer("alice", a_priv, directory, out), _Peer("bob", b_priv, directory, out),
            _Peer("bob", m_priv, directory, out), out)


def test_avatar_end_to_end_and_resend_dedupe(peers):
    alice, bob, _m, out = peers
    avatar.save_own("bob", avatar.normalize(_png()))
    assert bob.impl.send_avatar_to("alice") is True
    frame = out.pop()
    assert frame["type"] == "avatar_update"
    assert b"\xff\xd8" not in base64.b64decode(frame["encrypted_payload"])[:64]  # şifreli, ham JPEG değil

    assert alice.impl.receive_avatar("bob", frame["encrypted_payload"], frame["signature"]) is True
    stored = base64.b64decode(alice.impl.state["store"].get_contact_avatar("bob"))
    assert Image.open(io.BytesIO(stored)).size == (128, 128)

    # Aynı sürüm ikinci kez gönderilmez; fotoğraf değişince tekrar gönderilir
    import time
    calls = []
    bob.impl.send_avatar_to = calls.append
    bob.impl.maybe_send_avatar("alice")
    time.sleep(0.2)
    assert calls == []
    avatar.save_own("bob", avatar.normalize(_png(color=(10, 200, 10))))
    bob.impl.maybe_send_avatar("alice")
    time.sleep(0.2)
    assert calls == ["alice"]


def test_unsigned_avatar_rejected(peers):
    alice, bob, _m, out = peers
    avatar.save_own("bob", avatar.normalize(_png()))
    bob.impl.send_avatar_to("alice")
    frame = out.pop()
    assert alice.impl.receive_avatar("bob", frame["encrypted_payload"], "") is False
    assert alice.impl.state["store"].get_contact_avatar("bob") is None


def test_avatar_signed_by_wrong_key_rejected(peers):
    alice, _bob, mallory_as_bob, out = peers
    avatar.save_own("bob", avatar.normalize(_png(color=(0, 0, 0))))
    mallory_as_bob.impl.send_avatar_to("alice")
    frame = out.pop()
    assert alice.impl.receive_avatar("bob", frame["encrypted_payload"], frame["signature"]) is False


def test_dm_signature_cannot_be_replayed_as_avatar(peers):
    """Alan ayrımı: birebir mesaj imzası ("s:r:p") avatar imzası ("avatar:s:r:p") yerine geçemez."""
    from crypto_utils import encrypt_bytes
    alice, bob, _m, _out = peers
    payload = encrypt_bytes(avatar.normalize(_png()), alice.impl.fetch_recipient_pub_key("alice"))
    dm_sig = bob.impl._sign_direct_message("alice", payload)
    assert alice.impl.receive_avatar("bob", payload, dm_sig) is False
