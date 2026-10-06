"""WebSocket ve REST fallback mesaj boyut sınırı."""

import json
import uuid

import pytest
import websockets

from conftest import recv_until, run


@pytest.fixture(scope="module")
def sender(make_user):
    return make_user("lim")


async def _still_alive(ws):
    await ws.send(json.dumps({"type": "ping"}))
    await recv_until(ws, "pong")


def test_message_under_limit_is_accepted(server, sender):
    async def go():
        ws = await sender.connect()
        try:
            payload = "A" * (server.max_ws - 2000)
            await ws.send(json.dumps({"type": "message", "recipient": f"nobody_{uuid.uuid4().hex[:6]}",
                                      "encrypted_payload": payload}))
            ack = await recv_until(ws, "delivery_ack")
            assert ack["status"] == "stored_offline"
        finally:
            await ws.close()
    run(go())


def test_oversized_message_rejected_but_connection_survives(server, sender):
    async def go():
        ws = await sender.connect()
        try:
            await ws.send(json.dumps({"type": "message", "recipient": "x",
                                      "encrypted_payload": "A" * (server.max_ws + 1000)}))
            err = await recv_until(ws, "error")
            assert err["code"] == "payload_too_large"
            assert err["max_size"] == server.max_ws
            await _still_alive(ws)
        finally:
            await ws.close()
    run(go())


@pytest.mark.parametrize("raw", ["{bozuk json", "[1, 2, 3]", '"sadece string"'])
def test_invalid_json_rejected_but_connection_survives(sender, raw):
    async def go():
        ws = await sender.connect()
        try:
            await ws.send(raw)
            err = await recv_until(ws, "error")
            assert err["code"] == "invalid_json"
            await _still_alive(ws)
        finally:
            await ws.close()
    run(go())


def test_frame_above_protocol_cap_closes_connection(server, sender):
    """Uygulama sınırının 2 katından büyük çerçeve belleğe alınmadan kesilmeli (1009)."""
    async def go():
        ws = await sender.connect()
        await ws.send("A" * (server.max_ws * 2 + 10_000))
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await recv_until(ws, "pong", timeout=5)
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 1009
    run(go())


def test_rest_fallback_enforces_same_limit(server, sender):
    """WS sınırı REST fallback yolundan atlatılamamalı."""
    big = json.dumps({"type": "message", "recipient": "x",
                      "encrypted_payload": "A" * (server.max_ws + 1000)})
    assert sender.post("/api/send_ws_fallback", {"payload": big}).status_code == 422

    small = json.dumps({"type": "message", "recipient": f"nobody_{uuid.uuid4().hex[:6]}",
                        "encrypted_payload": "küçük"})
    assert sender.post("/api/send_ws_fallback", {"payload": small}).status_code == 200
