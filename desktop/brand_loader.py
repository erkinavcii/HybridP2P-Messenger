"""desktop/brand_loader.py — Marka zinciriyle animasyonlu yükleyici (L1).

Marka işaretini (iç içe geçmiş iki oval halka + kesişimde küçük bir anahtar
deliği) `flet.canvas` ile çizer ve canlandırır:

  • tüm işaret hafifçe sallanır (rock),
  • her halkanın çizgisi 0→360° dolar ve geri boşalır; sağ halka çeyrek tur
    geriden gelir, böylece hareket soldan sağa okunur,
  • parlayan küçük bir nokta sol halkadan sağ halkaya akar (iki cihaz arasında
    taşınan veri).

Animasyon, sayfanın olay döngüsünde (`page.run_task`) ~30 fps çalışan bir
asyncio döngüsüdür; her karede yalnızca mevcut şekillerin açı/konum alanları
değişir ve `canvas.update()` sadece bu farkı gönderir.

KULLANIM (diğer ekranlar için API)
----------------------------------
    from desktop.brand_loader import ChainLoader, chain_splash, BusyOverlay
    from desktop.theme import t

    loader = ChainLoader(message=t("loader_waiting_peer"), size=96)
    column.controls.append(loader)      # sayfaya eklenince KENDİLİĞİNDEN başlar
    page.update()
    ...
    loader.set_message(t("loader_connecting"))   # altyazıyı değiştir (UI döngüsünden)
    column.controls.remove(loader)      # sayfadan çıkınca KENDİLİĞİNDEN durur
    page.update()

  • ChainLoader(message="", size=96, fps=30, autostart=True)
      Bir `ft.Column` alt sınıfıdır; doğrudan herhangi bir kontrol ağacına
      eklenebilir. `did_mount` döngüyü başlatır, `will_unmount` durdurur.
      `autostart=False` verilirse `loader.start()` / `loader.stop()` elle
      çağrılır. `stop()` her yerden (thread dahil) güvenle çağrılabilir ve
      bekleyen görevi iptal eder; döngü sayfadan kopmuş bir kontrolü asla
      güncellemez. `visible=False` iken kare gönderilmez (CPU harcamaz).
      Renkler oluşturulduğu andaki paletten (C) okunur; tema değişince ekran
      zaten yeniden kurulduğu için yeni yükleyici yeni renkleri alır.
  • chain_splash(message="", size=120, show_splash=False) -> ft.Container
      Tema zemininde ortalanmış tam ekran yükleyici (uygulama açılışı için).
      İçindeki ChainLoader `container.data` üzerinden erişilebilir.
  • BusyOverlay(page)
      Sayfanın `overlay` katmanında tam ekran, tıklamaları yutan yükleyici.
      `show(message, delay=0.25)`, `set_message(message)`, `hide()` — her
      thread'den çağrılabilir (UI değişiklikleri `page.run_task` ile yapılır).
      `delay` içinde `hide()` gelirse hiç görünmez (kısa işlerde yanıp sönmez).
      Gecikmeli bir gösterim beklerken `show(..., delay=0)` çağrılırsa hemen
      görünür. `wait_visible(timeout=1.0) -> bool` kaplama ekrana çizilene kadar
      bekler — GIL'i tutan uzun işlerden (RSA-4096 üretimi gibi) ÖNCE işçi
      thread'inden çağrılır ki yükleyici iş başlamadan görünsün. UI döngüsünden
      çağrılmamalıdır (kendi kendini bekler).

Saf matematik yardımcıları (fill_fraction, arc_for_phase, dot_progress,
lerp_point, rock_angle, ring_geometry) Flet'ten bağımsızdır ve testlerde
doğrudan sınanır (tests/test_brand_loader.py).
"""

import asyncio
import math
import threading
import time

import flet as ft
import flet.canvas as cv

from desktop.theme import C

# Marka görselleri (assets_dir'e göreli; client.ASSETS_DIR)
WINDOW_ICON = "brand/app.ico"
BRAND_ICON = "brand/icon.png"
BRAND_SPLASH = "brand/splash.png"

TAU = 2 * math.pi
FILL_PERIOD = 2.4      # bir halkanın dolup boşalma süresi (sn)
RIGHT_LAG = 0.25       # sağ halkanın faz gecikmesi (periyodun kesri)
ROCK_PERIOD = 3.2      # sallanma periyodu (sn)
ROCK_AMPLITUDE = 0.09  # sallanma genliği (radyan, ~5°)
RING_START = math.pi   # dolum, halkanın en sol noktasından başlar


