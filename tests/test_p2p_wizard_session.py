"""Sunucusuz sihirbaz: yeni davet/yanıt üretilince önceki yarım oturum serbest kalmalı.

Hata: "1. Davet Kodu Oluştur" başarıdan sonra yeniden etkinleşince her tıklama yeni
bir RTCPeerConnection açıyordu; eskisi kapatılmıyor, sesli/görüntülü modda mikrofon,
kamera ve hoparlör açık kalıyordu. Ayrıca eski pc kapanırken tetiklenen 'closed'
ICE olayı cleanup_call'u çağırıp sihirbazı kapatmamalı.

Kamera/mikrofon gerekmez: izler ve pc sahte nesnelerdir (son test gerçek aiortc pc'si
kullanır ama medya açmaz).
"""

import asyncio

import pytest

from desktop import pure_p2p


class FakeTrack:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakePC:
    created = []

    def __init__(self, configuration=None):
        self.closed = False
        self.listeners_removed = False
        self.closed_with_listeners = None
        FakePC.created.append(self)

    def remove_all_listeners(self):
        self.listeners_removed = True

    async def close(self):
        self.closed_with_listeners = not self.listeners_removed
        self.closed = True


class App(pure_p2p.PureP2PMixin):
    def __init__(self):
        self.cleanups = 0
        self.state = {
            "active_pc": None,
            "call_loop": None,
            "call_state": None,
            "local_audio_track": None,
            "local_video_track": None,
            "audio_player": None,
            "quality_pc": None,
        }

    def cleanup_call(self):          # ekran değiştiren tam temizlik — burada ÇAĞRILMAMALI
        self.cleanups += 1


def _with_running_session(app):
    old_pc = FakePC()
    mic, cam, speaker = FakeTrack(), FakeTrack(), FakeTrack()
    app.state.update({
        "active_pc": old_pc,
        "local_audio_track": mic,
        "local_video_track": cam,
        "audio_player": speaker,
        "quality_pc": old_pc,
        "call_state": "ended",
    })
    return old_pc, mic, cam, speaker


def test_release_session_stops_media_and_closes_pc():
    app = App()
    old_pc, mic, cam, speaker = _with_running_session(app)

    asyncio.run(app._p2p_release_session())

    assert mic.stopped and cam.stopped and speaker.stopped
    assert old_pc.closed and old_pc.listeners_removed
    assert old_pc.closed_with_listeners is False      # dinleyiciler close()'dan ÖNCE sökülür
    for key in ("active_pc", "local_audio_track", "local_video_track", "audio_player", "quality_pc"):
        assert app.state[key] is None, key
    assert app.cleanups == 0                           # ekran değişmez, sihirbaz açık kalır


def test_release_session_without_previous_session_is_noop():
    app = App()
    asyncio.run(app._p2p_release_session())
    assert app.state["active_pc"] is None and app.cleanups == 0


def test_new_pc_tears_down_previous_session(monkeypatch):
    monkeypatch.setattr(pure_p2p, "RTCPeerConnection", FakePC)
    monkeypatch.setattr(pure_p2p, "_ice_config", lambda: None)
    app = App()
    old_pc, mic, cam, speaker = _with_running_session(app)

    async def scenario():
        first = await app._p2p_new_pc("caller", "video")
        second = await app._p2p_new_pc("caller", "chat")
        return first, second

    first, second = asyncio.run(scenario())

    assert old_pc.closed and mic.stopped and cam.stopped and speaker.stopped
    assert first.closed and not second.closed          # her yeni oturum bir öncekini kapatır
    assert app.state["active_pc"] is second
    assert app.state["call_type"] == "chat" and app.state["call_role"] == "caller"
    assert app.state["call_state"] is None             # eski "ended" yeni önizlemeyi durdurmasın
    assert app.cleanups == 0


def test_closing_old_real_pc_does_not_trigger_cleanup_call():
    """Gerçek aiortc pc: close() ICE durumunu 'closed' yapar; _p2p_watch_call bunu
    dinliyorsa cleanup_call tetiklenir ve sihirbaz kapanırdı. Serbest bırakma önce
    dinleyicileri söktüğü için tetiklenmemeli."""
    from aiortc import RTCPeerConnection

    app = App()

    async def scenario():
        pc = RTCPeerConnection()
        pc.createDataChannel("hp2p")
        await pc.setLocalDescription(await pc.createOffer())
        app._p2p_watch_call(pc, dialog=None)
        app.state["active_pc"] = pc
        await app._p2p_release_session()
        await asyncio.sleep(0.05)
        return pc

    pc = asyncio.run(scenario())
    assert pc.iceConnectionState == "closed"
    assert app.cleanups == 0
    assert app.state["active_pc"] is None


@pytest.mark.parametrize("key", list(pure_p2p.p2p_core.ICE_PRESETS))
def test_ice_preset_labels_translated_at_display_time(key):
    """p2p_core.ICE_PRESETS (serverless.html ile birebir) değişmez; çeviri gösterimde yapılır."""
    from desktop import theme

    try:
        theme.set_lang("en")
        assert pure_p2p.ice_preset_label(key) == pure_p2p.p2p_core.ICE_PRESETS[key]
        theme.set_lang("tr")
        assert pure_p2p.ice_preset_label(key) == theme.ARB["tr"][f"ice_preset_{key}"]
        assert pure_p2p.ice_preset_hint(key)
    finally:
        theme.set_lang("en")
