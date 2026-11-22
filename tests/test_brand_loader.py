"""tests/test_brand_loader.py — Zincir yükleyici (L1): saf animasyon matematiği,
başsız (headless) kurulum, başlat/durdur yaşam döngüsü ve hafif giriş noktası."""

import asyncio
import concurrent.futures
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from desktop import brand_loader as bl
from desktop.theme import ARB

ROOT = Path(__file__).resolve().parent.parent


# ── saf matematik ──────────────────────────────────────────────────────
def test_fill_fraction_cycles_zero_one_zero():
    assert bl.fill_fraction(0.0) == pytest.approx(0.0)
    assert bl.fill_fraction(0.5) == pytest.approx(1.0)
    assert bl.fill_fraction(1.0) == pytest.approx(0.0)
    assert bl.fill_fraction(0.25) == pytest.approx(0.5)
    # ilk yarıda monoton artar, ikinci yarıda azalır
    up = [bl.fill_fraction(i / 100) for i in range(0, 51)]
    down = [bl.fill_fraction(i / 100) for i in range(50, 101)]
    assert up == sorted(up)
    assert down == sorted(down, reverse=True)
    # periyodik
    for p in (0.1, 0.37, 0.8):
        assert bl.fill_fraction(p) == pytest.approx(bl.fill_fraction(p + 3))
    assert all(0.0 <= bl.fill_fraction(i / 37) <= 1.0 for i in range(200))


def test_arc_sweep_matches_fill_fraction():
    for i in range(101):
        p = i / 100
        start, sweep = bl.arc_for_phase(p)
        assert sweep == pytest.approx(bl.TAU * bl.fill_fraction(p), abs=1e-9)
        assert 0.0 <= sweep <= bl.TAU + 1e-9
    # büyürken başlangıç sabit, boşalırken kuyruk ileri kayar
    assert bl.arc_for_phase(0.1)[0] == pytest.approx(bl.RING_START)
    assert bl.arc_for_phase(0.75)[0] > bl.RING_START


