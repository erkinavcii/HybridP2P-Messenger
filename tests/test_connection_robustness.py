"""Bağlantı yönetimi regresyon testleri.

Gerçekte gözlenen hata zinciri:
  1. Bekleyen offline mesajı olan kullanıcı bağlanıp teslim sırasında koptu.
  2. Teslim try/except dışındaydı → handler çöktü, kullanıcı listeden silinmedi
     (ölü socket'le "online" kaldı).
  3. Başkası ona yazınca ölü socket'e yazma hatası GÖNDERENİN handler'ında
     patladı → gönderenin bağlantısı da koptu.
  4. Kuyruk toplu silindiği için teslim edilemeyen mesajlar kayboluyordu.
"""

import asyncio
import json
import uuid

import pytest

from conftest import recv_until, run


@pytest.fixture(scope="module")
def trio(make_user):
    return make_user("ra"), make_user("rb"), make_user("rc")


def _status(asker, name) -> bool:
    r = asker.get(f"/api/status/{name}")
    assert r.status_code == 200
    return r.json()["online"]


async def _queue(sender, recipient_name, n, size=20_000):
    """recipient çevrimdışıyken n adet benzersiz mesaj kuyruğa yazar."""
    tags = [f"q-{uuid.uuid4().hex}" for _ in range(n)]
    ws = await sender.connect()
    try:
        for t in tags:
            await ws.send(json.dumps({"type": "message", "recipient": recipient_name,
                                      "encrypted_payload": t + "-" + "A" * size}))
            assert (await recv_until(ws, "delivery_ack"))["status"] == "stored_offline"
    finally:
        await ws.close()
    return tags


def test_abrupt_disconnect_during_pending_delivery(trio):
    alice, bob, carol = trio

    async def go():
        tags = await _queue(carol, bob.name, 40)

        # bob bağlanır, kimlik doğrular ve hiçbir şey okumadan bağlantıyı keser
        ws_b = await bob.connect()
        await ws_b.close()
        await asyncio.sleep(1.0)
        return tags
    tags = run(go())

    # (2) bob hayalet "online" kalmamalı
    assert _status(alice, bob.name) is False

    # (3) alice bob'a yazınca alice'in bağlantısı kopmamalı
    async def alice_writes():
        ws_a = await alice.connect()
        try:
            extra = f"q-{uuid.uuid4().hex}"
            await ws_a.send(json.dumps({"type": "message", "recipient": bob.name,
                                        "encrypted_payload": extra}))
            assert (await recv_until(ws_a, "delivery_ack"))["status"] == "stored_offline"
            await ws_a.send(json.dumps({"type": "ping"}))
            await recv_until(ws_a, "pong")
            return extra
        finally:
            await ws_a.close()
    extra = run(alice_writes())

    # (4) hiçbir mesaj kaybolmamalı: bob tekrar bağlanınca hepsi gelir
    async def bob_collects():
        want = {*tags, extra}
        seen = set()
        ws_b = await bob.connect()
        try:
            while seen != want:
                d = await recv_until(ws_b, "message", timeout=10)
                seen.add(d["encrypted_payload"][:34])  # etiket = "q-" + 32 hex
        finally:
            await ws_b.close()
        return seen
    seen = run(bob_collects())
    assert seen == {*tags, extra}


def test_reconnect_does_not_evict_new_connection(trio):
    """Eski bağlantının kapanması, aynı kullanıcının YENİ bağlantısını listeden silmemeli."""
    alice, bob, _ = trio

    async def go():
        old = await alice.connect()
        new = await alice.connect()
        await old.close()
        await asyncio.sleep(0.5)

        tag = f"r-{uuid.uuid4().hex}"
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "encrypted_payload": tag}))
            ack = await recv_until(ws_b, "delivery_ack")
            got = await recv_until(new, "message", match=lambda d: d["encrypted_payload"] == tag)
            return ack, got
        finally:
            await ws_b.close(); await new.close()
    ack, got = run(go())
    assert ack["status"] == "delivered_online"
    assert got["sender"] == bob.name
