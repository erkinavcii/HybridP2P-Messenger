"""Sesli mesaj: kodlama, yerel saklama kuralları ve sunucu iletimi."""

import json
import uuid

import numpy as np
import pytest

from conftest import recv_until, run
from desktop import voice


def _tone(seconds=2.0, freq=440):
    t = np.arange(int(voice.SAMPLE_RATE * seconds)) / voice.SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * 8000).astype(np.int16)


# ── Kodlama ─────────────────────────────────────────────────────────

def test_opus_roundtrip_preserves_duration_and_is_small():
    pcm = _tone(3.0)
    data = voice.encode_opus(pcm)
    assert data[:4] == b"OggS"
    assert len(data) < len(pcm) * 2 / 10, "Opus WAV'dan en az 10 kat küçük olmalı"
    assert abs(voice.duration_of(data) - 3.0) < 0.05


def test_decoded_audio_is_not_silence():
    pcm, sr = voice.decode_audio(voice.encode_opus(_tone(1.0)))
    assert sr == voice.SAMPLE_RATE
    assert np.abs(pcm.astype(np.int32)).mean() > 1000


@pytest.mark.parametrize("name,ftype,expected", [
    ("voice-1712345678901.ogg", "audio", True),
    ("voice-1.ogg", "document", False),     # tip audio değil
    ("şarkı.ogg", "audio", False),          # sıradan ses dosyası
    ("voice-1.mp3", "audio", False),
])
def test_is_voice_file(name, ftype, expected):
    assert voice.is_voice_file(name, ftype) is expected


def test_fmt_duration():
    assert voice.fmt_duration(0) == "0:00"
    assert voice.fmt_duration(65.4) == "1:05"


# ── Yerel saklama ───────────────────────────────────────────────────

def test_media_path_traversal_rejected(isolated_keys_dir):
    assert voice.load_media("alice", "../bob/private_key.pem") is None
    assert voice.load_media("alice", "..\\x") is None
    name = voice.save_media("alice", "abc", b"OggS-test")
    assert voice.load_media("alice", name) == b"OggS-test"


class _Chat:
    def __init__(self, username):
        from desktop.chat_logic import ChatLogicMixin
        from message_store import MessageStore

        class _Impl(ChatLogicMixin):
            pass
        self.impl = _Impl()
        self.impl.state = {"username": username, "store": MessageStore(username), "recipient": None}


def test_store_voice_persists_and_shows_in_inbox(isolated_keys_dir):
    c = _Chat("alice")
    data = voice.encode_opus(_tone(1.0))
    name = c.impl.store_voice("bob", "bob", False, "2026-10-06T10:00:00+00:00", "f1", data, 1.0)
    assert name and (isolated_keys_dir / "alice" / "media" / name).read_bytes() == data

    store = c.impl.state["store"]
    msgs = store.get_messages("bob")
    assert msgs[-1]["msg_type"] == "voice"
    assert json.loads(msgs[-1]["content"]) == {"voice": name, "duration": 1.0}

    chat = next(ch for ch in store.get_all_chats() if ch["partner"] == "bob")
    assert chat["last_message"] == "🎤 Sesli mesaj"   # ham JSON değil
    assert chat["unread_count"] == 1                   # sohbet açık değildi

    res = store.search_chats_and_messages("voice")
    assert res["messages"] == [], "sesli mesaj JSON'u arama sonuçlarına sızmamalı"


def test_ephemeral_chat_writes_nothing_to_disk(isolated_keys_dir):
    c = _Chat("alice")
    c.impl.state["store"].set_ephemeral("bob", True, "alice")
    name = c.impl.store_voice("bob", "bob", False, "2026-10-06T10:00:00+00:00", "f2", b"OggS", 1.0)
    assert name is None
    assert not (isolated_keys_dir / "alice" / "media").exists() or \
        not any((isolated_keys_dir / "alice" / "media").iterdir())
    assert c.impl.state["store"].get_messages("bob") == []


# ── Sunucu iletimi (sunucu değişikliği yok — mevcut dosya yolu) ─────

def test_voice_file_relayed_as_regular_audio_file(make_user):
    from crypto_utils import decrypt_bytes, encrypt_bytes
    alice, bob = make_user("va"), make_user("vb")
    data = voice.encode_opus(_tone(1.5))
    name = voice.voice_filename()

    r = bob.post("/api/upload_file", {"sender": bob.name, "recipient": alice.name,
                                      "encrypted_data": encrypt_bytes(data, alice.pub),
                                      "original_name": name, "file_type": "audio"})
    assert r.status_code == 200
    file_uuid = r.json()["uuid"]

    async def go():
        ws_a = await alice.connect()
        ws_b = await bob.connect()
        try:
            await ws_b.send(json.dumps({"type": "file_message", "recipient": alice.name,
                                        "file_uuid": file_uuid, "original_name": name,
                                        "file_type": "audio", "view_once": False}))
            return await recv_until(ws_a, "file_message", match=lambda d: d["file_uuid"] == file_uuid)
        finally:
            await ws_a.close(); await ws_b.close()
    frame = run(go())
    assert voice.is_voice_file(frame["original_name"], frame["file_type"])

    dl = alice.get(f"/api/download_file/{file_uuid}")
    assert decrypt_bytes(dl.json()["encrypted_data"], alice.priv) == data
    # Sunucu ilk indirmede siler — istemcinin hemen yerelde saklamasının sebebi bu
    assert alice.get(f"/api/download_file/{file_uuid}").status_code == 404
