"""static/serverless.html — telefon/tarayıcı sunucusuz sayfası ile masaüstünün karşılıklı uyumu.

Sayfanın çekirdek betiği (script#p2p-core) Node.js'te çalıştırılır (tests/node/p2p_harness.mjs):
Python'un ürettiği kodları doğrular, kendi ürettiği kodları Python'a doğrulatır; parmak izi,
çerçeve ve dosya adı kuralları masaüstüyle birebir karşılaştırılır.
"""

import base64
import json
import re
import shutil
import subprocess
import zlib

import pytest
from cryptography.hazmat.primitives import serialization

from conftest import ROOT
from crypto_utils import get_public_key_fingerprint, public_key_to_pem_string, serialize_private_key
from desktop import p2p_core as core
from desktop import p2p_files as pf

HTML = ROOT / "static" / "serverless.html"
CRYPTO_JS = ROOT / "static" / "js" / "crypto.js"
HARNESS = ROOT / "tests" / "node" / "p2p_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js yok")

SDP = "v=0\r\no=- 7 2 IN IP4 127.0.0.1\r\ns=-\r\na=fingerprint:sha-256 AA:BB:CC\r\n"


def node(*cmds):
    r = subprocess.run(["node", str(HARNESS), str(HTML), str(CRYPTO_JS)], input=json.dumps(cmds),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def keys(keypair_pool):
    (a_priv, a_pub), (b_priv, b_pub), (m_priv, m_pub) = keypair_pool[:3]
    return {"alice": (a_priv, a_pub), "bob": (b_priv, b_pub), "mallory": (m_priv, m_pub)}


def fp(pub):
    return get_public_key_fingerprint(pub)


def pkcs8(priv):
    return serialize_private_key(priv).decode()


# ─────────────────────────── masaüstü → telefon ───────────────────────────

def test_page_verifies_desktop_codes(keys):
    a_priv, a_pub = keys["alice"]
    good = core.make_envelope("offer", "chat", SDP, "alice", a_priv, a_pub)
    m_priv, m_pub = keys["mallory"]
    impostor = core.make_envelope("offer", "chat", SDP, "alice", m_priv, m_pub)
    p = json.loads(zlib.decompress(base64.urlsafe_b64decode(good[3:])))
    p["s"] = SDP.replace("AA:BB:CC", "DD:EE:FF")                    # MITM kendi DTLS parmak izini koyar
    tampered = "h2:" + base64.urlsafe_b64encode(zlib.compress(json.dumps(p).encode())).decode()

    book = {"alice": fp(a_pub)}
    r = node({"op": "identify", "code": good, "contacts": book, "expected": "offer"},
             {"op": "identify", "code": good, "contacts": {}, "expected": "offer"},
             {"op": "identify", "code": impostor, "contacts": book, "expected": "offer"},
             {"op": "identify", "code": tampered, "contacts": book, "expected": "offer"},
             {"op": "identify", "code": good, "contacts": book, "expected": "answer"})
    assert [x["status"] for x in r] == ["verified", "new", "key_changed", "invalid", "invalid"]
    assert r[0]["sdp"] == SDP and r[1]["fingerprint"] == fp(a_pub)


# ─────────────────────────── telefon → masaüstü ───────────────────────────

def test_desktop_verifies_page_codes(keys):
    b_priv, b_pub = keys["bob"]
    a_priv, a_pub = keys["alice"]
    offer_from_desktop = core.make_envelope("offer", "chat", SDP, "alice", a_priv, a_pub)
    page_answer = "v=0\r\no=- 9 2 IN IP4 127.0.0.1\r\ns=-\r\na=fingerprint:sha-256 11:22\r\n"
    r = node({"op": "make", "type": "offer", "mode": "chat", "sdp": SDP, "username": "bob",
              "pkcs8_pem": pkcs8(b_priv)},
             {"op": "make", "type": "answer", "mode": "chat", "sdp": page_answer, "username": "bob",
              "pkcs8_pem": pkcs8(b_priv), "offer_sdp": core.parse_code(offer_from_desktop).sdp})
    offer, answer = r[0]["code"], r[1]["code"]
    assert r[0]["fingerprint"] == fp(b_pub)                          # aynı anahtar → aynı parmak izi

    pem = public_key_to_pem_string(b_pub)
    env = core.parse_code(offer)
    assert core.identify_peer(env, lambda u: pem if u == "bob" else None, "offer").status == core.VERIFIED
    aenv = core.parse_code(answer)
    ident = core.identify_peer(aenv, lambda u: pem if u == "bob" else None, "answer", own_offer_sdp=SDP)
    assert ident.status == core.VERIFIED and aenv.sdp == page_answer
    # başka teklife ait cevap reddedilir
    other = core.identify_peer(aenv, lambda u: pem, "answer", own_offer_sdp="başka teklif")
    assert other.status == core.INVALID


def test_page_rejects_legacy_bombs_and_garbage():
    bomb = "h2:" + base64.urlsafe_b64encode(zlib.compress(b"0" * 2_000_000)).decode()
    r = subprocess.run(["node", "-e", """
        const fs=require('fs'); const h=fs.readFileSync(process.argv[1],'utf8');
        new Function(/<script id="p2p-core">([\\s\\S]*?)<\\/script>/.exec(h)[1])();
        (async()=>{ const out=[]; for (const c of JSON.parse(process.argv[2])) {
          try { await P2PCore.parseCode(c); out.push('ok'); } catch(e) { out.push('err'); } }
          console.log(JSON.stringify(out)); })();""", str(HTML), json.dumps(["", "xx:1", "h2:!!", bomb])],
        capture_output=True, text=True, timeout=60)
    assert json.loads(r.stdout) == ["err"] * 4


# ─────────────────────────── parmak izi (güvenlik hatası düzeltmesi) ───────────────────────────

def test_fingerprints_match_desktop_everywhere(keys):
    _, pub = keys["alice"]
    pem = public_key_to_pem_string(pub)
    no_trailing = pem.rstrip("\n")                                  # web'de üretilen anahtarların PEM biçimi
    crlf = pem.replace("\n", "\r\n")
    r = node({"op": "fingerprint", "pem": pem},
             {"op": "webfp", "pem": pem}, {"op": "webfp", "pem": no_trailing}, {"op": "webfp", "pem": crlf})
    assert {x["fp"] for x in r} == {fp(pub)}


# ─────────────────────────── protokol eşliği ───────────────────────────

def test_frame_validation_matches_desktop():
    fid = "a" * 32
    frames = [core.text_frame("merhaba", "id1"), core.ack_frame("id1"), "not json", "[]",
              json.dumps({"t": "msg", "id": 5, "text": "x"}),
              json.dumps({"t": "file_offer", "fid": fid, "name": "a.pdf", "size": 3, "sha256": "b" * 64}),
              json.dumps({"t": "file_offer", "fid": fid, "name": "a", "size": "3", "sha256": "b" * 64}),
              json.dumps({"t": "file_accept", "fid": "../x"}),
              json.dumps({"t": "file_end", "fid": fid}), json.dumps({"t": "unknown"})]
    page = node({"op": "parse_frame", "frames": frames})[0]
    desktop = [core.parse_frame(f) for f in frames]
    assert page == desktop


def test_safe_filename_matches_desktop():
    names = ["rapor.pdf", "../../etc/passwd", "C:\\Windows\\evil.dll", "..", "", "CON.txt",
             "a\x00b<c>.txt", " .gizli. ", "x" * 300 + ".jpeg", "fotoğraf çok güzel.jpg"]
    assert node({"op": "safe_filename", "names": names})[0] == [pf.safe_filename(n) for n in names]


def test_file_chunks_interoperate():
    fid, data = pf.new_fid(), bytes(range(256)) * 10
    r = node({"op": "chunk", "fid": fid, "data_b64": base64.b64encode(data).decode()},
             {"op": "split", "frame_b64": base64.b64encode(pf.chunk_frame(fid, data)).decode()})
    assert base64.b64decode(r[0]["frame_b64"]) == pf.chunk_frame(fid, data)
    assert (r[1]["fid"], base64.b64decode(r[1]["data_b64"])) == (fid, data)


# ─────────────────────────── sayfa güvenliği ───────────────────────────

def test_page_is_self_contained_and_network_locked():
    html = HTML.read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html).group(1)
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp
    # dış kaynak yok: tek dosya olarak paylaşılabilir ve hiçbir yere istek atmaz
    assert not re.search(r'<(script|link|img|iframe)[^>]+(src|href)=["\']https?:', html)
    assert "Kazuhiko Arase" in html and "MIT" in html                # gömülü kütüphanenin lisansı


# ─────────────────────────── STUN/TURN seçimi (S4) ───────────────────────────

def test_ice_choice_matches_desktop():
    from test_p2p_core import ICE_CUSTOM_BAD, ICE_CUSTOM_OK
    cases = [(p, "") for p in core.ICE_PRESETS if p != "custom"] + [("bilinmeyen", "")]
    cases += [("custom", ICE_CUSTOM_OK), ("custom", ICE_CUSTOM_OK.replace("\n", "\r\n")), ("custom", "")]
    cases += [("custom", bad) for bad in ICE_CUSTOM_BAD]
    page = node(*({"op": "ice", "preset": p, "custom": c} for p, c in cases))
    for (preset, custom), got in zip(cases, page):
        try:
            want = {"servers": core.ice_servers_for(preset, custom)}
        except core.IceConfigError as ex:
            want = {"error": str(ex)}
        assert got == want, (preset, custom)


def test_page_lists_same_presets():
    html = HTML.read_text(encoding="utf-8")
    for key, label in core.ICE_PRESETS.items():
        assert f'"{key}": "{label}"' in html
