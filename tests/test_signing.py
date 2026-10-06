"""1:1 mesaj imzalama.

İki bölüm:
  1. İstemci doğrulama mantığı (desktop WsClientMixin.verify_direct_message) —
     kabul/ret kuralları ve ilk-imzada-güven (downgrade koruması).
  2. Sunucu aktarımı — `signature` alanı sunucunun BÜTÜN teslim yollarından
     kaybolmadan geçmeli. Bu, "yeni alan / tip eklerken üç sunucu kod yoluna
     dokunmak gerekir" tuzağına karşı regresyon testidir:
       canlı relay, _deliver_pending_messages, /api/fetch_messages, send_ws_fallback.
"""

import json
import uuid

import pytest

from conftest import recv_until, run
from crypto_utils import encrypt_message, public_key_to_pem_string, get_public_key_fingerprint


# ─────────────────────────── 1. İstemci doğrulama mantığı ───────────────────────────

class _Client:
    """Masaüstü istemcisinin imza mantığını UI olmadan çalıştıran ince sarmalayıcı."""

    def __init__(self, username, priv, directory):
        from desktop.ws_client import WsClientMixin
        from message_store import MessageStore

        class _Impl(WsClientMixin):
            pass

        self.impl = _Impl()
        self.impl.state = {"username": username, "private_key": priv,
                           "store": MessageStore(username)}
        # Sunucudan public key çekmenin yerine geçen sahte rehber
        self.impl.fetch_recipient_pub_key = lambda name: directory.get(name)

    @property
    def store(self):
        return self.impl.state["store"]

    def sign_for(self, recipient, payload):
        return self.impl._sign_direct_message(recipient, payload)

    def verify(self, sender, payload, sig):
        return self.impl.verify_direct_message(sender, payload, sig)


@pytest.fixture
def parties(isolated_keys_dir, keypair_pool):
    (a_priv, a_pub), (b_priv, b_pub), (m_priv, _m_pub) = keypair_pool[:3]
    directory = {"alice": a_pub, "bob": b_pub}   # mallory'nin anahtarı kimsenin rehberinde yok
    alice = _Client("alice", a_priv, directory)
    bob = _Client("bob", b_priv, directory)
    mallory_as_bob = _Client("bob", m_priv, directory)  # bob adına imza atmaya çalışan saldırgan
    return alice, bob, mallory_as_bob, b_pub


def test_unsigned_from_never_signed_contact_is_accepted(parties):
    """Eski masaüstü sürümleri ve web istemcisi henüz imzalamıyor — kabul edilmeli."""
    alice, *_ = parties
    assert alice.verify("bob", "payload", "") is True
    assert alice.store.contact_signs_messages("bob") is False


def test_valid_signature_is_accepted_and_contact_flagged(parties):
    alice, bob, *_ = parties
    assert alice.verify("bob", "p1", bob.sign_for("alice", "p1")) is True
    assert alice.store.contact_signs_messages("bob") is True


def test_tampered_payload_is_rejected(parties):
    alice, bob, *_ = parties
    assert alice.verify("bob", "DEĞİŞTİRİLMİŞ", bob.sign_for("alice", "orijinal")) is False


def test_downgrade_after_first_signature_is_rejected(parties):
    """Bob bir kez imzaladıktan sonra imzasız 'bob' mesajı = imzası silinmiş mesaj."""
    alice, bob, *_ = parties
    assert alice.verify("bob", "p1", bob.sign_for("alice", "p1")) is True
    assert alice.verify("bob", "p2", "") is False


def test_signature_for_another_recipient_is_rejected(parties):
    """Alıcı imzaya dahil: carol'a imzalanan mesaj alice'e yönlendirilemez."""
    alice, bob, *_ = parties
    assert alice.verify("bob", "p", bob.sign_for("carol", "p")) is False


def test_signature_with_wrong_key_is_rejected(parties):
    alice, _bob, mallory_as_bob, _ = parties
    assert alice.verify("bob", "p", mallory_as_bob.sign_for("alice", "p")) is False


def test_unknown_sender_key_is_rejected(parties):
    alice, bob, *_ = parties
    # 'ghost' rehberde de sunucuda da yok → anahtar çözülemez → reddedilmeli
    assert alice.verify("ghost", "p", bob.sign_for("alice", "p")) is False


