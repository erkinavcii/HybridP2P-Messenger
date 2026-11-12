"""K4: web istemcisinde uzaktan gelen metnin HTML'e kaçışsız basılmaması.

safe.js Node'da gerçekten çalıştırılır; ui.js için metin üzerinden statik denetim yapılır.
"""

import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

UI = (ROOT / "static" / "js" / "ui.js").read_text(encoding="utf-8")
HARNESS = ROOT / "tests" / "node" / "xss_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js yok")


def run(*cmds):
    r = subprocess.run(["node", str(HARNESS)], input=json.dumps(cmds), capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_escape_html_neutralizes_markup():
    out = run(["escapeHtml", "<img src=x onerror=alert(1)>"], ["escapeHtml", 'x."onerror="alert(1)'])
    assert "<" not in out[0] and ">" not in out[0]
    assert out[0] == "&lt;img src=x onerror=alert(1)&gt;"
    assert '"' not in out[1]


@needs_node
def test_image_mime_allowlist():
    names = ["a.PNG", "b.jpeg", "c.jpg", "d.gif", "e.webp", "noext", 'x."onerror="alert(1)', "evil.svg", "evil.SVG"]
    out = run(*[["imageMime", n] for n in names])
    assert out == ["image/png", "image/jpeg", "image/jpeg", "image/gif", "image/webp",
                   "image/png", "image/png", "image/png", "image/png"]
    assert all(m in {"image/png", "image/jpeg", "image/gif", "image/webp"} for m in out)


@needs_node
def test_js_arg_is_safe_inside_onclick_attribute():
    (v,) = run(["jsArg", "');alert(1);//\""])
    assert "'" not in v and '"' not in v.replace("&quot;", "")


def test_ui_has_no_unescaped_snippet_or_sender():
    assert not re.search(r"\$\{snippet", UI)
    assert ">${item.partner}<" not in UI
    assert ">${m.sender}<" not in UI


def test_ui_has_no_raw_extension_data_url():
    assert "data:image/${ext}" not in UI
    assert UI.count("imageMime(m.original_name)") == 2


def test_append_system_message_uses_text_content():
    body = UI[UI.index("export function appendSystemMessage"):]
    body = body[:body.index("\n}\n")]
    assert "innerHTML" not in body
    assert "textContent" in body
