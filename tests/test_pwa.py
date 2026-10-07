"""PWA: manifest, ikonlar ve service worker önbellek listesi.

Service worker'ın kurulumu, önbellek listesindeki (SHELL) TEK bir dosya bile
404 dönerse tamamen başarısız olur; yeni bir JS modülü listeye eklenmezse de
çevrimdışı açılışta eksik kalır. Bu testler ikisini de yakalar.
"""

import json
import re

import requests
from PIL import Image

from conftest import ROOT

STATIC = ROOT / "static"


def _sw_list(name):
    src = (STATIC / "sw.js").read_text(encoding="utf-8")
    block = re.search(rf"const {name} = \[(.*?)\];", src, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def test_manifest_and_icons():
    man = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["start_url"] == "/" and man["display"] == "standalone"
    purposes = set()
    for icon in man["icons"]:
        path = STATIC / icon["src"].lstrip("/")
        w, h = Image.open(path).size
        assert f"{w}x{h}" == icon["sizes"]
        purposes.add(icon["purpose"])
    assert {"any", "maskable"} <= purposes
    assert 'rel="manifest"' in (STATIC / "index.html").read_text(encoding="utf-8")


def test_shell_covers_every_web_module():
    shell = set(_sw_list("SHELL"))
    for f in list((STATIC / "js").glob("*.js")) + list((STATIC / "css").glob("*.css")):
        assert "/" + f.relative_to(STATIC).as_posix() in shell, f"{f.name} sw.js SHELL listesinde yok"


def test_shell_paths_are_served(server):
    for path in _sw_list("SHELL"):
        r = requests.get(server.base + path, timeout=5)
        assert r.status_code == 200, path
    assert requests.get(server.base + "/manifest.webmanifest", timeout=5).headers["content-type"].startswith(
        "application/manifest+json")


def test_live_endpoints_never_cached():
    never = _sw_list("NEVER_CACHE")
    assert {"/api/", "/ws/", "/health"} <= set(never)
    assert not any(p.startswith(tuple(never)) for p in _sw_list("SHELL"))
