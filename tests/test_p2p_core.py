"""Sunucusuz ("telsiz") mod çekirdeği: imzalı bağlantı kodları, kimlik doğrulama,
veri kanalı protokolü — ve iki gerçek aiortc eşi arasında uçtan uca sohbet."""

import asyncio
import base64
import json
import time
import zlib

import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from crypto_utils import public_key_to_pem_string
from desktop import p2p_core as core


@pytest.fixture(scope="module")
def keys(keypair_pool):
    (a_priv, a_pub), (b_priv, b_pub), (m_priv, m_pub) = keypair_pool[:3]
    return {"alice": (a_priv, a_pub), "bob": (b_priv, b_pub), "mallory": (m_priv, m_pub)}


SDP = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\na=fingerprint:sha-256 AA:BB\r\n"


def _offer(keys, who="alice", mode="chat", sdp=SDP):
    priv, pub = keys[who]
    return core.make_envelope("offer", mode, sdp, who, priv, pub)


def _book(**contacts):
    return lambda name: contacts.get(name)


# ─────────────────────────── kod biçimi ve kimlik ───────────────────────────

def test_roundtrip_and_new_contact(keys):
    env = core.parse_code(_offer(keys))
    assert (env.type, env.mode, env.sdp, env.username) == ("offer", "chat", SDP, "alice")
    ident = core.identify_peer(env, _book(), "offer")
    assert ident.status == core.NEW and not ident.blocked and ident.fingerprint


def test_verified_when_key_matches_contact(keys):
    pem = public_key_to_pem_string(keys["alice"][1])
    ident = core.identify_peer(core.parse_code(_offer(keys)), _book(alice=pem), "offer")
    assert ident.status == core.VERIFIED and ident.can_save_history


def test_impostor_with_own_key_is_blocked(keys):
    """Mallory 'alice' adıyla, kendi anahtarıyla imzalı kod üretir; rehberde gerçek alice var."""
    m_priv, m_pub = keys["mallory"]
    code = core.make_envelope("offer", "chat", SDP, "alice", m_priv, m_pub)
    pem = public_key_to_pem_string(keys["alice"][1])
    ident = core.identify_peer(core.parse_code(code), _book(alice=pem), "offer")
    assert ident.status == core.KEY_CHANGED and ident.blocked


def _tamper(code, **changes):
    p = json.loads(zlib.decompress(base64.urlsafe_b64decode(code[3:])))
    p.update(changes)
    return "h2:" + base64.urlsafe_b64encode(zlib.compress(json.dumps(p).encode())).decode()


@pytest.mark.parametrize("change", [
    {"s": SDP.replace("AA:BB", "CC:DD")},     # MITM kendi DTLS parmak izini koyar
    {"m": "video"},
    {"u": "bob"},
    {"ts": 1},
])
def test_tampered_code_is_invalid(keys, change):
    env = core.parse_code(_tamper(_offer(keys), **change))
    assert core.identify_peer(env, _book(), "offer").status == core.INVALID


def test_answer_must_answer_our_offer(keys):
    b_priv, b_pub = keys["bob"]
    ans = core.make_envelope("answer", "chat", SDP, "bob", b_priv, b_pub, offer_sdp="başka teklif")
    ident = core.identify_peer(core.parse_code(ans), _book(), "answer", own_offer_sdp=SDP)
    assert ident.status == core.INVALID and "teklifinize" in ident.detail
    ok = core.make_envelope("answer", "chat", SDP, "bob", b_priv, b_pub, offer_sdp=SDP)
    assert core.identify_peer(core.parse_code(ok), _book(), "answer", own_offer_sdp=SDP).status == core.NEW


def test_wrong_direction_and_stale_codes(keys):
    env = core.parse_code(_offer(keys))
    assert core.identify_peer(env, _book(), "answer").status == core.INVALID
    later = time.time() + core.MAX_CODE_AGE_SEC + 60
    assert core.identify_peer(env, _book(), "offer", now=later).status == core.INVALID


