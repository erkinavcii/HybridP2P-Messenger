"""Link önizleme.

Sunucu: encrypted_preview + preview_sig mesajla birlikte bütün teslim yollarından
(canlı, çevrimdışı kuyruk, REST fetch, WS fallback) aynen geçmeli.
İstemci: üretim yerel ağa istek atmamalı; gelen önizleme temizlenmeli.
"""

import base64
import io
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from conftest import recv_until, run
from desktop import linkpreview


@pytest.fixture(scope="module")
def pair(make_user):
    return make_user("lpa"), make_user("lpb")


def _frame(alice, tag):
    return {"type": "message", "recipient": alice.name, "encrypted_payload": f"p-{tag}",
            "signature": "s", "encrypted_preview": f"prev-{tag}", "preview_sig": f"psig-{tag}"}


def test_preview_fields_relayed_on_all_paths(pair):
    alice, bob = pair
    live, off, fb = (uuid.uuid4().hex for _ in range(3))

    async def go_live():
        ws_a = await alice.connect(); ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps(_frame(alice, live)))
            return await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == f"p-{live}")
        finally:
            await ws_a.close(); await ws_b.close()
    got = run(go_live())
    assert (got["encrypted_preview"], got["preview_sig"]) == (f"prev-{live}", f"psig-{live}")

    async def send_offline():
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps(_frame(alice, off)))
            return await recv_until(ws_b, "delivery_ack")
        finally:
            await ws_b.close()
    assert run(send_offline())["status"] == "stored_offline"
    assert bob.post("/api/send_ws_fallback", {"payload": json.dumps(_frame(alice, fb))}).status_code == 200

    msgs = {m["encrypted_payload"]: m for m in alice.get(f"/api/fetch_messages/{alice.name}").json()["messages"]}
    for tag in (off, fb):
        assert (msgs[f"p-{tag}"]["encrypted_preview"], msgs[f"p-{tag}"]["preview_sig"]) == (f"prev-{tag}", f"psig-{tag}")


def test_message_without_preview_has_empty_fields(pair):
    alice, bob = pair
    tag = uuid.uuid4().hex

    async def go():
        ws_a = await alice.connect(); ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "message", "recipient": alice.name,
                                        "encrypted_payload": f"p-{tag}", "signature": "s"}))
            return await recv_until(ws_a, "message", match=lambda d: d["encrypted_payload"] == f"p-{tag}")
        finally:
            await ws_a.close(); await ws_b.close()
    got = run(go())
    assert got["encrypted_preview"] == "" and got["preview_sig"] == ""


# ─────────────────────────── linkpreview modülü ───────────────────────────

@pytest.mark.parametrize("text,url", [
    ("bak https://example.com/a?b=1.", "https://example.com/a?b=1"),
    ("(http://x.org/y)", "http://x.org/y"),
    ("link yok", None),
    ("javascript:alert(1)", None),
])
def test_find_first_url(text, url):
    assert linkpreview.find_first_url(text) == url


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://192.168.1.1/",
                                 "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data",
                                 "http://[::1]/", "file:///etc/passwd"])
def test_private_targets_refused(url):
    assert linkpreview.fetch_preview(url) is None


def _png(w=800, h=600):
    out = io.BytesIO(); Image.new("RGB", (w, h), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def site():
    png = _png()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302); self.send_header("Location", "/page"); self.end_headers(); return
            if self.path == "/img.png":
                self.send_response(200); self.send_header("Content-Type", "image/png"); self.end_headers()
                self.wfile.write(png); return
            body = (b'<html><head><title>Yedek</title>'
                    b'<meta property="og:title" content="Baslik &amp; Test">'
                    b'<meta property="og:description" content="Aciklama">'
                    b'<meta property="og:image" content="/img.png"></head></html>')
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(body)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_fetch_preview_extracts_og_and_thumbnail(site):
    p = linkpreview.fetch_preview(site + "/redir", allow_private=True)
    assert p["title"] == "Baslik & Test" and p["description"] == "Aciklama"
    assert p["url"] == site + "/redir"
    img = Image.open(io.BytesIO(base64.b64decode(p["image"])))
    assert img.format == "JPEG" and max(img.size) <= linkpreview.THUMB_MAX


def test_redirect_to_private_is_refused_without_flag(site):
    # Genel bir adres gibi görünse de localhost'a giden her adım reddedilir
    assert linkpreview.fetch_preview(site + "/redir") is None


