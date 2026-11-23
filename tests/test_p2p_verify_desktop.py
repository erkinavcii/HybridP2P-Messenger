"""P2 (masaüstü): kişi doğrulama, sıkı mod ve P2P sihirbazının adım akışı.

Flet penceresi açılmaz: saf yardımcılar (p2p_core.strict_blocks, desktop/p2p_verify,
pure_p2p.step_state) sahte depo/kanal nesneleriyle sınanır. strict_blocks sayfadaki
P2PCore.strictBlocks ile Node.js üzerinden karşılaştırılır.
"""
import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT
from crypto_utils import get_public_key_fingerprint, public_key_to_pem_string
from desktop import p2p_core, pure_p2p, theme
from desktop import p2p_verify as pv

HTML = ROOT / "static" / "serverless.html"
KINDS = ("text", "audio", "file", "video")


# ── sıkı mod kuralı ──
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("strict", [False, True])
def test_strict_blocks_table(kind, verified, strict):
    expected = strict and not verified and kind in ("file", "video")
    assert p2p_core.strict_blocks(kind, verified, strict) is expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js yok")
def test_strict_blocks_matches_page():
    script = """
const fs = require("fs");
const html = fs.readFileSync(%s, "utf8");
const m = /<script id="p2p-core">([\\s\\S]*?)<\\/script>/.exec(html);
new Function(m[1])();
const C = globalThis.P2PCore, out = [];
for (const k of %s) for (const v of [false, true]) for (const s of [false, true])
  out.push([k, v, s, C.strictBlocks(k, v, s)]);
console.log(JSON.stringify(out));
""" % (json.dumps(str(HTML)), json.dumps(list(KINDS)))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert len(rows) == 16
    for kind, verified, strict, blocked in rows:
        assert p2p_core.strict_blocks(kind, verified, strict) is blocked, (kind, verified, strict)


def test_strict_kinds_static_parity():
    """Node yoksa da: sayfadaki tür listesi Python'dakiyle aynı."""
    m = re.search(r"const STRICT_KINDS = \[([^\]]*)\]", HTML.read_text(encoding="utf-8"))
    assert m and tuple(re.findall(r'"([a-z]+)"', m.group(1))) == p2p_core.STRICT_KINDS


# ── sahte depo ──
class FakeStore:
    def __init__(self):
        self.contacts = {}

    def get_contact(self, username):
        c = self.contacts.get(username)
        if not c:
            return None
        d = dict(c)
        d["is_verified"] = bool(c.get("verified")) and c.get("verified_fp") == c["fingerprint"]
        return d

    def save_contact(self, username, pem, fp):
        old = self.contacts.get(username)
        keep = old and old["fingerprint"] == fp
        self.contacts[username] = {"username": username, "public_key": pem, "fingerprint": fp,
                                   "verified": keep and old.get("verified"),
                                   "verified_fp": old.get("verified_fp") if keep else None}

    def mark_contact_verified(self, username):
        c = self.contacts.get(username)
        if not c:
            return False
        c["verified"], c["verified_fp"] = True, c["fingerprint"]
        return True

    def unmark_contact_verified(self, username):
        c = self.contacts.get(username)
        if c:
            c["verified"], c["verified_fp"] = False, None


@pytest.fixture(scope="module")
def keys(keypair_pool):
    out = {}
    for name, (priv, pub) in zip(("alice", "bob", "mallory"), keypair_pool[:3]):
        out[name] = (pub, public_key_to_pem_string(pub), get_public_key_fingerprint(pub))
    out["_alice_priv"] = keypair_pool[0][0]
    return out


def _ident(keys, name, status=p2p_core.NEW):
    _, pem, fp = keys[name]
    return p2p_core.PeerIdentity(status, username=name, public_key_pem=pem, fingerprint=fp)


# ── güvenlik kodu ──
def test_dialog_safety_number_matches_core_both_orders(keys):
    a_pub, a_pem, _ = keys["alice"]
    b_pub, b_pem, _ = keys["bob"]
    core = p2p_core.safety_number("alice", a_pub, "bob", b_pem)
    assert pv.dialog_safety_number("alice", a_pub, "bob", b_pem) == core
    assert pv.dialog_safety_number("bob", b_pub, "alice", a_pem) == core       # karşı cihaz
    assert re.fullmatch(r"\d{5} \d{5} \d{5} \d{5}", core)