def test_first_contact_pins_sender_key(parties):
    """Sunucudan alınan anahtar rehbere kaydedilir (TOFU)."""
    alice, bob, _m, bob_pub = parties
    assert alice.store.get_contact("bob") is None
    alice.verify("bob", "p", bob.sign_for("alice", "p"))
    contact = alice.store.get_contact("bob")
    assert contact["public_key"] == public_key_to_pem_string(bob_pub)
    assert contact["fingerprint"] == get_public_key_fingerprint(bob_pub)


def test_garbage_signature_does_not_crash(parties):
    alice, *_ = parties
    assert alice.verify("bob", "p", "bu-base64-degil!!!") is False


# ─────────────────────────── 2. Sunucu aktarımı ───────────────────────────

@pytest.fixture(scope="module")
def pair(make_user):
    return make_user("sa"), make_user("sb")


def _unique():
    tag = uuid.uuid4().hex
    return f"payload-{tag}", f"sig-{tag}"


def test_signature_relayed_live(pair):
    alice, bob = pair
    payload, sig = _unique()

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "encrypted_payload": payload, "signature": sig}))
            got = await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == payload)
            assert got["signature"] == sig
            assert got["sender"] == bob.name
        finally:
            await ws_a.close(); await ws_b.close()
    run(go())


def test_sender_field_cannot_be_spoofed(pair):
    alice, bob = pair
    payload, sig = _unique()

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "sender": "admin", "encrypted_payload": payload, "signature": sig}))
            got = await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == payload)
            assert got["sender"] == bob.name
        finally:
            await ws_a.close(); await ws_b.close()
    run(go())


def test_signature_survives_offline_queue_ws_delivery(pair):
    """Alıcı çevrimdışı → kuyruk → bağlanınca _deliver_pending_messages ile teslim."""
    alice, bob = pair
    payload, sig = _unique()

    async def go():
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "encrypted_payload": payload, "signature": sig}))
            assert (await recv_until(ws_b, "delivery_ack"))["status"] == "stored_offline"
        finally:
            await ws_b.close()

        ws_a = await alice.connect()
        try:
            got = await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == payload)
            assert got["signature"] == sig
        finally:
            await ws_a.close()
    run(go())


def test_signature_survives_offline_queue_rest_fetch(pair):
    alice, bob = pair
    payload, sig = _unique()

    async def send():
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "encrypted_payload": payload, "signature": sig}))
            await recv_until(ws_b, "delivery_ack")
        finally:
            await ws_b.close()
    run(send())

    r = alice.get(f"/api/fetch_messages/{alice.name}")
    assert r.status_code == 200
    mine = [m for m in r.json()["messages"] if m["encrypted_payload"] == payload]
    assert len(mine) == 1 and mine[0]["signature"] == sig


def test_signature_survives_rest_fallback_path(pair):
    """WS kapalıyken kullanılan /api/send_ws_fallback yolu da imzayı taşımalı."""
    alice, bob = pair
    payload, sig = _unique()
    raw = json.dumps({"type": "message", "recipient": alice.name,
                      "encrypted_payload": payload, "signature": sig})
    assert bob.post("/api/send_ws_fallback", {"payload": raw}).status_code == 200

    r = alice.get(f"/api/fetch_messages/{alice.name}")
    mine = [m for m in r.json()["messages"] if m["encrypted_payload"] == payload]
    assert len(mine) == 1 and mine[0]["signature"] == sig


def test_end_to_end_real_encryption_and_signature(pair, isolated_keys_dir):
    """Gerçek şifreleme + gerçek imza: gönderen istemci → sunucu → alıcı istemci doğrular ve çözer."""
    from crypto_utils import decrypt_message
    alice, bob = pair
    directory = {alice.name: alice.pub, bob.name: bob.pub}
    bob_client = _Client(bob.name, bob.priv, directory)
    alice_client = _Client(alice.name, alice.priv, directory)

    text = f"gerçek mesaj {uuid.uuid4().hex[:6]}"
    enc = encrypt_message(text, alice.pub)
    sig = bob_client.sign_for(alice.name, enc)

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "encrypted_payload": enc, "signature": sig}))
            return await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == enc)
        finally:
            await ws_a.close(); await ws_b.close()
    got = run(go())

    assert alice_client.verify(got["sender"], got["encrypted_payload"], got["signature"]) is True
    assert decrypt_message(got["encrypted_payload"], alice.priv) == text
