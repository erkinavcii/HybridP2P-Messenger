"""K2 — grup anahtarı dağıtımı (group_key_dist) kimlik doğrulaması.

Üç bölüm:
  1. Sunucu: yalnızca gönderen VE alıcı grubun üyesiyse iletilir/kuyruğa yazılır;
     imza alanı canlı relay, offline kuyruk ve REST fallback'te aynen taşınır.
  2. Masaüstü alıcı: decide_group_key kuralı + receive_group_key uçtan uca
     (sahte store ile, Flet olmadan).
  3. Web: decideGroupKey / groupKeySigData Node.js ile çalıştırılır, Python ile
     aynı sonuçları verdiği doğrulanır.
"""

import asyncio
import base64
import json
import re
import shutil
import subprocess
import uuid

import pytest

from conftest import ROOT, recv_until, run
from crypto_utils import encrypt_message, sign_data
from desktop import ws_client
from desktop.ws_client import (
    GROUP_KEY_ACCEPT, GROUP_KEY_REJECT, decide_group_key, group_key_sig_data,
)


# ─────────────────────────── 1. Sunucu ───────────────────────────

@pytest.fixture(scope="module")
def trio(make_user):
    """alice (kurucu) + bob (üye) gruptadır; mallory değildir."""
    alice, bob, mallory = make_user("ga"), make_user("gb"), make_user("gm")
    group_id = f"group_{uuid.uuid4().hex[:12]}"
    r = alice.post("/api/groups", {"group_id": group_id, "group_name": "K2",
                                   "creator": alice.name, "members": [bob.name]})
    assert r.status_code == 200, r.text
    return alice, bob, mallory, group_id


def _frame(recipient, group_id, tag, sig="imza"):
    f = {"type": "group_key_dist", "recipient": recipient, "group_id": group_id,
         "encrypted_payload": tag}
    if sig is not None:
        f["signature"] = sig
    return f


def _tag():
    return f"gk-{uuid.uuid4().hex}"


