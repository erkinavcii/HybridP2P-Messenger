"""P3 + L2: sunucusuz sayfada kişi doğrulama, sıkı mod, adım akışı ve marka yükleyicisi.

strictBlocks / contactVerified sayfanın kendi çekirdek betiğinden (script#p2p-core) Node.js'te
çalıştırılır; i18n sözlüğü sayfadan çıkarılıp Node'da değerlendirilir.
"""
import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

HTML = ROOT / "static" / "serverless.html"
INDEX = ROOT / "static" / "index.html"
STYLES = ROOT / "static" / "css" / "styles.css"
APP_JS = ROOT / "static" / "js" / "app.js"
SW = ROOT / "static" / "sw.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js yok")


def page():
    return HTML.read_text(encoding="utf-8")


def run_node(script):
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


CORE_LOADER = """
const fs = require("fs");
const html = fs.readFileSync(%s, "utf8");
const m = /<script id="p2p-core">([\\s\\S]*?)<\\/script>/.exec(html);
new Function(m[1])();
const C = globalThis.P2PCore;
"""


@needs_node
def test_strict_blocks_truth_table():
    script = CORE_LOADER % json.dumps(str(HTML)) + """
const out = [];
for (const kind of ["text", "audio", "file", "video"])
  for (const v of [false, true])
    for (const s of [false, true]) out.push([kind, v, s, C.strictBlocks(kind, v, s)]);
console.log(JSON.stringify(out));
"""
    for kind, verified, strict, blocked in run_node(script):
        expected = strict and not verified and kind in ("file", "video")
        assert blocked is expected, (kind, verified, strict)


@needs_node
def test_contact_verified_requires_current_key():
    script = CORE_LOADER % json.dumps(str(HTML)) + """
const fp = "AA:BB";
console.log(JSON.stringify([
  C.contactVerified({ fp, verified: true, verifiedFp: fp }, fp),
  C.contactVerified({ fp, verified: true, verifiedFp: "OLD" }, fp),
  C.contactVerified({ fp, verified: false, verifiedFp: fp }, fp),
  C.contactVerified({ fp }, fp),
  C.contactVerified(null, fp),
  C.contactVerified({ fp, verified: true, verifiedFp: fp }, "CC:DD"),
]));
"""
    assert run_node(script) == [True, False, False, False, False, False]


def _i18n():
    m = re.search(r"const I18N = (\{[\s\S]*?\n  \});\n", page())
    assert m, "I18N sözlüğü bulunamadı"
    return run_node("console.log(JSON.stringify(" + m.group(1) + "));")


@needs_node
def test_i18n_keys_present_in_both_languages():
    d = _i18n()
    en, tr = d["en"], d["tr"]
    assert set(en) == set(tr), set(en) ^ set(tr)
    src = page()
    used = set(re.findall(r'data-i18n="([^"]+)"', src))
    used |= set(re.findall(r'\bt\("([a-z0-9_+]+)"(?!\s*\+)', src))   # "ice_h_" + preset gibi önekler hariç
    used |= {"si_1", "si_2", "si_3", "si_4", "sj_1", "sj_2", "sj_3"}
    missing = sorted(k for k in used if k not in en)
    assert not missing, missing
    # STUN önayarları ve ipuçları gösterimde çevrilir (ICE_PRESETS metinleri sabit kalır)
    presets = re.search(r"const ICE_PRESETS = \{([\s\S]*?)\};", src).group(1)
    for key in re.findall(r'"([a-z+]+)":', presets):
        assert "ice_p_" + key in tr and "ice_h_" + key in tr, key


def test_step_flow_and_verification_markup():
    src = page()
    for el_id in ("pane-start", "pane-answer", "mode", "btn-offer", "offer-box", "offer-code", "offer-qr",
                  "answer-in", "btn-connect", "offer-in", "btn-answer", "answer-ident", "answer-box",
                  "answer-code", "answer-qr", "start-status", "answer-status", "tab-start", "tab-answer",
                  "steps", "btn-sent-offer", "btn-sent-answer", "offer-expiry", "slow-hint",
                  "verify-dlg", "sn-digits", "btn-mark-verified", "strict-mode", "call-sn",
                  "peer-badge", "call-badge"):
        assert f'id="{el_id}"' in src, el_id
    assert src.count("data-again") >= 2                  # Sıfırla / Yeni Kod iki panelde de
    assert len(re.findall(r'class="step" data-role="inviter"', src)) == 4
    assert len(re.findall(r'class="step" data-role="joiner"', src)) == 3
    assert "SLOW_CONNECT_MS = 20000" in src
    assert "MAX_CODE_AGE_SEC" in src and "flow.expiresAt" in src
    # Doğrulama durumu "hatırla" deposuna geriye uyumlu yazılır/okunur
    assert "verifiedFp" in src and 'kv("put", "settings"' in src and 'kv("get", "settings")' in src


def _assert_loader(css, markup):
    for cls in ("cl-track", "cl-fill", "cl-key", "cl-dot"):
        assert cls in markup, cls
    assert 'id="tpl-chain-loader"' in markup
    assert "stroke-dashoffset" in css and "offset-path" in css and "@keyframes cl-turn" in css
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\s*\}\n", css)
    assert rm and "cl-pulse" in rm.group(1) and "animation: none" in rm.group(1)


def test_serverless_loader_markup_and_reduced_motion():
    src = page()
    _assert_loader(src, src)
    assert src.count("data-loader") >= 6                 # anahtar, kod, yanıt, bekleme, bağlanma, QR, arama
    assert "<progress" not in src


def test_web_client_uses_chain_loader():
    html, css, app = (p.read_text(encoding="utf-8") for p in (INDEX, STYLES, APP_JS))
    _assert_loader(css, html)
    for el_id in ("loading-keys-section", "p2p-caller-loading", "p2p-callee-loading"):
        block = re.search(rf'id="{el_id}"[\s\S]*?</div>', html).group(0)
        assert "data-loader" in block and "spinner" not in block, el_id
    assert "fillChainLoader" in app and 'chainLoader("sm")' in app
    assert '<div class="spinner"></div> Signing In' not in app
    assert 'hybridp2p-shell-v8' in SW.read_text(encoding="utf-8")
