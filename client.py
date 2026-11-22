"""
client.py — Flet Tabanlı E2EE Mesajlaşma İstemcisi (giriş noktası)
===================================================================
v3.3 — Hızlı ilk kare + animasyonlu marka zinciri yükleyicisi (L1)

Uygulamanın tüm davranışı `desktop/` paketindeki mixin sınıflarında, bunları
birleştiren `MessengerApp` ise `desktop/app.py`'de yaşar. Bu dosya bilerek
hafiftir (aiortc / cv2 / av / requests İÇE AKTARILMAZ):

  1. `boot(page)` pencereyi, temayı ve dili kurar ve HEMEN tam ekran zincir
     yükleyiciyi ("Başlatılıyor…") gösterir — Flet'in yerel "Working…"
     ekranı yerine ilk karede marka animasyonu görünür,
  2. ağır modülleri (`desktop.app`) arka plan thread'inde içe aktarır —
     `prefetch()` bunu ft.run() pencereyi açarken başlatır, böylece süre örtüşür,
  3. `MessengerApp`'i UI döngüsünde (`page.run_task`) kurup gerçek arayüze geçer.

Geriye uyumluluk: `from client import MessengerApp` hâlâ çalışır (tembel yükleme).
"""

import os
import sys
import threading
import time
import traceback

import flet as ft
from desktop.theme import C, t, set_lang
from desktop import settings_store
from desktop.brand_loader import WINDOW_ICON, BRAND_ICON as LOGIN_ICON, BRAND_SPLASH as LOGIN_SPLASH, chain_splash

_BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(_BASE_DIR, "assets")

__all__ = ["ASSETS_DIR", "WINDOW_ICON", "LOGIN_ICON", "LOGIN_SPLASH", "MessengerApp", "boot", "main", "prefetch"]

# HYBRIDP2P_BOOT_LOG=1 → açılış zaman damgalarını konsola yazar (ilk kare ölçümü)
_BOOT_LOG = os.environ.get("HYBRIDP2P_BOOT_LOG") == "1"
_T0 = time.perf_counter()


def _boot_log(what: str):
    if _BOOT_LOG:
        print(f"[boot] {(time.perf_counter() - _T0) * 1000:7.0f} ms  {what}", flush=True)


def __getattr__(name):
    # `from client import MessengerApp` için tembel içe aktarma (ağır modüller)
    if name == "MessengerApp":
        from desktop.app import MessengerApp
        return MessengerApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def prefetch():
    """Ağır modülleri, Flet penceresi açılırken paralel olarak yüklemeye başlar.

    ft.run() masaüstü istemcisini başlatıp bağlantıyı beklerken (~0.5 sn) Python
    boşta kalır; bu süre desktop.app içe aktarımına kullanılır. Hata olursa yutulur:
    boot() aynı içe aktarmayı tekrar dener ve hatayı ekranda gösterir.
    """
    def _imp():
        try:
            import desktop.app  # noqa: F401
            _boot_log("desktop.app ön yüklemesi bitti")
        except Exception:
            pass

    threading.Thread(target=_imp, daemon=True, name="app-prefetch").start()


def boot(page: ft.Page, serverless_only: bool = False):
    """İlk kareyi (zincir yükleyici) hemen çizer, uygulamayı arka planda yükler."""
    _boot_log("main() çağrıldı")
    page.title = "HybridP2P — Direct P2P" if serverless_only else "HybridP2P Messenger"
    page.window.width = 480
    page.window.height = 820
    page.window.icon = WINDOW_ICON
    page.padding = 0

    C.apply(settings_store.get("theme"))
    set_lang(settings_store.get("lang"))
    page.theme_mode = ft.ThemeMode.DARK if C.is_dark else ft.ThemeMode.LIGHT
    page.bgcolor = C.bg
    page.theme = ft.Theme(color_scheme_seed=C.accent, font_family="Inter, sans-serif")

    page.add(chain_splash(t("loader_starting")))
    _boot_log("ilk kare (yükleyici) gönderildi")

    async def _mount(app_cls):
        # UI döngüsünde: MessengerApp ekranı temizleyip giriş ekranını ekler;
        # yükleyici sayfadan çıkınca animasyonu kendiliğinden durur.
        app_cls(page, serverless_only=serverless_only)
        _boot_log("arayüz hazır")

    async def _fail(err: str):
        page.controls.clear()
        page.add(ft.Container(
            content=ft.Text(err, color=C.danger, size=12, selectable=True),
            padding=20,
            expand=True,
            bgcolor=C.bg,
        ))

    def _load():
        try:
            from desktop.app import MessengerApp   # ağır içe aktarma (aiortc, cv2, av …)
            _boot_log("desktop.app içe aktarıldı")
            page.run_task(_mount, MessengerApp)
        except Exception:
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            page.run_task(_fail, tb)

    threading.Thread(target=_load, daemon=True, name="app-boot").start()


def main(page: ft.Page):
    boot(page)


if __name__ == "__main__":
    _boot_log("client.py içe aktarıldı")
    prefetch()
    ft.run(main, assets_dir=ASSETS_DIR)