def test_legacy_codes_parse_but_are_unverified():
    legacy = "z1:" + base64.b64encode(zlib.compress(json.dumps(
        {"sdp": SDP, "type": "offer", "call_type": "video"}).encode())).decode()
    env = core.parse_code(legacy)
    assert (env.version, env.mode) == (1, "video")
    assert core.identify_peer(env, _book(), "offer").status == core.LEGACY


@pytest.mark.parametrize("bad", ["", "xx:abc", "h2:!!!", "h2:" + "A" * 20000,
                                 "h2:" + base64.urlsafe_b64encode(zlib.compress(b"0" * 2_000_000)).decode()])
def test_bad_codes_rejected(bad):
    with pytest.raises(core.P2PCodeError):
        core.parse_code(bad)


def test_whitespace_from_copy_paste_is_ignored(keys):
    code = _offer(keys)
    spaced = "\n".join(code[i:i + 60] for i in range(0, len(code), 60))
    assert core.parse_code("  " + spaced + "  ").sdp == SDP


# ─────────────────────────── veri kanalı protokolü ───────────────────────────

def test_frames():
    f = core.parse_frame(core.text_frame("merhaba", "id1"))
    assert (f["t"], f["id"], f["text"]) == ("msg", "id1", "merhaba")
    assert core.parse_frame(core.ack_frame("id1")) == {"t": "ack", "id": "id1"}
    assert core.parse_frame(core.text_frame("x" * 20000))["text"] == "x" * core.MAX_TEXT_CHARS
    for bad in ["not json", "[]", '{"t":"msg","id":5,"text":"a"}', '{"t":"unknown"}', b"\x00"]:
        assert core.parse_frame(bad) is None


# ─────────────────────────── uçtan uca: iki gerçek eş ───────────────────────────

def test_two_peers_chat_end_to_end_without_server(keys):
    """Sunucu yok, STUN yok (yalnızca yerel adaylar): alice teklif üretir, bob cevaplar,
    iki taraf da kimliği doğrular, veri kanalından mesaj ve onay gider gelir."""
    a_priv, a_pub = keys["alice"]
    b_priv, b_pub = keys["bob"]
    book_a = _book(bob=public_key_to_pem_string(b_pub))
    book_b = _book(alice=public_key_to_pem_string(a_pub))

    async def gather(pc):
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.02)

    async def scenario():
        cfg = RTCConfiguration(iceServers=[])
        pa, pb = RTCPeerConnection(cfg), RTCPeerConnection(cfg)
        got_b, got_a = asyncio.Queue(), asyncio.Queue()
        try:
            ch_a = pa.createDataChannel(core.CHANNEL_LABEL)
            ch_a.on("message", lambda m: got_a.put_nowait(core.parse_frame(m)))
            await pa.setLocalDescription(await pa.createOffer())
            await gather(pa)
            offer_code = core.make_envelope("offer", "chat", pa.localDescription.sdp, "alice", a_priv, a_pub)

            # — bob: teklifi doğrula, cevap üret —
            env = core.parse_code(offer_code)
            assert core.identify_peer(env, book_b, "offer").status == core.VERIFIED

            @pb.on("datachannel")
            def on_dc(ch):
                @ch.on("message")
                def on_msg(m):
                    f = core.parse_frame(m)
                    got_b.put_nowait(f)
                    if f and f["t"] == "msg":
                        ch.send(core.ack_frame(f["id"]))

            await pb.setRemoteDescription(RTCSessionDescription(env.sdp, "offer"))
            await pb.setLocalDescription(await pb.createAnswer())
            await gather(pb)
            answer_code = core.make_envelope("answer", "chat", pb.localDescription.sdp, "bob",
                                             b_priv, b_pub, offer_sdp=env.sdp)

            # — alice: cevabı doğrula, bağlan —
            aenv = core.parse_code(answer_code)
            ident = core.identify_peer(aenv, book_a, "answer", own_offer_sdp=pa.localDescription.sdp)
            assert ident.status == core.VERIFIED and ident.username == "bob"
            await pa.setRemoteDescription(RTCSessionDescription(aenv.sdp, "answer"))

            for _ in range(200):
                if ch_a.readyState == "open":
                    break
                await asyncio.sleep(0.05)
            assert ch_a.readyState == "open"
            ch_a.send(core.text_frame("merhaba bob, sunucu yok 📻", "m1"))
            msg = await asyncio.wait_for(got_b.get(), 10)
            ack = await asyncio.wait_for(got_a.get(), 10)
            return msg, ack
        finally:
            await pa.close()
            await pb.close()

    msg, ack = core.run(scenario()).result(timeout=60)
    assert msg["text"] == "merhaba bob, sunucu yok 📻" and ack == {"t": "ack", "id": "m1"}