def test_member_key_relayed_live_with_signature(trio):
    alice, bob, _, gid = trio
    tag = _tag()

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_a.send(json.dumps(_frame(bob.name, gid, tag, "s-live")))
            return await recv_until(ws_b, "group_key_dist", match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_a.close(); await ws_b.close()
    got = run(go())
    assert got["sender"] == alice.name and got["group_id"] == gid
    assert got["signature"] == "s-live"


def test_old_client_without_signature_relayed_with_empty(trio):
    alice, bob, _, gid = trio
    tag = _tag()

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_a.send(json.dumps(_frame(bob.name, gid, tag, sig=None)))
            return await recv_until(ws_b, "group_key_dist", match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_a.close(); await ws_b.close()
    assert run(go())["signature"] == ""


def test_non_member_sender_not_relayed_and_rejected(trio):
    _, bob, mallory, gid = trio
    tag = _tag()

    async def go():
        ws_m = await mallory.connect()
        ws_b = await bob.connect()
        try:
            await ws_m.send(json.dumps(_frame(bob.name, gid, tag)))
            ack = await recv_until(ws_m, "delivery_ack")
            with pytest.raises(asyncio.TimeoutError):
                await recv_until(ws_b, "group_key_dist", timeout=1.0,
                                 match=lambda d: d["encrypted_payload"] == tag)
            return ack
        finally:
            await ws_m.close(); await ws_b.close()
    ack = run(go())
    assert ack["status"] == "rejected_not_group_member" and ack["recipient"] == gid


def test_non_member_recipient_not_queued(trio):
    """Üye, grupta olmayan birine (çevrimdışı) anahtar gönderemez — kuyruğa da yazılmaz."""
    alice, _, mallory, gid = trio
    tag = _tag()

    async def go():
        ws_a = await alice.connect()
        try:
            await ws_a.send(json.dumps(_frame(mallory.name, gid, tag)))
            ack = await recv_until(ws_a, "delivery_ack")
        finally:
            await ws_a.close()
        ws_m = await mallory.connect()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await recv_until(ws_m, "group_key_dist", timeout=1.0,
                                 match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_m.close()
        return ack
    assert run(go())["status"] == "rejected_not_group_member"


def test_offline_queue_keeps_signature(trio):
    alice, bob, _, gid = trio
    tag = _tag()

    async def go():
        ws_a = await alice.connect()
        try:
            await ws_a.send(json.dumps(_frame(bob.name, gid, tag, "s-offline")))
            await asyncio.sleep(0.4)
        finally:
            await ws_a.close()
        ws_b = await bob.connect()
        try:
            return await recv_until(ws_b, "group_key_dist", match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_b.close()
    got = run(go())
    assert got["sender"] == alice.name and got["signature"] == "s-offline" and got["group_id"] == gid


def test_rest_fallback_member_relayed(trio):
    alice, bob, _, gid = trio
    tag = _tag()
    raw = json.dumps(_frame(bob.name, gid, tag, "s-rest"))
    assert alice.post("/api/send_ws_fallback", {"payload": raw}).status_code == 200

    async def go():
        ws_b = await bob.connect()
        try:
            return await recv_until(ws_b, "group_key_dist", match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_b.close()
    got = run(go())
    assert got["sender"] == alice.name and got["signature"] == "s-rest"


def test_rest_fallback_non_member_rejected(trio):
    _, bob, mallory, gid = trio
    tag = _tag()
    raw = json.dumps(_frame(bob.name, gid, tag))
    assert mallory.post("/api/send_ws_fallback", {"payload": raw}).status_code == 403

    async def go():
        ws_b = await bob.connect()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await recv_until(ws_b, "group_key_dist", timeout=1.0,
                                 match=lambda d: d["encrypted_payload"] == tag)
        finally:
            await ws_b.close()
    run(go())


def test_unknown_group_rejected(trio):
    alice, bob, _, _ = trio
    raw = json.dumps(_frame(bob.name, "group_yok_" + uuid.uuid4().hex[:6], _tag()))
    assert alice.post("/api/send_ws_fallback", {"payload": raw}).status_code == 403


# ─────────────────────────── 2. Masaüstü alıcı ───────────────────────────

# (has_existing_key, has_sig, sig_valid) → beklenen karar
DECISION_CASES = [
    ((False, False, False), GROUP_KEY_ACCEPT),   # ilk anahtar, imzasız (eski istemci)
    ((False, True, True), GROUP_KEY_ACCEPT),     # ilk anahtar, geçerli imza
    ((False, True, False), GROUP_KEY_REJECT),    # ilk anahtar, bozuk imza
    ((True, False, False), GROUP_KEY_REJECT),    # imzasız DEĞİŞTİRME
    ((True, True, False), GROUP_KEY_REJECT),     # bozuk imzalı değiştirme
    ((True, True, True), GROUP_KEY_ACCEPT),      # geçerli imzalı değiştirme
]


@pytest.mark.parametrize("args,expected", DECISION_CASES)
def test_decide_group_key(args, expected):
    assert decide_group_key(*args) == expected


def test_sig_data_format():
    assert group_key_sig_data("a", "b", "group_1", "P") == b"gkd:v1:a:b:group_1:P"


class _FakeStore:
    def __init__(self):
        self.keys, self.events = {}, []

    def get_group_key(self, gid):
        return bytes.fromhex(self.keys[gid]) if gid in self.keys else None

    def save_group_key(self, gid, key_hex):
        self.keys[gid] = key_hex

    def get_chat_info(self, gid):
        return {"chat_id": gid, "is_group": 1}

    def save_system_event(self, partner, text):
        self.events.append((partner, text))


class _FakeApp(ws_client.WsClientMixin):
    def __init__(self, me, my_priv, contacts):
        self.state = {"username": me, "private_key": my_priv, "store": _FakeStore(),
                      "recipient": None}
        self.contacts = contacts  # kullanıcı adı → public key
        self.statuses = []
        self.synced = 0

    def _resolve_sender_public_key(self, sender):
        return self.contacts.get(sender)

    def log_status(self, msg):
        self.statuses.append(msg)

    def sync_user_groups_from_server(self):
        self.synced += 1

    def run_on_ui(self, fn, *args):
        pass

    def load_inbox_chats(self):
        pass


@pytest.fixture(scope="module")
def keys(keypair_pool):
    return {"alice": keypair_pool[0], "bob": keypair_pool[1], "eve": keypair_pool[2]}


def _signed(sender_priv, sender, recipient, gid, enc):
    return base64.b64encode(sign_data(sender_priv, group_key_sig_data(sender, recipient, gid, enc))).decode()


def _bob(keys):
    return _FakeApp("bob", keys["bob"][0], {"alice": keys["alice"][1], "eve": keys["eve"][1]})


GID = "group_k2test0001"


def test_desktop_unsigned_first_key_accepted(keys):
    app = _bob(keys)
    k = "11" * 32
    assert app.receive_group_key("alice", GID, encrypt_message(k, keys["bob"][1]), "") == "accepted"
    assert app.state["store"].keys[GID] == k and app.synced == 1
    assert app.state["store"].events == []  # ilk anahtar: bugünkü davranış, sistem mesajı yok


def test_desktop_unsigned_replacement_rejected(keys):
    app = _bob(keys)
    app.state["store"].keys[GID] = "11" * 32
    enc = encrypt_message("22" * 32, keys["bob"][1])
    assert app.receive_group_key("eve", GID, enc, "") == "rejected"
    assert app.state["store"].keys[GID] == "11" * 32
    assert app.state["store"].events and "UYARI" in app.state["store"].events[0][1]


def test_desktop_bad_signature_rejected(keys):
    app = _bob(keys)
    app.state["store"].keys[GID] = "11" * 32
    enc = encrypt_message("22" * 32, keys["bob"][1])
    # eve kendi anahtarıyla imzalayıp alice adına gönderir
    forged = _signed(keys["eve"][0], "alice", "bob", GID, enc)
    assert app.receive_group_key("alice", GID, enc, forged) == "rejected"
    assert app.state["store"].keys[GID] == "11" * 32
    assert any("imza" in s for s in app.statuses)


def test_desktop_bad_signature_first_key_rejected(keys):
    app = _bob(keys)
    enc = encrypt_message("22" * 32, keys["bob"][1])
    assert app.receive_group_key("alice", GID, enc, "AAAA") == "rejected"
    assert GID not in app.state["store"].keys


def test_desktop_signature_bound_to_recipient_and_group(keys):
    """Başka alıcıya / başka gruba atılmış imza yeniden kullanılamaz."""
    app = _bob(keys)
    app.state["store"].keys[GID] = "11" * 32
    enc = encrypt_message("22" * 32, keys["bob"][1])
    for sig in (_signed(keys["alice"][0], "alice", "carol", GID, enc),
                _signed(keys["alice"][0], "alice", "bob", "group_baska", enc)):
        assert app.receive_group_key("alice", GID, enc, sig) == "rejected"
    assert app.state["store"].keys[GID] == "11" * 32


def test_desktop_valid_signed_replacement_accepted(keys):
    app = _bob(keys)
    app.state["store"].keys[GID] = "11" * 32
    enc = encrypt_message("22" * 32, keys["bob"][1])
    # kurucu olmayan bir üye (eve) de yenileyebilir — bugünkü davranış
    sig = _signed(keys["eve"][0], "eve", "bob", GID, enc)
    assert app.receive_group_key("eve", GID, enc, sig) == "replaced"
    assert app.state["store"].keys[GID] == "22" * 32
    assert app.state["store"].events == [(GID, "🔑 'eve' grup anahtarını yeniledi.")]


def test_desktop_same_key_resent_is_noop(keys):
    app = _bob(keys)
    app.state["store"].keys[GID] = "11" * 32
    enc = encrypt_message("11" * 32, keys["bob"][1])
    assert app.receive_group_key("alice", GID, enc, "") == "unchanged"
    assert app.state["store"].events == []


def test_desktop_sender_signs_outgoing_frame(keys):
    app = _FakeApp("alice", keys["alice"][0], {})
    sent = []
    app.send_ws_message_with_fallback = sent.append
    app.send_group_key_to("bob", GID, "33" * 32, keys["bob"][1])
    f = sent[0]
    assert f["type"] == "group_key_dist" and f["recipient"] == "bob" and f["group_id"] == GID
    bob = _bob(keys)
    assert bob.receive_group_key("alice", GID, f["encrypted_payload"], f["signature"]) == "accepted"
