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


# ─────────────────────────── sesli/görüntülü arama (S3b) ───────────────────────────

def test_page_offers_every_desktop_mode():
    html = HTML.read_text(encoding="utf-8")
    select = re.search(r'<select id="mode">(.*?)</select>', html, re.S).group(1)
    assert re.findall(r'<option value="(\w+)"', select) == list(core.MODES)
    # eski "yalnızca yazılı mesajlaşma" engeli kalktı; arama izinleri ve medya CSP'si yerinde
    assert "şimdilik yalnızca yazılı mesajlaşma" not in html
    assert "getUserMedia" in html and "media-src blob: mediastream:" in html


def test_page_blocks_unverifiable_legacy_codes():
    # Masaüstü eski (imzasız) kodlarla aramaya uyarıyla izin verir; sayfa hiç kabul etmez
    legacy = "z1:" + base64.b64encode(zlib.compress(json.dumps(
        {"type": "offer", "sdp": SDP, "call_type": "video"}).encode())).decode()
    assert node({"op": "identify", "code": legacy, "contacts": {}, "expected": "offer"})[0]["status"] == "legacy"
    html = HTML.read_text(encoding="utf-8")
    assert "C.isBlocked(id) || id.status === C.S.LEGACY" in html


# ─────────────────────────── otomatik kalite düşürme ───────────────────────────

def test_quality_policy_identical_in_page_module_and_desktop():
    from desktop import call_quality as q
    from test_call_quality import SEQUENCES
    seqs = [[[loss, rtt] for loss, rtt in samples] for samples, _ in SEQUENCES]
    want = []
    for seq in seqs:
        p, levels = q.QualityPolicy(), []
        for loss, rtt in seq:
            p.step(loss, rtt)
            levels.append(p.level)
        want.append(levels)

    def r(**kw):
        return {"type": "remote-inbound-rtp", **kw}
    pair = {"type": "candidate-pair", "nominated": True, "state": "succeeded"}
    reports = [
        [r(kind="audio", fractionLost=0.0, roundTripTime=0.05), r(kind="video", fractionLost=0.25, roundTripTime=0.3)],
        [r(kind="audio", fractionLost=0.01, roundTripTime=0.05)],
        [],
        # Bant kısıtı yok sayılır (bizim bit hızı sınırımız onu kendisi üretir); RTT çiftten
        [{"type": "outbound-rtp", "kind": "video", "qualityLimitationReason": "bandwidth"},
         {**pair, "availableOutgoingBitrate": 120_000, "currentRoundTripTime": 0.02}],
    ]
    out = node({"op": "quality", "quality_js": str(ROOT / "static" / "js" / "quality.js"),
                "sequences": seqs, "reports": reports})[0]
    assert out["page"] == want and out["module"] == want
    expected_stats = [
        {"loss": 0.25, "rtt": 0.3},
        {"loss": 0.01, "rtt": 0.05},
        {"loss": None, "rtt": None},
        {"loss": None, "rtt": 0.02},
    ]
    assert out["page_stats"] == expected_stats and out["module_stats"] == expected_stats
    assert [l[0] for l in out["labels"]] == [l[1] for l in out["labels"]]
    assert out["labels"][0] == ["", "", ""] and "180p" in out["labels"][3][0]


