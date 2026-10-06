"""Kullanıcı adı kuralı: ^[a-z0-9_]{2,32}$ — sunucu ve masaüstü aynı kuralı uygulamalı.

Not: /api/register dakikada 20 istekle sınırlı; vaka sayısı bilerek az tutuldu.
"""

import uuid

import pytest

from conftest import register

VALID = ["ab", "a_b_9", "x" * 32]
INVALID = [
    "a",                                   # çok kısa
    "x" * 33,                              # çok uzun
    "Bob",                                 # büyük harf
    "bo b",                                # boşluk
    "../etc",                              # yol geçişi
    "ömer",                                # ASCII dışı
    "-----begin private key-----miiev",    # yapıştırılmış PEM (gerçek olay)
]


@pytest.mark.parametrize("name", INVALID)
def test_server_rejects_invalid_username(server, keypair_pool, name):
    priv, pub = keypair_pool[0]
    assert register(server, name, priv, pub).status_code == 400


def test_server_accepts_valid_usernames(server, keypair_pool):
    priv, pub = keypair_pool[0]
    for base in VALID:
        # Benzersiz ama kurala uyan ad (çakışma olmasın diye son ek, uzunluk korunur)
        suffix = uuid.uuid4().hex[:4]
        name = (base[:-4] + suffix) if len(base) > 4 else base + suffix[:2]
        assert register(server, name, priv, pub).status_code == 200, name


def test_desktop_regex_matches_server_rule():
    from desktop.login_screen import USERNAME_RE
    for name in VALID:
        assert USERNAME_RE.fullmatch(name), name
    for name in INVALID:
        assert not USERNAME_RE.fullmatch(name), name
