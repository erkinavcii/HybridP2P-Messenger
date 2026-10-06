"""'Yazıyor…' göstergesi — sunucu yalnızca canlı relay yapmalı, asla kuyruğa yazmamalı."""

import asyncio
import json

import pytest

from conftest import recv_until, run


@pytest.fixture(scope="module")
def pair(make_user):
    return make_user("ta"), make_user("tb")


def test_typing_relayed_live_with_server_set_sender(pair):
    alice, bob = pair

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "typing", "recipient": alice.name,
                                        "sender": "sahte", "is_typing": True}))
            got = await recv_until(ws_a, "typing")
            assert got == {"type": "typing", "sender": bob.name, "is_typing": True}

            await ws_b.send(json.dumps({"type": "typing", "recipient": alice.name, "is_typing": False}))
            assert (await recv_until(ws_a, "typing"))["is_typing"] is False
        finally:
            await ws_a.close(); await ws_b.close()
    run(go())


def test_typing_to_offline_user_is_not_queued(pair):
    """Bayat 'yazıyor' asla teslim edilmemeli: alıcı sonradan bağlanınca gelmemeli."""
    alice, bob = pair

    async def go():
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "typing", "recipient": alice.name, "is_typing": True}))
            await asyncio.sleep(0.3)
        finally:
            await ws_b.close()

        ws_a = await alice.connect()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await recv_until(ws_a, "typing", timeout=1.0)
        finally:
            await ws_a.close()
    run(go())

    r = alice.get(f"/api/fetch_messages/{alice.name}")
    assert r.status_code == 200


def test_typing_to_self_is_ignored(pair):
    alice, _ = pair

    async def go():
        ws_a = await alice.connect()
        try:
            await ws_a.send(json.dumps({"type": "typing", "recipient": alice.name, "is_typing": True}))
            with pytest.raises(asyncio.TimeoutError):
                await recv_until(ws_a, "typing", timeout=1.0)
        finally:
            await ws_a.close()
    run(go())