# ─────────────────────────── STUN/TURN seçimi (S4) ───────────────────────────

ICE_CUSTOM_OK = """# kendi coturn sunucum
stun:turn.ornek.com:3478
turn:turn.ornek.com:3478?transport=udp alice s3cr3t!
turns:[2001:db8::1]:5349 1700000000:alice abc=="""

ICE_CUSTOM_BAD = [
    "http://ornek.com",                         # şema
    "stun:ornek.com:70000",                     # port
    "stun:ornek.com user pass",                 # STUN'a kimlik
    "stun:ornek.com?transport=udp",             # STUN'a transport
    "turn:ornek.com:3478",                      # TURN'de kimlik yok
    "turn:ornek.com:3478 kullanıcı şifre",      # ASCII dışı kimlik
    "stun:ornek.com:३४७८",                      # ASCII dışı rakam
    "\n".join(f"stun:s{i}.ornek.com" for i in range(9)),   # 8'den fazla
]


def test_ice_presets():
    assert core.ice_servers_for("lan") == []
    assert core.ice_servers_for("cloudflare") == [{"urls": "stun:stun.cloudflare.com:3478"}]
    assert [s["urls"] for s in core.ice_servers_for("google")] == [
        "stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]
    default = core.ice_servers_for(core.ICE_DEFAULT)
    assert len(default) == 3 and core.ice_servers_for("bilinmeyen") == default


def test_ice_custom_list():
    servers = core.ice_servers_for("custom", ICE_CUSTOM_OK)
    assert servers == [
        {"urls": "stun:turn.ornek.com:3478"},
        {"urls": "turn:turn.ornek.com:3478?transport=udp", "username": "alice", "credential": "s3cr3t!"},
        {"urls": "turns:[2001:db8::1]:5349", "username": "1700000000:alice", "credential": "abc=="},
    ]
    with pytest.raises(core.IceConfigError, match="boş"):
        core.ice_servers_for("custom", "# yalnızca yorum\n\n")
    for bad in ICE_CUSTOM_BAD:
        with pytest.raises(core.IceConfigError):
            core.ice_servers_for("custom", bad)


def test_ice_config_reaches_aiortc(monkeypatch):
    from desktop import pure_p2p, settings_store
    values = {"p2p_ice": "custom", "p2p_ice_custom": ICE_CUSTOM_OK}
    monkeypatch.setattr(settings_store, "get", values.get)
    cfg = pure_p2p._ice_config()
    assert [(s.urls, s.username, s.credential) for s in cfg.iceServers] == [
        (["stun:turn.ornek.com:3478"], None, None),
        (["turn:turn.ornek.com:3478?transport=udp"], "alice", "s3cr3t!"),
        (["turns:[2001:db8::1]:5349"], "1700000000:alice", "abc=="),
    ]
    values["p2p_ice"] = "lan"
    assert pure_p2p._ice_config().iceServers == []
    values.update(p2p_ice="custom", p2p_ice_custom="turn:x")
    with pytest.raises(core.IceConfigError):
        pure_p2p._ice_config()
