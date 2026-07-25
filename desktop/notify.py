"""desktop/notify.py — Yeni mesaj bildirim sesi.

flet 0.85'te `ft.Audio` bulunmadığı için ses, zaten proje bağımlılığı olan
`sounddevice` + `numpy` ile anlık üretilir — harici ses dosyası gerekmez.
(Web istemcisindeki "dosyasız Web Audio beep" yaklaşımının eşleniği,
bkz. static/js/voip.js:377.)

Tasarım kuralı: bildirim sesi hiçbir koşulda mesajlaşmayı bozmamalıdır.
Ses cihazı yoksa, meşgulse veya sürücü hata verirse sessizce atlanır.
"""

import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44100

# Aynı anda birden fazla bildirim sesinin cihazı kilitlemesini önler
_play_lock = threading.Lock()


def _tone(freq: float, duration: float, volume: float = 0.22) -> np.ndarray:
    """Tek bir sinüs tonu üretir; başına/sonuna kısa fade koyarak 'tık' sesini engeller."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    wave = np.sin(2 * np.pi * freq * t)

    fade = int(SAMPLE_RATE * 0.008)
    if fade > 0 and len(wave) > 2 * fade:
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)

    return (wave * volume).astype(np.float32)


def _play_blocking():
    try:
        chunk = np.concatenate([
            _tone(880.0, 0.09),
            np.zeros(int(SAMPLE_RATE * 0.035), dtype=np.float32),
            _tone(1174.0, 0.13),
        ])
        # Aynı anda tek çalma; kilit alınamıyorsa sesi tamamen atla
        if not _play_lock.acquire(blocking=False):
            return
        try:
            sd.play(chunk, SAMPLE_RATE, blocking=True)
        finally:
            _play_lock.release()
    except Exception as ex:
        print(f"[Notify] Bildirim sesi calinamadi (yok sayildi): {ex}")


def play_notification():
    """Kısa çift tonlu bildirim sesi çalar. Bloke etmez, hata fırlatmaz."""
    threading.Thread(target=_play_blocking, daemon=True, name="notify-sound").start()