def test_dot_interpolates_between_ring_centers():
    g = bl.ring_geometry(100)
    a, b = g["left"], g["right"]
    assert bl.lerp_point(a, b, 0) == pytest.approx(a)
    assert bl.lerp_point(a, b, 1) == pytest.approx(b)
    mid = bl.lerp_point(a, b, 0.5)
    assert mid == pytest.approx(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    # ilerleme yolda 0→1 monoton, yol dışında None
    assert bl.dot_progress(0.05) is None and bl.dot_progress(0.95) is None
    seq = [bl.dot_progress(0.2 + 0.6 * i / 100) for i in range(101)]
    assert seq[0] == pytest.approx(0.0) and seq[-1] == pytest.approx(1.0)
    assert seq == sorted(seq)


def test_right_ring_lags_left_ring():
    lag_t = bl.RIGHT_LAG * bl.FILL_PERIOD
    for t in (0.0, 0.3, 1.1, 2.0):
        assert bl.frame_state(t + lag_t)["right_arc"] == pytest.approx(bl.frame_state(t)["left_arc"])


def test_rock_angle_bounded_and_periodic():
    vals = [bl.rock_angle(i * 0.05) for i in range(200)]
    assert max(abs(v) for v in vals) <= bl.ROCK_AMPLITUDE + 1e-12
    assert bl.rock_angle(0.7) == pytest.approx(bl.rock_angle(0.7 + bl.ROCK_PERIOD))


def test_geometry_rings_overlap_inside_canvas():
    g = bl.ring_geometry(96)
    (lx, _), (rx, _) = g["left"], g["right"]
    half = g["ring_w"] / 2
    assert lx + half > rx - half                     # halkalar iç içe geçer
    assert lx - half - g["stroke"] / 2 >= 0          # tuvalden taşmaz
    assert rx + half + g["stroke"] / 2 <= g["size"]
    assert g["center"][0] == pytest.approx((lx + rx) / 2)


# ── kontrol: başsız kurulum ve yaşam döngüsü ───────────────────────────
def test_loader_constructs_headless():
    loader = bl.ChainLoader(message="Başlatılıyor…", size=96)
    assert loader.running is False
    assert loader._caption.value == "Başlatılıyor…" and loader._caption.visible
    assert loader._canvas.width == 96 and len(loader._canvas.shapes) >= 6
    silent = bl.ChainLoader(size=40, autostart=False)
    assert silent._caption.visible is False
    splash = bl.chain_splash("x")
    assert isinstance(splash.data, bl.ChainLoader)


def test_render_moves_shapes():
    loader = bl.ChainLoader(size=100)
    loader._render(0.0)
    first = (loader._left_arc.sweep_angle, loader._dot_core.x)
    loader._render(0.3 * bl.FILL_PERIOD)
    assert loader._left_arc.sweep_angle > first[0]
    assert loader._dot_core.x > first[1]
    loader.set_message("Bağlanıyor…")                 # sayfasızken de güvenli
    assert loader._caption.value == "Bağlanıyor…"


class _LoopPage:
    """run_task'ı ayrı bir olay döngüsünde çalıştıran sahte sayfa."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run_task(self, fn, *args):
        return asyncio.run_coroutine_threadsafe(fn(*args), self.loop)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)


def test_start_runs_frames_and_stop_cancels():
    page = _LoopPage()
    try:
        loader = bl.ChainLoader(size=64, fps=60, autostart=False)
        frames = []
        ticked = threading.Event()
        loader._mounted = True

        def fake_flush():
            frames.append(loader._left_arc.sweep_angle)
            if len(frames) >= 5:
                ticked.set()
            return True

        loader._flush = fake_flush
        loader.start(page=page)
        assert loader.running
        assert ticked.wait(3), "animasyon döngüsü kare üretmedi"
        fut = loader._future
        assert isinstance(fut, concurrent.futures.Future)
        loader.start(page=page)                      # ikinci start yeni görev açmaz
        assert loader._future is fut

        loader.stop()
        assert loader.running is False
        try:
            fut.result(timeout=2)
        except concurrent.futures.CancelledError:
            pass
        assert fut.done()
        n = len(frames)
        threading.Event().wait(0.15)
        assert len(frames) == n, "stop() sonrası kare gönderildi"
        loader.stop()                                # idempotent
    finally:
        page.close()


def test_loop_exits_when_unmounted():
    page = _LoopPage()
    try:
        loader = bl.ChainLoader(size=64, fps=60, autostart=False)
        loader._mounted = True
        loader._flush = lambda: loader._mounted     # sayfaya bağlıyken kare gönderiyor say
        loader.start(page=page)
        fut = loader._future
        assert fut is not None
        loader.will_unmount()                        # sayfadan çıkarılma
        assert loader.running is False
        try:
            fut.result(timeout=2)
        except concurrent.futures.CancelledError:
            pass
        del loader._flush
        assert loader._flush() is False              # kopuk kontrol güncellenmez
    finally:
        page.close()


def test_busy_overlay_state_without_page():
    calls = []

    class P:
        overlay = []

        def run_task(self, fn, *a):
            calls.append(fn.__name__)

    ov = bl.BusyOverlay(P())
    ov.show("a")
    assert ov.shown and ov.message == "a"
    ov.show("b")                                     # zaten açık: yalnız altyazı
    ov.hide()
    assert not ov.shown
    assert calls == ["_ui_show", "_ui_set_message", "_ui_hide"]


def test_busy_overlay_immediate_show_and_wait_visible():
    """Gecikmeli gösterim beklerken delay=0 hemen çizer; wait_visible bunu bekler."""
    page = _LoopPage()
    page.overlay = []
    page.update = lambda: None
    try:
        ov = bl.BusyOverlay(page)
        ov.show("Bağlanıyor…")                       # 0.25 sn gecikmeli
        t0 = time.monotonic()
        ov.show("Anahtar üretiliyor…", delay=0)      # bekleme: şimdi göster
        assert ov.wait_visible(1.0)
        assert time.monotonic() - t0 < 0.32          # gecikmeli yol 0.25+0.12 sn sürerdi
        assert len(page.overlay) == 1
        assert ov._loader._caption.value == "Anahtar üretiliyor…"
        threading.Event().wait(0.35)                 # gecikmeli kopya ikinci kaplama açmaz
        assert len(page.overlay) == 1
        ov.hide()
        assert not ov.wait_visible(0.05)
        threading.Event().wait(0.1)
        assert page.overlay == []
    finally:
        page.close()


# ── yerelleştirme ve hafif giriş noktası ──────────────────────────────
@pytest.mark.parametrize("key", ["loader_starting", "loader_keys", "loader_waiting_peer",
                                 "loader_connecting", "loader_loading"])
def test_loader_strings_localized(key):
    assert ARB["en"][key] and ARB["tr"][key] and ARB["en"][key] != ARB["tr"][key]


def test_client_entry_is_light():
    """client içe aktarılırken ağır modüller (aiortc, cv2, av, requests) yüklenmemeli."""
    code = (
        "import sys, client, serverless_client\n"
        "heavy = [m for m in ('aiortc', 'cv2', 'av', 'requests', 'desktop.app') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
        "assert callable(client.boot) and callable(serverless_client.main)\n"
        "print(client.MessengerApp.__module__)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    heavy, mod = out.stdout.rstrip("\n").split("\n")
    assert heavy == ""
    assert mod == "desktop.app"