def test_page_embeds_quality_js_verbatim():
    import re as _re
    mod = (ROOT / "static" / "js" / "quality.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    body = _re.sub(r"^export ", "", mod[mod.index("export const LOSS_BAD"):], flags=_re.M)
    page = HTML.read_text(encoding="utf-8").replace("\r\n", "\n")
    for line in body.splitlines():
        if line.strip():
            assert ("  " + line) in page, line


# ─────────────────────────── güvenlik kodu (kişi doğrulama) ───────────────────────────

# Sabit anahtarlar + bilinen sonuç: algoritma sessizce değişirse iki tarafın kodları uyuşmaz.
KAT_A_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDKPTwsvjB5r7TfLrXfUaBSBmA/
+/biov2sROsqGhuyEFW4Ouh1Bqo/1b1VDoc2jGIFhTlR9L/O9PtvQNELKojRVrXE
ZSHS4dTrKEBcn8ixesaTjlFDRgCa0UqN8xquGtlRU2P/IVaUwM3Kys1p1BzhEt1X
uMvKczu8TJUkdWgWEQIDAQAB
-----END PUBLIC KEY-----
"""
KAT_B_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCjqRD4J8cZtFc0je37TKFPewFp
jwJFBYfrVrgT4ENKcz7/h5EsGFlh0Y1pmSNhbrbOHtbXwzMXAYx+5VFRV/em0Mq8
UVIFrN7OyWJ4oc7HN37XlJs22PUqsjnGjNiwk9GhhbOP8Z40UWvAXnfcpvPWwk20
4G1hF3c71Jw3ELhe2QIDAQAB
-----END PUBLIC KEY-----
"""
KAT_SN = "83593 34410 85029 95495"          # alice=KAT_A, bob=KAT_B
SN_RE = re.compile(r"^\d{5} \d{5} \d{5} \d{5}$")


def test_safety_number_known_answer():
    assert core.safety_number("alice", KAT_A_PEM, "bob", KAT_B_PEM) == KAT_SN
    assert core.safety_number("bob", KAT_B_PEM, "alice", KAT_A_PEM) == KAT_SN
    r = node({"op": "safety", "a": "alice", "a_pem": KAT_A_PEM, "b": "bob", "b_pem": KAT_B_PEM},
             {"op": "safety", "a": "bob", "a_pem": KAT_B_PEM, "b": "alice", "b_pem": KAT_A_PEM})
    assert [x["sn"] for x in r] == [KAT_SN, KAT_SN]


def test_safety_number_matches_desktop(keys):
    pems = {n: public_key_to_pem_string(pub) for n, (_, pub) in keys.items()}
    objs = {n: pub for n, (_, pub) in keys.items()}
    cases = [("alice", "alice", "bob", "bob"), ("bob", "bob", "alice", "alice"),
             ("alice", "alice", "mallory", "mallory"), ("bob", "bob", "mallory", "mallory"),
             ("alice", "mallory", "bob", "bob"),        # alice adına mallory'nin anahtarı
             ("carol_9", "alice", "bob", "bob"),        # aynı anahtar, farklı ad
             ("alice", "alice", "bob", "alice")]        # karşı tarafın anahtarı değişti
    r = node(*[{"op": "safety", "a": ua, "a_pem": pems[ka], "b": ub, "b_pem": pems[kb]}
               for ua, ka, ub, kb in cases])
    py = [core.safety_number(ua, objs[ka], ub, objs[kb]) for ua, ka, ub, kb in cases]
    assert [x["sn"] for x in r] == py
    assert all(SN_RE.match(s) for s in py)
    assert py[0] == py[1]                                   # sıradan bağımsız
    assert core.safety_number("alice", pems["alice"], "bob", pems["bob"]) == py[0]   # PEM = nesne
    base = py[0]
    assert py[4] != base and py[5] != base and py[6] != base   # anahtar ya da ad değişirse kod değişir
    assert core.safety_number("alice", objs["alice"], "bobby", objs["bob"]) != base


def test_safety_number_rejects_same_user(keys):
    _, a_pub = keys["alice"]
    _, b_pub = keys["bob"]
    with pytest.raises(ValueError):
        core.safety_number("alice", a_pub, "alice", b_pub)
    pem_a, pem_b = public_key_to_pem_string(a_pub), public_key_to_pem_string(b_pub)
    r = node({"op": "safety", "a": "alice", "a_pem": pem_a, "b": "alice", "b_pem": pem_b})
    assert "error" in r[0] and "sn" not in r[0]
