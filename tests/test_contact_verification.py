"""Kişi doğrulama (güvenlik kodu) durumunun yerel rehberde saklanması — message_store.

Doğrulama, doğrulanan anahtarın parmak iziyle birlikte tutulur: anahtar değişirse
(save_contact yeni parmak iziyle çağrılırsa) doğrulama kendiliğinden düşer.
"""

import sqlite3

from crypto_utils import get_public_key_fingerprint, public_key_to_pem_string
from desktop import settings_store
from message_store import MessageStore


def _key(keypair_pool, i):
    pub = keypair_pool[i][1]
    return public_key_to_pem_string(pub), get_public_key_fingerprint(pub)


def test_old_db_without_columns_migrates(isolated_keys_dir, keypair_pool):
    pem, fp = _key(keypair_pool, 0)
    db = isolated_keys_dir / "cv_old" / "messages.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)                       # doğrulama sütunları olmayan eski şema
    conn.execute("""CREATE TABLE contacts (username TEXT PRIMARY KEY, public_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO contacts VALUES ('bob', ?, ?, '2026-01-01T00:00:00+00:00')", (pem, fp))
    conn.commit()
    conn.close()

    store = MessageStore("cv_old")
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(contacts)")}
    assert {"verified", "verified_fp", "verified_at"} <= cols
    c = store.get_contact("bob")
    assert c["verified"] == 0 and c["is_verified"] is False
    assert store.is_contact_verified("bob") is False
    assert store.mark_contact_verified("bob") is True
    assert store.is_contact_verified("bob") is True


def test_verify_key_change_resave_and_unmark(isolated_keys_dir, keypair_pool):
    pem1, fp1 = _key(keypair_pool, 0)
    pem2, fp2 = _key(keypair_pool, 1)
    store = MessageStore("cv_alice")
    store.save_contact("bob", pem1, fp1)
    assert store.is_contact_verified("bob") is False

    store.mark_contact_verified("bob")
    c = store.get_contact("bob")
    assert store.is_contact_verified("bob") and c["verified_fp"] == fp1 and c["verified_at"]
    assert [x["is_verified"] for x in store.get_all_contacts()] == [True]

    store.save_contact("bob", pem1, fp1)             # aynı anahtar yeniden → doğrulama korunur
    assert store.is_contact_verified("bob") is True

    store.save_contact("bob", pem2, fp2)             # anahtar değişti → doğrulama düşer
    c = store.get_contact("bob")
    assert store.is_contact_verified("bob") is False
    assert c["verified"] == 0 and c["verified_fp"] is None and c["is_verified"] is False

    store.mark_contact_verified("bob")
    assert store.is_contact_verified("bob") is True
    store.unmark_contact_verified("bob")
    assert store.is_contact_verified("bob") is False


def test_stale_verified_fp_is_not_trusted(isolated_keys_dir, keypair_pool):
    # Parmak izi başka bir yoldan değişmiş olsa bile eski doğrulama geçerli sayılmaz
    pem1, fp1 = _key(keypair_pool, 0)
    _, fp2 = _key(keypair_pool, 1)
    store = MessageStore("cv_carol")
    store.save_contact("bob", pem1, fp1)
    store.mark_contact_verified("bob")
    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE contacts SET fingerprint = ? WHERE username = 'bob'", (fp2,))
    conn.commit()
    conn.close()
    assert store.is_contact_verified("bob") is False


def test_unknown_contact_is_not_verified(isolated_keys_dir):
    store = MessageStore("cv_dave")
    assert store.mark_contact_verified("nobody") is False
    assert store.is_contact_verified("nobody") is False


def test_strict_mode_setting_defaults_off():
    assert settings_store.DEFAULTS["p2p_strict"] is False
