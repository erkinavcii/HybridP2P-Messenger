"""Masaüstü girişi: sunucuya ulaşılamazsa anlaşılır, yerelleştirilmiş hata (pencere gerekmez)."""

import socket

import pytest

from desktop import net_config, theme
from desktop.rest_client import RestClientMixin, ServerUnreachable, login_error_text


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def restore_net():
    saved = (net_config.BASE_URL, net_config.TLS_VERIFY)
    yield
    net_config.BASE_URL, net_config.TLS_VERIFY = saved


def test_register_against_dead_server_raises_unreachable(restore_net):
    from crypto_utils import generate_rsa_keypair
    priv, pub = generate_rsa_keypair(2048)
    net_config.BASE_URL = f"http://127.0.0.1:{_closed_port()}"
    with pytest.raises(ServerUnreachable):
        RestClientMixin().register_with_server("alice", pub, priv)


@pytest.mark.parametrize("lang,needle", [("tr", "Sunucuya ulaşılamadı"), ("en", "Server unreachable")])
def test_unreachable_message_localized(lang, needle):
    old = theme.get_lang()
    theme.set_lang(lang)
    try:
        msg = login_error_text(ServerUnreachable("x"), "10.0.0.5:8000")
    finally:
        theme.set_lang(old)
    assert needle in msg and "10.0.0.5:8000" in msg


def test_other_errors_keep_generic_text():
    assert "boom" in login_error_text(Exception("boom"), "x")
