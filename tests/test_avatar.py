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
