"""Mesaj düzenleme/silme.

Sunucu bölümü: msg_uid + uid_sig mesajla birlikte bütün teslim yollarından
geçmeli; message_edit / message_delete canlı, kuyruk ve REST fallback ile iletilmeli.
"""

import json
import uuid

import pytest

from conftest import recv_until, run


@pytest.fixture(scope="module")
def pair(make_user):
    return make_user("ea"), make_user("eb")


def _ids():
    t = uuid.uuid4().hex
    return f"p-{t}", f"uid-{t}", f"usig-{t}"


async def _send(user, frame):
    ws = await user.connect()
    try:
        await ws.send(json.dumps(frame))
        if frame["type"] == "message":
            return await recv_until(ws, "delivery_ack")
    finally:
        await ws.close()


def test_msg_uid_relayed_live(pair):
    alice, bob = pair
    p, uid, usig = _ids()

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name, "encrypted_payload": p,
                                        "signature": "s", "msg_uid": uid, "uid_sig": usig}))
            ack = await recv_until(ws_b, "delivery_ack")
            got = await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == p)
            return ack, got
        finally:
            await ws_a.close(); await ws_b.close()
    ack, got = run(go())
    assert ack["status"] == "delivered_online"
    assert (got["msg_uid"], got["uid_sig"]) == (uid, usig)


def test_msg_uid_survives_offline_ws_delivery(pair):
    alice, bob = pair
    p, uid, usig = _ids()
    ack = run(_send(bob, {"type": "message", "recipient": alice.name, "encrypted_payload": p,
                          "msg_uid": uid, "uid_sig": usig}))
    assert ack["status"] == "stored_offline"

    async def go():
        ws_a = await alice.connect()
        try:
            return await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == p)
        finally:
            await ws_a.close()
    got = run(go())
    assert (got["msg_uid"], got["uid_sig"]) == (uid, usig)


def test_msg_uid_survives_rest_fetch_and_fallback(pair):
    alice, bob = pair
    p1, uid1, usig1 = _ids()
    p2, uid2, usig2 = _ids()
    run(_send(bob, {"type": "message", "recipient": alice.name, "encrypted_payload": p1,
                    "msg_uid": uid1, "uid_sig": usig1}))
    raw = json.dumps({"type": "message", "recipient": alice.name, "encrypted_payload": p2,
                      "msg_uid": uid2, "uid_sig": usig2})
    assert bob.post("/api/send_ws_fallback", {"payload": raw}).status_code == 200

    msgs = {m["encrypted_payload"]: m for m in alice.get(f"/api/fetch_messages/{alice.name}").json()["messages"]}
    assert (msgs[p1]["msg_uid"], msgs[p1]["uid_sig"]) == (uid1, usig1)
    assert (msgs[p2]["msg_uid"], msgs[p2]["uid_sig"]) == (uid2, usig2)


@pytest.mark.parametrize("kind", ["message_edit", "message_delete"])
def test_change_frames_live_offline_and_fallback(pair, kind):
    alice, bob = pair
    _, uid_live, _ = _ids()
    _, uid_off, _ = _ids()
    _, uid_fb, _ = _ids()

    def frame(uid, payload=""):
        return {"type": kind, "recipient": alice.name, "msg_uid": uid,
                "encrypted_payload": payload, "signature": f"sig-{uid}", "sender": "sahte"}

    async def live():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps(frame(uid_live, "yeni")))
            return await recv_until(ws_a, kind, match=lambda d: d["msg_uid"] == uid_live)
        finally:
            await ws_a.close(); await ws_b.close()
    got = run(live())
    assert got["sender"] == bob.name and got["signature"] == f"sig-{uid_live}"
    assert got["encrypted_payload"] == "yeni"

    run(_send(bob, frame(uid_off)))
    raw = json.dumps(frame(uid_fb))
    assert bob.post("/api/send_ws_fallback", {"payload": raw}).status_code == 200

    async def later():
        ws_a = await alice.connect()
        seen = set()
        try:
            while not {uid_off, uid_fb} <= seen:
                seen.add((await recv_until(ws_a, kind, timeout=5))["msg_uid"])
        finally:
            await ws_a.close()
        return seen
    assert {uid_off, uid_fb} <= run(later())


def test_change_without_msg_uid_is_dropped(pair):
    alice, bob = pair
    import asyncio

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message_delete", "recipient": alice.name, "signature": "s"}))
            with pytest.raises(asyncio.TimeoutError):
                await recv_until(ws_a, "message_delete", timeout=1.0)
        finally:
            await ws_a.close(); await ws_b.close()
    run(go())