# ── doğrulama durumu ──
def test_confirm_verified_adds_contact_tofu_and_marks(keys):
    store = FakeStore()
    ident = _ident(keys, "bob")
    assert not pv.peer_verified(store, ident)
    assert pv.confirm_verified(store, "bob", ident.public_key_pem, ident.fingerprint) is True
    assert pv.peer_verified(store, ident)
    # İkinci kez: kişi zaten var, yalnızca işaretlenir
    assert pv.confirm_verified(store, "bob", ident.public_key_pem, ident.fingerprint) is False


def test_confirm_verified_refuses_different_key(keys):
    store = FakeStore()
    _, pem, fp = keys["bob"]
    store.save_contact("bob", pem, fp)
    fake = _ident(keys, "mallory")
    with pytest.raises(ValueError):
        pv.confirm_verified(store, "bob", fake.public_key_pem, fake.fingerprint)
    assert not store.get_contact("bob")["is_verified"]


def test_peer_verified_requires_same_key(keys):
    store = FakeStore()
    _, pem, fp = keys["bob"]
    store.save_contact("bob", pem, fp)
    store.mark_contact_verified("bob")
    assert pv.peer_verified(store, _ident(keys, "bob", p2p_core.VERIFIED))
    imposter = p2p_core.PeerIdentity(p2p_core.KEY_CHANGED, username="bob",
                                     public_key_pem=keys["mallory"][1], fingerprint=keys["mallory"][2])
    assert not pv.peer_verified(store, imposter)
    store.unmark_contact_verified("bob")
    assert not pv.peer_verified(store, _ident(keys, "bob", p2p_core.VERIFIED))
    assert not pv.peer_verified(None, _ident(keys, "bob"))
    assert not pv.peer_verified(store, None)


def test_strict_on_reads_setting(monkeypatch):
    monkeypatch.setattr(pv.settings_store, "get", lambda k: k == "p2p_strict")
    assert pv.strict_on() is True
    monkeypatch.setattr(pv.settings_store, "get", lambda k: False)
    assert pv.strict_on() is False


# ── uygulama kancaları ──
def test_incoming_offer_auto_rejected_when_strict_and_unverified():
    sent = []
    offer = {"t": "file_offer", "fid": "abc123", "name": "x.txt", "size": 3, "sha256": "0" * 64}
    assert pv.reject_offer_if_strict(offer, sent.append, peer_is_verified=False, strict=True) is True
    assert [json.loads(s) for s in sent] == [{"t": "file_reject", "fid": "abc123"}]


@pytest.mark.parametrize("verified,strict", [(True, True), (False, False), (True, False)])
def test_incoming_offer_passes_otherwise(verified, strict):
    sent = []
    assert pv.reject_offer_if_strict({"fid": "f"}, sent.append, verified, strict) is False
    assert sent == []


def test_outgoing_file_and_video_hooks():
    assert pv.file_send_blocked(False, True)
    assert not pv.file_send_blocked(True, True) and not pv.file_send_blocked(False, False)
    assert pv.video_refused("video", False, True)
    assert not pv.video_refused("audio", False, True)        # sesli arama serbest
    assert not pv.video_refused("chat", False, True)
    assert not pv.video_refused("video", True, True)


# ── sihirbaz adım akışı ──
def test_step_state_inviter():
    st = lambda cur, n, opened=(): pure_p2p.step_state("inviter", cur, n, opened)
    assert st(1, 1) == ("cur", True, True)
    assert st(1, 2)[2] is False and st(1, 3)[2] is False
    # Yanıt beklenirken yapıştırma adımı da açık
    assert st(2, 1) == ("done", False, True)
    assert st(2, 2) == ("cur", True, True)
    assert st(2, 3) == ("todo", True, True)
    assert st(2, 4)[2] is False
    # Tamamlanan adım tıklanınca yeniden açılır
    assert st(4, 1, {1}) == ("done", True, True)
    assert st(4, 4) == ("cur", True, True)