# ── Saf animasyon matematiği ───────────────────────────────────────────
def _smooth(x: float) -> float:
    """0..1 aralığında yumuşak geçiş (smoothstep)."""
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def fill_fraction(phase: float) -> float:
    """Halkanın dolu kesri: faz 0→0.5 boyunca 0→1, 0.5→1 boyunca 1→0 (periyodik)."""
    p = phase % 1.0
    return _smooth(p * 2) if p < 0.5 else 1.0 - _smooth((p - 0.5) * 2)


def arc_for_phase(phase: float, base: float = RING_START) -> tuple:
    """(start_angle, sweep_angle) radyan. İlk yarıda yay `base`ten büyür; ikinci
    yarıda kuyruk ileri kayarak boşalır (yılan gibi). sweep/TAU == fill_fraction."""
    p = phase % 1.0
    if p < 0.5:
        return base, TAU * fill_fraction(p)
    shrink = _smooth((p - 0.5) * 2)
    return base + TAU * shrink, TAU * (1.0 - shrink)


def dot_progress(phase: float):
    """Noktanın sol→sağ yolculuğundaki ilerlemesi (0..1); yolda değilse None.
    Sol halka dolarken (faz 0.2–0.8) yola çıkar."""
    p = phase % 1.0
    if p < 0.2 or p > 0.8:
        return None
    return _smooth((p - 0.2) / 0.6)


def lerp_point(a: tuple, b: tuple, u: float) -> tuple:
    """a ile b arasında doğrusal ara nokta (u=0 → a, u=1 → b)."""
    return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u


def rock_angle(t: float) -> float:
    """İşaretin t anındaki sallanma açısı (radyan)."""
    return ROCK_AMPLITUDE * math.sin(TAU * t / ROCK_PERIOD)


def ring_geometry(size: float) -> dict:
    """size×size tuvalde iki halkanın ve anahtar deliğinin ölçüleri."""
    s = float(size)
    rw, rh = 0.50 * s, 0.36 * s
    cy = 0.5 * s
    left, right = (0.31 * s, cy), (0.69 * s, cy)
    return {
        "size": s,
        "ring_w": rw,
        "ring_h": rh,
        "stroke": max(2.0, 0.07 * s),
        "left": left,
        "right": right,
        "badge_r": 0.085 * s,
        "center": (0.5 * s, cy),
    }


def frame_state(t: float) -> dict:
    """t saniyesindeki tüm animasyon değerleri (Flet'siz, test edilebilir)."""
    p_left = (t / FILL_PERIOD) % 1.0
    p_right = (t / FILL_PERIOD - RIGHT_LAG) % 1.0
    return {
        "rock": rock_angle(t),
        "left_arc": arc_for_phase(p_left),
        "right_arc": arc_for_phase(p_right),
        "dot": dot_progress(p_left),
    }


