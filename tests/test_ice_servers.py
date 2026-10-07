"""Aramalar için ICE (STUN/TURN) yapılandırması — server/routes/voip.py.

Google STUN açılıp kapanabilir; kendi coturn'ümüz varsa önce o gelir; TURN
kimliği coturn'ün "use-auth-secret" şemasıyla üretilir ve kısa ömürlüdür.
"""

import base64
import hashlib
import hmac
import time

import pytest
import requests

from server import config
from server.routes.voip import TURN_CREDENTIAL_TTL, build_ice_servers


@pytest.fixture
def cfg(monkeypatch):
    def apply(**values):
        defaults = dict(PUBLIC_STUN=True, PUBLIC_STUN_URLS=["stun:a.example:19302", "stun:b.example:19302"],
                        TURN_HOST="", TURN_PORT=3478, TURN_TLS_PORT="", TURN_USERNAME="",
                        TURN_CREDENTIAL="", TURN_SECRET="")
        defaults.update(values)
        for k, v in defaults.items():
            monkeypatch.setattr(config, k, v)
    return apply


def _urls(servers):
    out = []
    for s in servers:
        out += s["urls"] if isinstance(s["urls"], list) else [s["urls"]]
    return out


def test_default_is_public_stun_only(cfg):
    cfg()
    assert build_ice_servers("alice") == [{"urls": "stun:a.example:19302"}, {"urls": "stun:b.example:19302"}]


def test_public_stun_off_and_no_turn_contacts_nobody(cfg):
    cfg(PUBLIC_STUN=False)
    assert build_ice_servers("alice") == []


def test_own_turn_comes_first_with_valid_short_lived_credential(cfg):
    cfg(TURN_HOST="203.0.113.5", TURN_SECRET="s3cret", PUBLIC_STUN=False)
    servers = build_ice_servers("alice")
    assert servers[0] == {"urls": "stun:203.0.113.5:3478"}       # coturn kendi STUN'u
    turn = servers[1]
    assert turn["urls"] == ["turn:203.0.113.5:3478?transport=udp", "turn:203.0.113.5:3478?transport=tcp"]
    expiry, user = turn["username"].split(":", 1)
    assert user == "alice"
    assert 0 < int(expiry) - time.time() <= TURN_CREDENTIAL_TTL
    # coturn'ün doğrulayacağı parola: base64(HMAC-SHA1(secret, username))
    expected = base64.b64encode(hmac.new(b"s3cret", turn["username"].encode(), hashlib.sha1).digest()).decode()
    assert turn["credential"] == expected
    assert not any("google" in u for u in _urls(servers))


def test_turns_only_when_tls_port_configured(cfg):
    cfg(TURN_HOST="turn.example.com", TURN_SECRET="x")
    assert not any(u.startswith("turns:") for u in _urls(build_ice_servers("a")))
    cfg(TURN_HOST="turn.example.com", TURN_SECRET="x", TURN_TLS_PORT="5349")
    assert "turns:turn.example.com:5349" in _urls(build_ice_servers("a"))


def test_turn_with_static_credentials_and_public_stun_kept(cfg):
    cfg(TURN_HOST="turn.example.com", TURN_USERNAME="u", TURN_CREDENTIAL="p")
    servers = build_ice_servers("a")
    assert (servers[1]["username"], servers[1]["credential"]) == ("u", "p")
    assert _urls(servers)[-2:] == ["stun:a.example:19302", "stun:b.example:19302"]


def test_ipv6_turn_host_is_bracketed(cfg):
    cfg(TURN_HOST="2001:db8::7", TURN_SECRET="x", PUBLIC_STUN=False)
    assert build_ice_servers("a")[0] == {"urls": "stun:[2001:db8::7]:3478"}


def test_endpoint_requires_signature(server, make_user):
    # Web istemcisi bu isteği eskiden imzasız atıyordu → 401 → aramalarda STUN yoktu
    assert requests.get(f"{server.base}/api/ice_servers", timeout=5).status_code in (401, 422)
    r = make_user("ice").get("/api/ice_servers")
    assert r.status_code == 200 and isinstance(r.json()["ice_servers"], list)
