"""Regresyon testleri: alt çizgili chat_id, auth 401 timeout, read_receipt <= ve file_message imzası."""

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

from conftest import recv_until, run
from crypto_utils import sign_data
from message_store import MessageStore


def test_expired_timestamp_returns_401_not_400(make_user):
    alice = make_user("auth401")
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    path = f"/api/status/{alice.name}"
    body_hash = hashlib.sha256(b"").hexdigest()
    data = "\n".join([alice.name, old_ts, "GET", path, body_hash]).encode()
    sig = base64.b64encode(sign_data(alice.priv, data)).decode()
    r = requests.get(
        f"{alice.server.base}{path}",
        headers={"X-Username": alice.name, "X-Timestamp": old_ts, "X-Signature": sig},
        timeout=5,
    )
    assert r.status_code == 401


def test_chat_settings_underscore_usernames_no_leak(make_user):
    alice_1 = make_user("alice_1")
    bob_2 = make_user("bob_2")
    bob = make_user("bob_plain")
    bob_other = make_user("bob_plain_2")

    # alice_1 <-> bob_2 ve bob_plain_2 <-> bob_2 için ephemeral aç
    r1 = alice_1.post("/api/ephemeral_toggle", {
        "sender": alice_1.name, "recipient": bob_2.name, "ephemeral": True
    })
    assert r1.status_code == 200
    assert r1.json()["chat_id"] == ":".join(sorted([alice_1.name, bob_2.name]))

    r2 = bob_other.post("/api/ephemeral_toggle", {
        "sender": bob_other.name, "recipient": bob_2.name, "ephemeral": True
    })
    assert r2.status_code == 200

    # alice_1 yalnız kendi sohbetini ve doğru partner'ı almalı
    s1 = alice_1.get(f"/api/chat_settings/{alice_1.name}").json()["settings"]
    assert len(s1) == 1
    assert s1[0]["partner"] == bob_2.name
    assert s1[0]["ephemeral"] is True

    # bob_plain (alt çizgi prefixi paylaşsa bile) başkasının ayarını görmemeli
    s_bob = bob.get(f"/api/chat_settings/{bob.name}").json()["settings"]
    assert s_bob == []


def test_read_receipt_marks_earlier_messages_as_read(isolated_keys_dir):
    store = MessageStore("rr_alice")
    t1 = "2026-11-21T01:00:01+00:00"
    t2 = "2026-11-21T01:00:02.500Z"
    t3 = "2026-11-21T01:00:05+00:00"
    store.save_message("rr_bob", "rr_alice", "birinci", True, timestamp=t1, is_read=0)
    store.save_message("rr_bob", "rr_alice", "ikinci", True, timestamp=t2, is_read=0)
    store.save_message("rr_bob", "rr_alice", "sonra", True, timestamp=t3, is_read=0)

    store.mark_sent_messages_as_read("rr_bob", t2)
    msgs = store.get_messages("rr_bob")
    assert [m["is_read"] for m in msgs] == [1, 1, 0]


def test_file_message_signature_relayed_live_offline_and_fallback(make_user):
    alice = make_user("fsa")
    bob = make_user("fsb")

    uid_live = uuid.uuid4().hex
    uid_off = uuid.uuid4().hex
    uid_fb = uuid.uuid4().hex

    async def live():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({
                "type": "file_message", "recipient": alice.name,
                "file_uuid": uid_live, "original_name": "a.png",
                "file_type": "image", "view_once": False, "signature": "sig-live",
            }))
            return await recv_until(ws_a, "file_message", match=lambda d: d["file_uuid"] == uid_live)
        finally:
            await ws_a.close(); await ws_b.close()

    got_live = run(live())
    assert got_live["signature"] == "sig-live" and got_live["sender"] == bob.name

    async def send_off():
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({
                "type": "file_message", "recipient": alice.name,
                "file_uuid": uid_off, "original_name": "b.pdf",
                "file_type": "document", "view_once": True, "signature": "sig-off",
            }))
        finally:
            await ws_b.close()

    run(send_off())
    assert bob.post("/api/send_ws_fallback", {"payload": json.dumps({
        "type": "file_message", "recipient": alice.name,
        "file_uuid": uid_fb, "original_name": "c.txt",
        "file_type": "document", "view_once": False, "signature": "sig-fb",
    })}).status_code == 200

    async def collect():
        ws_a = await alice.connect()
        seen = {}
        try:
            while len(seen) < 2:
                d = await recv_until(ws_a, "file_message", timeout=5)
                if d["file_uuid"] in (uid_off, uid_fb):
                    seen[d["file_uuid"]] = d
        finally:
            await ws_a.close()
        return seen

    seen = run(collect())
    assert seen[uid_off]["signature"] == "sig-off" and seen[uid_off]["view_once"] is True
    assert seen[uid_fb]["signature"] == "sig-fb"


def test_client_file_message_signing_and_downgrade_protection(isolated_keys_dir, keypair_pool):
    from desktop.ws_client import WsClientMixin, file_sig_data
    from crypto_utils import sign_data

    (a_priv, a_pub), (b_priv, b_pub), (m_priv, _m_pub) = keypair_pool[:3]
    directory = {"alice": a_pub, "bob": b_pub}

    class _Impl(WsClientMixin):
        pass

    alice = _Impl()
    alice.state = {"username": "alice", "private_key": a_priv, "store": MessageStore("alice")}
    alice.fetch_recipient_pub_key = lambda name: directory.get(name)

    sig = base64.b64encode(
        sign_data(b_priv, file_sig_data("bob", "alice", "uuid1", "photo.png", "image", False))
    ).decode("ascii")
    assert alice.verify_file_message("bob", "uuid1", "photo.png", "image", False, sig) is True
    # Tampered filename or view_once or attacker key must be rejected
    assert alice.verify_file_message("bob", "uuid1", "evil.exe", "image", False, sig) is False
    assert alice.verify_file_message("bob", "uuid1", "photo.png", "image", True, sig) is False
    bad_sig = base64.b64encode(
        sign_data(m_priv, file_sig_data("bob", "alice", "uuid1", "photo.png", "image", False))
    ).decode("ascii")
    assert alice.verify_file_message("bob", "uuid1", "photo.png", "image", False, bad_sig) is False
    # Once bob signed, unsigned file_message must be rejected (downgrade protection)
    assert alice.verify_file_message("bob", "uuid2", "photo2.png", "image", False, "") is False


def test_web_crypto_exports_file_sig_data():
    import shutil, subprocess
    if shutil.which("node") is None:
        pytest.skip("Node.js yok")
    from desktop.ws_client import file_sig_data
    script = (
        "import { fileSigData } from './static/js/crypto.js';"
        "console.log(Buffer.from(fileSigData('bob','alice','u1','a.png','image',true)).toString('utf8'));"
    )
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().encode("utf-8") == file_sig_data("bob", "alice", "u1", "a.png", "image", True)