# ── Kontrol ────────────────────────────────────────────────────────────
@ft.control
class ChainLoader(ft.Column):
    """Animasyonlu marka zinciri + altyazı. Ayrıntılar: modül açıklaması."""

    message: str = ""
    size: float = 96
    fps: int = 30
    autostart: bool = True

    def init(self):
        super().init()
        self._running = False
        self._gen = 0
        self._future = None
        self._lock = threading.Lock()
        self._mounted = False

        g = ring_geometry(self.size)
        self._geo = g
        s, rw, rh, sw = g["size"], g["ring_w"], g["ring_h"], g["stroke"]
        track = ft.Paint(
            color=ft.Colors.with_opacity(0.22, C.accent),
            stroke_width=sw,
            style=ft.PaintingStyle.STROKE,
            anti_alias=True,
        )

        def fill_paint(color):
            return ft.Paint(
                color=color,
                stroke_width=sw,
                stroke_cap=ft.StrokeCap.ROUND,
                style=ft.PaintingStyle.STROKE,
                anti_alias=True,
            )

        (lx, ly), (rx, ry) = g["left"], g["right"]
        self._left_arc = cv.Arc(lx - rw / 2, ly - rh / 2, rw, rh, RING_START, 0, paint=fill_paint(C.accent))
        self._right_arc = cv.Arc(rx - rw / 2, ry - rh / 2, rw, rh, RING_START, 0, paint=fill_paint(C.accent_light))

        cx, cy = g["center"]
        br = g["badge_r"]
        # Anahtar deliği: vurgu renkli rozet üzerinde beyaz yuva (iki temada da okunur)
        badge = cv.Circle(cx, cy, br, paint=ft.Paint(color=C.accent, style=ft.PaintingStyle.FILL))
        badge_ring = cv.Circle(
            cx, cy, br,
            paint=ft.Paint(color=ft.Colors.with_opacity(0.9, "#ffffff"), stroke_width=max(1.0, 0.012 * s),
                           style=ft.PaintingStyle.STROKE),
        )
        hole_r = 0.30 * br
        hole_cy = cy - 0.22 * br
        key_white = ft.Paint(color="#ffffff", style=ft.PaintingStyle.FILL, anti_alias=True)
        keyhole_head = cv.Circle(cx, hole_cy, hole_r, paint=key_white)
        keyhole_slot = cv.Path(
            [
                cv.Path.MoveTo(cx - 0.12 * br, hole_cy),
                cv.Path.LineTo(cx + 0.12 * br, hole_cy),
                cv.Path.LineTo(cx + 0.22 * br, cy + 0.55 * br),
                cv.Path.LineTo(cx - 0.22 * br, cy + 0.55 * br),
                cv.Path.Close(),
            ],
            paint=key_white,
        )

        # Akan nokta: bulanık parıltı + beyaz çekirdek
        self._dot_glow = cv.Circle(
            lx, ly, 0.07 * s,
            paint=ft.Paint(color=ft.Colors.with_opacity(0.0, C.accent_light), blur_image=0.05 * s,
                           style=ft.PaintingStyle.FILL),
        )
        self._dot_core = cv.Circle(
            lx, ly, 0.028 * s,
            paint=ft.Paint(color=ft.Colors.with_opacity(0.0, "#ffffff"), style=ft.PaintingStyle.FILL),
        )

        self._canvas = cv.Canvas(
            shapes=[
                cv.Oval(lx - rw / 2, ly - rh / 2, rw, rh, paint=track),
                cv.Oval(rx - rw / 2, ry - rh / 2, rw, rh, paint=track),
                self._left_arc,
                self._right_arc,
                badge,
                badge_ring,
                keyhole_head,
                keyhole_slot,
                self._dot_glow,
                self._dot_core,
            ],
            width=s,
            height=s,
            rotate=ft.Rotate(0, alignment=ft.Alignment(0, 0)),
        )
        self._caption = ft.Text(
            self.message,
            size=13 if s >= 64 else 12,
            color=C.text_secondary,
            text_align=ft.TextAlign.CENTER,
            visible=bool(self.message),
        )
        self.controls = [self._canvas, self._caption]
        self.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self.alignment = ft.MainAxisAlignment.CENTER
        self.spacing = 10
        self.tight = True
        self._render(0.0)

    # ── çizim ──
    def _render(self, t: float):
        """t anındaki kareyi şekillere uygular (yalnızca alan değişikliği, ağ yok)."""
        st = frame_state(t)
        self._canvas.rotate = ft.Rotate(st["rock"], alignment=ft.Alignment(0, 0))
        self._left_arc.start_angle, self._left_arc.sweep_angle = st["left_arc"]
        self._right_arc.start_angle, self._right_arc.sweep_angle = st["right_arc"]
        u = st["dot"]
        if u is None:
            alpha = 0.0
            x, y = self._geo["left"]
        else:
            alpha = math.sin(math.pi * u)
            x, y = lerp_point(self._geo["left"], self._geo["right"], u)
        self._dot_glow.x = self._dot_core.x = x
        self._dot_glow.y = self._dot_core.y = y
        self._dot_glow.paint = ft.Paint(
            color=ft.Colors.with_opacity(round(0.75 * alpha, 3), C.accent_light),
            blur_image=0.05 * self._geo["size"],
            style=ft.PaintingStyle.FILL,
        )
        self._dot_core.paint = ft.Paint(
            color=ft.Colors.with_opacity(round(alpha, 3), "#ffffff"),
            style=ft.PaintingStyle.FILL,
        )

    def _flush(self) -> bool:
        """Kareyi istemciye gönderir; kontrol sayfadan koptuysa False döner."""
        if not self._mounted:
            return False
        if not self.visible:
            return True
        try:
            self._canvas.update()
            return True
        except Exception:
            return False

    async def _run(self, gen: int):
        t0 = time.monotonic()
        dt = 1.0 / max(1, int(self.fps))
        try:
            while self._running and gen == self._gen:
                self._render(time.monotonic() - t0)
                if not self._flush():
                    break
                await asyncio.sleep(dt)
        except asyncio.CancelledError:
            pass
        finally:
            with self._lock:
                if gen == self._gen:
                    self._running = False
                    self._future = None

    # ── yaşam döngüsü ──
    @property
    def running(self) -> bool:
        return self._running

    def start(self, page=None):
        """Animasyonu başlatır (zaten çalışıyorsa bir şey yapmaz)."""
        with self._lock:
            if self._running:
                return
            pg = page if page is not None else self.page
            self._running = True
            self._gen += 1
            gen = self._gen
        try:
            fut = pg.run_task(self._run, gen)
        except Exception:
            with self._lock:
                self._running = False
            raise
        with self._lock:
            if gen == self._gen and self._running:
                self._future = fut
            else:
                fut.cancel()

    def stop(self):
        """Animasyonu durdurur ve bekleyen görevi iptal eder (idempotent)."""
        with self._lock:
            self._running = False
            self._gen += 1
            fut, self._future = self._future, None
        if fut is not None:
            try:
                fut.cancel()
            except Exception:
                pass

    def set_message(self, message: str):
        """Altyazıyı değiştirir. UI döngüsünden çağrılmalı (ya da run_on_ui ile)."""
        self.message = message or ""
        self._caption.value = self.message
        self._caption.visible = bool(self.message)
        if self._mounted:
            try:
                self._caption.update()
            except Exception:
                pass

    def did_mount(self):
        super().did_mount()
        self._mounted = True
        if self.autostart:
            try:
                self.start()
            except Exception:
                pass

    def will_unmount(self):
        self._mounted = False
        self.stop()
        super().will_unmount()