@pytest.mark.parametrize("bad", [
    None, "değil-json", [], {"url": "javascript:alert(1)", "title": "x"},
    {"url": "https://a.com", "title": "", "description": ""},
    {"url": 5, "title": "x"},
])
def test_sanitize_rejects_bad(bad):
    assert linkpreview.sanitize(bad) is None


def test_sanitize_truncates_and_reencodes():
    raw = base64.b64encode(_png(50, 50)).decode()
    p = linkpreview.sanitize({"url": "https://a.com", "title": "t" * 999, "description": 7, "image": raw})
    assert len(p["title"]) == linkpreview.MAX_TITLE and p["description"] == ""
    assert Image.open(io.BytesIO(base64.b64decode(p["image"]))).format == "JPEG"
    assert linkpreview.sanitize({"url": "https://a.com", "title": "t", "image": "!!bozuk"})["image"] is None


# ─────────────────────────── İstemci: imza bağlama ve depo ───────────────────────────

from crypto_utils import encrypt_message
from test_edit_delete import _Peer

PREVIEW = {"url": "https://example.com/x", "title": "Ornek", "description": "Aciklama", "image": None}


@pytest.fixture
def peers(isolated_keys_dir, keypair_pool):
    (a_priv, a_pub), (b_priv, b_pub), (m_priv, _) = keypair_pool[:3]
    directory = {"alice": a_pub, "bob": b_pub}
    out = []
    return (_Peer("alice", a_priv, directory, out), _Peer("bob", b_priv, directory, out),
            _Peer("bob", m_priv, directory, out), out, a_pub)


def _bob_sends(bob, out, a_pub, text, preview=PREVIEW):
    enc = encrypt_message(text, a_pub)
    bob.impl.send_message_via_ws("alice", enc, False, msg_uid=uuid.uuid4().hex, preview=preview)
    return out.pop()


def test_preview_roundtrip_is_encrypted_and_verified(peers):
    alice, bob, _m, out, a_pub = peers
    f = _bob_sends(bob, out, a_pub, "bak https://example.com/x")
    assert "example.com" not in f["encrypted_preview"]          # sunucu URL'yi görmez
    got = json.loads(alice.impl.verified_preview("bob", f["encrypted_payload"],
                                                 f["encrypted_preview"], f["preview_sig"]))
    assert got["title"] == "Ornek" and got["url"] == PREVIEW["url"]


def test_message_without_preview_sends_no_fields(peers):
    _a, bob, _m, out, a_pub = peers
    f = _bob_sends(bob, out, a_pub, "duz metin", preview=None)
    assert "encrypted_preview" not in f


@pytest.mark.parametrize("attack", ["unsigned", "wrong_key", "moved_to_other_message"])
def test_forged_preview_dropped(peers, attack):
    alice, bob, mallory_as_bob, out, a_pub = peers
    f = _bob_sends(bob, out, a_pub, "bir")
    payload, enc_prev, sig = f["encrypted_payload"], f["encrypted_preview"], f["preview_sig"]
    if attack == "unsigned":
        sig = ""
    elif attack == "wrong_key":
        g = _bob_sends(mallory_as_bob, out, a_pub, "bir")
        payload, enc_prev, sig = g["encrypted_payload"], g["encrypted_preview"], g["preview_sig"]
    else:  # sunucu bu önizlemeyi bob'un başka bir mesajına takar
        payload = _bob_sends(bob, out, a_pub, "iki", preview=None)["encrypted_payload"]
    assert alice.impl.verified_preview("bob", payload, enc_prev, sig) is None


def test_store_preview_lifecycle(peers):
    alice, *_ = peers
    s = alice.store
    s.save_message("bob", "alice", "link", True, "2026-10-06T10:00:00+00:00", msg_uid="m1")
    s.save_message("bob", "bob", "onun", False, "2026-10-06T10:01:00+00:00", msg_uid="m2",
                   preview=json.dumps(PREVIEW))
    assert s.set_message_preview("bob", "m1", json.dumps(PREVIEW)) is True
    assert s.set_message_preview("bob", "m2", "{}") is False      # başkasının mesajına eklenemez
    msgs = {m["msg_uid"]: m for m in s.get_messages("bob")}
    assert json.loads(msgs["m1"]["preview"])["title"] == "Ornek"
    assert json.loads(msgs["m2"]["preview"])["title"] == "Ornek"

    s.edit_message("bob", "m1", "alice", "degisti")
    s.delete_message("bob", "m2", "bob")
    msgs = {m["msg_uid"]: m for m in s.get_messages("bob")}
    assert msgs["m1"]["preview"] is None and msgs["m2"]["preview"] is None
