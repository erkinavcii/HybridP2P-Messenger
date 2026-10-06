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


# ─────────────────────────── İstemci: depo ve doğrulama ───────────────────────────

from crypto_utils import encrypt_message


class _Peer:
    def __init__(self, username, priv, directory, outbox):
        from desktop.ws_client import WsClientMixin
        from message_store import MessageStore

        class _Impl(WsClientMixin):
            pass
        self.impl = _Impl()
        self.impl.state = {"username": username, "private_key": priv,
                           "store": MessageStore(username), "recipient": None}
        self.impl.fetch_recipient_pub_key = lambda n: directory.get(n)
        self.impl.send_ws_message_with_fallback = outbox.append

    @property
    def store(self):
        return self.impl.state["store"]


@pytest.fixture
def peers(isolated_keys_dir, keypair_pool):
    (a_priv, a_pub), (b_priv, b_pub), (m_priv, _) = keypair_pool[:3]
    directory = {"alice": a_pub, "bob": b_pub}
    out = []
    return (_Peer("alice", a_priv, directory, out), _Peer("bob", b_priv, directory, out),
            _Peer("bob", m_priv, directory, out), out, a_pub)


def _bob_sends(bob, out, a_pub, text, uid):
    """bob'un istemcisinin ürettiği gerçek message çerçevesi."""
    enc = encrypt_message(text, a_pub)
    bob.impl.send_message_via_ws("alice", enc, False, timestamp="2026-10-06T10:00:00+00:00", msg_uid=uid)
    return out.pop()


def test_store_edit_and_delete_scoped_to_sender(peers):
    alice, *_ = peers
    s = alice.store
    s.save_message("bob", "bob", "merhaba", False, "2026-10-06T10:00:00+00:00", msg_uid="u1")
    s.save_message("bob", "alice", "selam", True, "2026-10-06T10:01:00+00:00", msg_uid="u2")

    assert s.edit_message("bob", "u1", "bob", "merhaba!") is True
    assert s.edit_message("bob", "u2", "bob", "ele gecirme") is False   # bob, alice'in mesajını değiştiremez
    assert s.delete_message("bob", "u2", "bob") is False
    msgs = {m["msg_uid"]: m for m in s.get_messages("bob")}
    assert msgs["u1"]["content"] == "merhaba!" and msgs["u1"]["edited"] == 1
    assert msgs["u2"]["content"] == "selam"

    assert s.delete_message("bob", "u1", "bob") is True
    m = {m["msg_uid"]: m for m in s.get_messages("bob")}["u1"]
    assert m["msg_type"] == "deleted" and m["content"] == ""
    assert s.edit_message("bob", "u1", "bob", "dirilt") is False          # silinen düzenlenemez
    assert s.search_chats_and_messages("merhaba")["messages"] == []
    s.save_message("bob", "bob", "son", False, "2026-10-06T09:00:00+00:00", msg_uid="u0")
    s.delete_message("bob", "u0", "bob")


def test_inbox_preview_for_deleted(peers):
    alice, *_ = peers
    alice.store.save_message("bob", "bob", "gizli", False, "2026-10-06T10:00:00+00:00", msg_uid="u9")
    alice.store.delete_message("bob", "u9", "bob")
    chat = next(c for c in alice.store.get_all_chats() if c["partner"] == "bob")
    assert chat["last_message"] == "🚫 Bu mesaj silindi"


def test_msg_uid_accepted_only_with_valid_uid_sig(peers):
    alice, bob, _m, out, a_pub = peers
    f = _bob_sends(bob, out, a_pub, "bir", "uid-A")
    assert alice.impl.verified_msg_uid("bob", f["encrypted_payload"], f["msg_uid"], f["uid_sig"]) == "uid-A"
    assert alice.impl.verified_msg_uid("bob", f["encrypted_payload"], f["msg_uid"], "") is None


def test_server_swapping_msg_uids_is_detected(peers):
    """Saldırı: sunucu iki mesajın kimliğini değiştirir → A'nın silinmesi B'ye yönlendirilmek istenir."""
    alice, bob, _m, out, a_pub = peers
    fa = _bob_sends(bob, out, a_pub, "A", "uid-A")
    fb = _bob_sends(bob, out, a_pub, "B", "uid-B")
    # A'nın payload'ına B'nin kimliği (ve imzası) takılır
    assert alice.impl.verified_msg_uid("bob", fa["encrypted_payload"], fb["msg_uid"], fb["uid_sig"]) is None


def test_signed_edit_and_delete_apply(peers):
    alice, bob, _m, out, a_pub = peers
    alice.store.save_message("bob", "bob", "eski", False, "2026-10-06T10:00:00+00:00", msg_uid="u1")
    alice.store.save_message("bob", "bob", "silinecek", False, "2026-10-06T10:01:00+00:00", msg_uid="u2")

    bob.impl.state["recipient"] = "alice"
    bob.impl.send_message_change("message_edit", "alice", "u1", "yeni metin")
    e = out.pop()
    assert e["encrypted_payload"] and "yeni metin" not in e["encrypted_payload"]  # şifreli
    assert alice.impl.receive_message_change("message_edit", "bob", e["msg_uid"], e["encrypted_payload"], e["signature"])

    bob.impl.send_message_change("message_delete", "alice", "u2")
    d = out.pop()
    assert alice.impl.receive_message_change("message_delete", "bob", d["msg_uid"], d["encrypted_payload"], d["signature"])

    msgs = {m["msg_uid"]: m for m in alice.store.get_messages("bob")}
    assert msgs["u1"]["content"] == "yeni metin" and msgs["u1"]["edited"] == 1
    assert msgs["u2"]["msg_type"] == "deleted"


@pytest.mark.parametrize("attack", ["unsigned", "wrong_key", "delete_sig_as_edit", "retargeted_uid"])
def test_forged_changes_rejected(peers, attack):
    alice, bob, mallory_as_bob, out, a_pub = peers
    alice.store.save_message("bob", "bob", "orijinal", False, "2026-10-06T10:00:00+00:00", msg_uid="u1")
    alice.store.save_message("bob", "bob", "diger", False, "2026-10-06T10:01:00+00:00", msg_uid="u2")

    if attack == "unsigned":
        kind, uid, payload, sig = "message_delete", "u1", "", ""
    elif attack == "wrong_key":
        mallory_as_bob.impl.send_message_change("message_delete", "alice", "u1")
        f = out.pop(); kind, uid, payload, sig = "message_delete", "u1", "", f["signature"]
    elif attack == "delete_sig_as_edit":
        bob.impl.send_message_change("message_delete", "alice", "u1")
        f = out.pop(); kind, uid = "message_edit", "u1"
        payload, sig = encrypt_message("sahte", a_pub), f["signature"]
    else:  # u2 için atılmış imzayla u1'i silmeye çalış
        bob.impl.send_message_change("message_delete", "alice", "u2")
        f = out.pop(); kind, uid, payload, sig = "message_delete", "u1", "", f["signature"]

    assert alice.impl.receive_message_change(kind, "bob", uid, payload, sig) is False
    assert {m["msg_uid"]: m["content"] for m in alice.store.get_messages("bob")} == {"u1": "orijinal", "u2": "diger"}