# ── Tam ekran ve kaplama yardımcıları ──────────────────────────────────
def chain_splash(message: str = "", size: float = 120, show_splash: bool = False) -> ft.Container:
    """Tema zemininde ortalanmış tam ekran yükleyici. Yükleyici: `container.data`."""
    loader = ChainLoader(message=message, size=size)
    layers = []
    if show_splash and C.is_dark:
        layers.append(ft.Container(
            image=ft.DecorationImage(src=BRAND_SPLASH, fit=ft.BoxFit.COVER, opacity=0.25),
            left=0, top=0, right=0, bottom=0,
        ))
    layers.append(ft.Container(content=loader, alignment=ft.Alignment(0, 0), left=0, top=0, right=0, bottom=0))
    return ft.Container(
        content=ft.Stack(layers, expand=True),
        expand=True,
        bgcolor=C.bg,
        data=loader,
    )


class BusyOverlay:
    """Sayfa kaplamasında tam ekran, tıklamaları yutan yükleyici (thread güvenli)."""

    def __init__(self, page: ft.Page, size: float = 110):
        self.page = page
        self.size = size
        self.message = ""
        self.shown = False          # çağıranın bakış açısından (eşzamanlı güncellenir)
        self._token = 0
        self._host = None
        self._loader = None
        self._visible = threading.Event()   # kaplama gerçekten çizildi mi

    def show(self, message: str = "", delay: float = 0.25):
        self.message = message or ""
        if self.shown:
            self._schedule(self._ui_set_message, self.message)
            if not delay and not self._visible.is_set():
                # gecikmeli gösterim bekliyor: beklemeden şimdi göster
                self._schedule(self._ui_show, self._token, 0)
            return
        self._token += 1
        token = self._token
        self.shown = True
        self._visible.clear()
        self._schedule(self._ui_show, token, delay)

    def wait_visible(self, timeout: float = 1.0) -> bool:
        """Kaplama ekrana çizilene kadar bekler (yalnızca işçi thread'lerinden)."""
        return self._visible.wait(timeout)

    def set_message(self, message: str):
        self.message = message or ""
        if self.shown:
            self._schedule(self._ui_set_message, self.message)

    def hide(self):
        self._token += 1
        self.shown = False
        self._visible.clear()
        self._schedule(self._ui_hide)

    def _schedule(self, coro_fn, *args):
        try:
            self.page.run_task(coro_fn, *args)
        except Exception:
            pass

    async def _ui_show(self, token, delay):
        if delay:
            await asyncio.sleep(delay)
        if token != self._token:
            return
        if self._host is not None:
            # zaten çizili (hızlı gösterim ya da iptal edilmiş gizleme): yalnız altyazı
            if self._loader is not None:
                self._loader.set_message(self.message)
            self._visible.set()
            return
        self._loader = ChainLoader(message=self.message, size=self.size)
        self._host = ft.Container(
            content=ft.Container(
                content=self._loader,
                bgcolor=C.surface,
                border=ft.Border.all(1, C.border),
                border_radius=20,
                padding=ft.Padding(28, 22, 28, 22),
                shadow=ft.BoxShadow(blur_radius=24, color=C.shadow, offset=ft.Offset(0, 6)),
            ),
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.with_opacity(0.72, C.bg),
            left=0, top=0, right=0, bottom=0,
            on_click=lambda e: None,   # alttaki ekrana tıklama geçmesin
        )
        self.page.overlay.append(self._host)
        self.page.update()
        # update() yalnızca kuyruğa yazar; bekleyen işçi GIL'i kapmadan önce
        # farkın istemciye gerçekten gönderilmesi için döngüye kısa süre yol ver.
        await asyncio.sleep(0.12)
        if token == self._token:
            self._visible.set()

    async def _ui_set_message(self, message):
        if self._loader is not None:
            self._loader.set_message(message)

    async def _ui_hide(self):
        if self.shown:
            return              # arada yeniden show() geldi: kaplama kalsın
        host, self._host, self._loader = self._host, None, None
        if host is not None and host in self.page.overlay:
            self.page.overlay.remove(host)
            self.page.update()