def test_step_state_joiner():
    st = lambda cur, n: pure_p2p.step_state("joiner", cur, n)
    assert st(1, 1) == ("cur", True, True) and st(1, 2)[2] is False
    assert st(2, 1) == ("done", False, True) and st(2, 3)[2] is False
    assert st(3, 3) == ("cur", True, True)


def test_code_expiry_is_envelope_ts_plus_24h(keys):
    priv, pub = keys["_alice_priv"], keys["alice"][0]
    code = p2p_core.make_envelope("offer", "chat", "v=0\r\n", "alice", priv, pub)
    assert pure_p2p.code_expiry(code) == p2p_core.parse_code(code).ts + 24 * 3600


def test_wizard_uses_chain_loader_not_default_spinners():
    src = (ROOT / "desktop" / "pure_p2p.py").read_text(encoding="utf-8")
    assert "ProgressBar" not in src and "ProgressRing" not in src
    assert src.count("make_loader(") >= 5             # 2 kod üretimi + 3 bekleme
    call_src = (ROOT / "desktop" / "call_screen.py").read_text(encoding="utf-8")
    assert "ChainLoader(" in call_src and "call_safety_panel" in call_src


# ── i18n ──
NEW_KEYS = [
    *pure_p2p.INVITER_STEPS, *pure_p2p.JOINER_STEPS,
    "p2p_show_again", "p2p_hide_again", "p2p_btn_create_invite", "p2p_btn_connect", "p2p_btn_answer",
    "p2p_i_sent_it", "p2p_gathering_offer", "p2p_gathering_answer", "p2p_wait_reply_p", "p2p_keep_open",
    "p2p_valid_until", "p2p_paste_reply_label", "p2p_paste_invite_label", "p2p_connecting_p",
    "p2p_slow_t", "p2p_slow_1", "p2p_slow_2", "p2p_slow_3", "p2p_change_stun", "p2p_wait_conn_t",
    "p2p_dont_close", "p2p_wait_conn_p", "p2p_reset", "p2p_strict_video",
    "v_verified", "v_unverified", "v_unverified_short", "v_verify", "v_unverify", "v_title", "v_state_ok",
    "v_state_no", "v_sn_call", "v_instructions", "v_codes_match", "v_mark_unverified", "v_marked",
    "v_unmarked", "v_no_key", "strict_mode", "strict_hint", "strict_file_out", "strict_file_in",
]


def test_new_i18n_keys_in_both_languages():
    en, tr = theme.ARB["en"], theme.ARB["tr"]
    assert set(en) == set(tr), set(en) ^ set(tr)
    for key in NEW_KEYS:
        assert en.get(key) and tr.get(key), key
    assert tr["v_verified"] == "✓ Doğrulandı" and tr["v_unverified"] == "Doğrulanmadı · Doğrula"
    assert tr["v_codes_match"] == "Kodlar aynı, doğruladım"
    assert tr["loader_waiting_peer"] == "Arkadaşının cevabı bekleniyor…"


def test_p2p_chat_strings_go_through_i18n():
    """p2p_chat.py'de kullanılan tüm t() anahtarları iki dilde de var; çıplak Türkçe metin kalmadı."""
    src = (ROOT / "desktop" / "p2p_chat.py").read_text(encoding="utf-8")
    used = set(re.findall(r'\bt\("([a-z0-9_]+)"', src))
    assert len(used) > 25
    for key in used:
        assert key in theme.ARB["en"] and key in theme.ARB["tr"], key
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#") and "print(" not in ln)
    body = code.split('"""', 2)[2]                     # modül açıklaması hariç
    for word in ("Gönder", "Rehbere", "Reddet", "Kabul et", "İptal", "Bağlantı kapandı"):
        assert f'"{word}' not in body, word


def test_palette_has_warning_tokens():
    for pal in (theme.DARK, theme.LIGHT):
        for key in ("warning", "warning_bg", "success_bg"):
            assert key in pal
