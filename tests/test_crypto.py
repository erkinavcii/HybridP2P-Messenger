"""crypto_utils birim testleri — sunucu gerektirmez."""

import os

import pytest
from cryptography.exceptions import InvalidTag

from crypto_utils import (
    decrypt_bytes,
    decrypt_message,
    decrypt_symmetric,
    encrypt_bytes,
    encrypt_message,
    encrypt_symmetric,
    get_public_key_fingerprint,
    pem_string_to_public_key,
    public_key_to_pem_string,
    sign_data,
    verify_signature,
)


@pytest.fixture(scope="module")
def alice(keypair_pool):
    return keypair_pool[0]


@pytest.fixture(scope="module")
def bob(keypair_pool):
    return keypair_pool[1]


@pytest.mark.parametrize("text", ["merhaba", "", "çğıöşü ÇĞİÖŞÜ 🔐", "x" * 50_000],
                         ids=["ascii", "bos", "turkce-emoji", "50k"])
def test_hybrid_message_roundtrip(alice, text):
    priv, pub = alice
    assert decrypt_message(encrypt_message(text, pub), priv) == text


def test_each_encryption_is_unique(alice):
    """Aynı metin iki kez şifrelenince farklı paket çıkmalı (taze AES anahtarı + nonce)."""
    _, pub = alice
    assert encrypt_message("aynı", pub) != encrypt_message("aynı", pub)


def test_wrong_private_key_cannot_decrypt(alice, bob):
    _, alice_pub = alice
    bob_priv, _ = bob
    with pytest.raises(Exception):
        decrypt_message(encrypt_message("gizli", alice_pub), bob_priv)


def test_bytes_roundtrip(alice):
    priv, pub = alice
    data = os.urandom(200_000)
    assert decrypt_bytes(encrypt_bytes(data, pub), priv) == data


def test_symmetric_roundtrip_and_tamper_detection():
    key = os.urandom(32)
    packet = encrypt_symmetric("grup mesajı", key)
    assert decrypt_symmetric(packet, key) == "grup mesajı"
    with pytest.raises(InvalidTag):
        decrypt_symmetric(packet, os.urandom(32))


def test_signature_valid_and_tampered(alice, bob):
    priv, pub = alice
    _, other_pub = bob
    data = b"bob:alice:payload"
    sig = sign_data(priv, data)
    assert verify_signature(pub, sig, data) is True
    assert verify_signature(pub, sig, b"bob:alice:PAYLOAD") is False
    assert verify_signature(other_pub, sig, data) is False


def test_pem_roundtrip_and_fingerprint_stable(alice):
    _, pub = alice
    restored = pem_string_to_public_key(public_key_to_pem_string(pub))
    fp = get_public_key_fingerprint(pub)
    assert fp == get_public_key_fingerprint(restored)
    assert len(fp.replace(" ", "")) == 64  # SHA-256 hex
