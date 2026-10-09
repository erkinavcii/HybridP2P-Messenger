"""Zayıf bağlantıda otomatik kalite düşürme (desktop/call_quality.py) ve kameranın
gönderdiği görüntüyü küçültmesi (desktop/voip_tracks.CameraTrack)."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from desktop import call_quality as q

BAD, GOOD, MID = (0.20, 0.10), (0.0, 0.05), (0.05, 0.30)   # (kayıp, rtt)

# (örnek dizisi, beklenen basamaklar) — tests/test_serverless_html.py JS ile de çalıştırır
SEQUENCES = [
    ([BAD], [0]),                                         # tek kötü örnek yetmez
    ([BAD, BAD], [0, 1]),                                 # iki kötü → bir basamak
    ([BAD] * 10, [0, 1, 1, 2, 2, 3, 3, 3, 3, 3]),         # en düşük basamakta durur
    ([BAD, MID, BAD], [0, 0, 0]),                         # ara örnek sayacı sıfırlar
    ([BAD, BAD] + [GOOD] * 5, [0, 1, 1, 1, 1, 1, 0]),     # 5 iyi örnekle geri çıkar
    ([BAD, BAD] + [GOOD] * 4 + [BAD], [0, 1, 1, 1, 1, 1, 1]),
    ([GOOD] * 6, [0] * 6),                                # en iyide yukarı çıkmaz
    ([(None, None)] * 3, [0, 0, 0]),                      # ölçüm yoksa iyi sayılır
    ([(0.09, None), (None, 0.41)], [0, 1]),               # eşikler: kayıp > %8, RTT > 400 ms
    ([(0.08, 0.40)] * 3, [0, 0, 0]),                      # eşiğin kendisi kötü değil
]


@pytest.mark.parametrize("samples,levels", SEQUENCES)
def test_policy_sequences(samples, levels):
    p = q.QualityPolicy()
    got = []
    for loss, rtt in samples:
        p.step(loss, rtt)
        got.append(p.level)
    assert got == levels


def test_bandwidth_limited_counts_as_bad():
    p = q.QualityPolicy()
    assert not p.step(0.0, 0.01, limited=True)
    assert p.step(0.0, 0.01, limited=True) and p.level == 1


def test_stats_sample_prefers_video_and_scales_rtcp_loss():
    def rep(kind, lost, rtt):
        return SimpleNamespace(type="remote-inbound-rtp", kind=kind, fractionLost=lost, roundTripTime=rtt)
    report = {"a": rep("audio", 0, 0.05), "v": rep("video", 64, 0.3),
              "o": SimpleNamespace(type="outbound-rtp", kind="video")}
    assert q.stats_sample(report) == (0.25, 0.3)          # RTCP fraction_lost 64/256
    assert q.stats_sample({"a": rep("audio", 0, 0.05)}) == (0.0, 0.05)
    assert q.stats_sample({}) == (None, None)


class _FakeCap:
    def __init__(self, *a):
        pass

    def set(self, *a):
        return True

    def read(self):
        return True, np.full((480, 640, 3), 120, np.uint8)

    def release(self):
        pass


def test_camera_scales_frames_and_keeps_preview(monkeypatch):
    from desktop import voip_tracks
    monkeypatch.setattr(voip_tracks.cv2, "VideoCapture", _FakeCap)

    async def run():
        cam = voip_tracks.CameraTrack()
        f1 = await cam.recv()
        cam.set_quality(320, 240, 10)
        f2 = await cam.recv()
        cam.stop()
        return cam, f1, f2

    cam, f1, f2 = asyncio.run(run())
    assert (f1.width, f1.height) == (640, 480) and (f2.width, f2.height) == (320, 240)
    assert f2.pts > f1.pts and cam.fps_interval == pytest.approx(0.1)
    assert cam.last_image.shape == (240, 320, 3)           # önizleme gönderilen kareyi gösterir


def test_monitor_lowers_camera_on_bad_link(monkeypatch):
    lossy = {"v": SimpleNamespace(type="remote-inbound-rtp", kind="video", fractionLost=80, roundTripTime=0.5)}

    class FakePc:
        connectionState = "connected"
        calls = 0

        async def getStats(self):
            FakePc.calls += 1
            if FakePc.calls >= 4:
                self.connectionState = "closed"
            return lossy

    cam = SimpleNamespace(running=True, sizes=[])
    cam.set_quality = lambda w, h, fps: cam.sizes.append((w, h, fps))
    levels = []
    asyncio.run(q.monitor(FakePc(), cam, levels.append, interval=0))
    assert levels == [1, 2] and cam.sizes == [q.DESKTOP_LEVELS[1], q.DESKTOP_LEVELS[2]]


def test_microphone_frames_have_valid_timing(monkeypatch):
    """PyAV 16'da av.Fraction yok: zaman tabanı fractions.Fraction olmalı (eskiden ilk karede çöküyordu)."""
    from desktop import voip_tracks

    class FakeStream:
        def __init__(self, **kw):
            self.cb = kw["callback"]

        def start(self):
            self.cb(np.zeros((960, 1), np.int16), 960, None, None)

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(voip_tracks.sd, "InputStream", FakeStream)

    async def run():
        mic = voip_tracks.MicrophoneTrack()
        frame = await asyncio.wait_for(mic.recv(), 2)
        mic.stop()
        return frame

    frame = asyncio.run(run())
    assert frame.sample_rate == 48000 and frame.samples == 960 and float(frame.time_base) == 1 / 48000


def test_player_plays_decoded_opus_stereo(monkeypatch):
    """aiortc Opus'u stereo çözer; çalıcı bunu mono'ya çevirip duyulur şekilde çalmalı
    (eskiden boyut uyuşmazlığı yüzünden hep sessizlik çalınıyordu)."""
    import fractions
    from av import AudioFrame
    from aiortc.codecs.opus import OpusDecoder, OpusEncoder
    from aiortc.jitterbuffer import JitterFrame
    from desktop import voip_tracks

    enc, dec = OpusEncoder(), OpusDecoder()
    decoded = []
    for i in range(6):
        t = np.arange(i * 960, (i + 1) * 960)
        pcm = (np.sin(2 * np.pi * 440 * t / 48000) * 8000).astype(np.int16).reshape(1, -1)
        f = AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        f.sample_rate, f.pts, f.time_base = 48000, i * 960, fractions.Fraction(1, 48000)
        for payload in enc.encode(f)[0]:
            decoded += dec.decode(JitterFrame(data=payload, timestamp=i * 960))
    assert decoded and decoded[-1].layout.name == "stereo"
    mono = voip_tracks.to_mono_s16(decoded[-1])
    assert mono.shape == (960,) and np.abs(mono).max() > 1000

    out = {}

    class FakeOut:
        def __init__(self, **kw):
            out["cb"] = kw["callback"]

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(voip_tracks.sd, "OutputStream", FakeOut)

    async def run():
        player = voip_tracks.AudioPlayer(track=None)
        player.feed(mono)
        buf = np.zeros((960, 1), np.int16)
        out["cb"](buf, 960, None, None)
        empty = np.ones((960, 1), np.int16)
        out["cb"](empty, 960, None, None)                 # tampon boşsa sessizlik
        player.stop()
        return buf, empty

    played, silent = asyncio.run(run())
    assert np.array_equal(played[:, 0], mono) and not silent.any()
