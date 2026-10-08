"""Sunucusuz başlatma (S2): kişi kartı doğrulama ve başlatmanın HİÇ ağ isteği yapmaması."""

import json
import socket
import threading
import types

import pytest

from crypto_utils import get_public_key_fingerprint, public_key_to_pem_string
from desktop import serverless_screen as ss


@pytest.fixture
def card(keypair_pool):
    _, pub = keypair_pool[0]
    return {"username": "bob", "public_key": public_key_to_pem_string(pub),
            "fingerprint": get_public_key_fingerprint(pub)}


def test_valid_card(card):
    user, pem, fp = ss.parse_contact_card(json.dumps(card), own_username="alice")
    assert (user, fp) == ("bob", card["fingerprint"]) and "BEGIN PUBLIC KEY" in pem


def test_card_without_fingerprint_is_accepted(card):
    card.pop("fingerprint")
    assert ss.parse_contact_card(json.dumps(card))[0] == "bob"


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c.update(fingerprint="AAAA BBBB"), "uyuşmuyor"),
    (lambda c: c.update(username="Bob Smith"), "kullanıcı adı"),
    (lambda c: c.update(public_key="bozuk"), "açık anahtar"),
    (lambda c: c.update(username="alice"), "Kendi kartınızı"),
])
def test_bad_cards(card, mutate, msg):
    mutate(card)
    with pytest.raises(ValueError, match=msg):
        ss.parse_contact_card(json.dumps(card), own_username="alice")


@pytest.mark.parametrize("bad", ["", "not json", "[]", "null"])
def test_garbage_cards(bad):
    with pytest.raises(ValueError):
        ss.parse_contact_card(bad)


def test_own_card_roundtrip(keypair_pool):
    _, pub = keypair_pool[1]
    text = ss.own_contact_card("carol", pub)
    assert ss.parse_contact_card(text)[0] == "carol"


def test_serverless_start_makes_no_network_calls(isolated_keys_dir, monkeypatch, keypair_pool):
    """Sunucu engelliyken de açılabilmeli: başlatma sırasında tek bir soket bile açılmamalı."""
    import crypto_utils
    import message_store

    attempts = []

    def no_network(*a, **kw):
        attempts.append(a)
        raise OSError("ağ yasak (test)")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(crypto_utils, "KEYS_DIR", isolated_keys_dir)
    priv, pub = keypair_pool[2]
    crypto_utils.save_keys_to_disk("alice", priv, pub)

    from desktop.rest_client import RestClientMixin

    class App(ss.ServerlessScreenMixin, RestClientMixin):
        pass

    app = App()
    done = threading.Event()
    app.state = {}
    app.username_field = types.SimpleNamespace(value="alice", error_text=None)
    app.serverless_btn = types.SimpleNamespace(disabled=False)
    app.import_key_checkbox = types.SimpleNamespace(value=False)
    app.import_key_field = types.SimpleNamespace(value="")
    app.page = types.SimpleNamespace(update=lambda: None)
    app.log_status = lambda msg: None
    app.run_on_ui = lambda fn: fn()
    app.show_serverless_screen = done.set
    app.on_serverless_start_click(None)
    assert done.wait(10), app.username_field.error_text

    assert attempts == []
    assert app.state["serverless"] is True and app.state["logged_in"] is False
    assert app.state["username"] == "alice"
    assert get_public_key_fingerprint(app.state["public_key"]) == get_public_key_fingerprint(pub)
    assert isinstance(app.state["store"], message_store.MessageStore)
