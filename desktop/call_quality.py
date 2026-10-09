"""desktop/call_quality.py — Zayıf bağlantıda görüntü kalitesini otomatik düşürme.

Her INTERVAL_SEC saniyede bir bağlantı istatistiği (paket kaybı, gecikme) okunur:
  • kötü örnek (kayıp > %8 ya da RTT > 400 ms) üst üste 2 kez → kalite bir basamak düşer
  • iyi örnek (kayıp < %2 ve RTT < 250 ms) üst üste 5 kez (~10 sn) → bir basamak çıkar
  • arada kalan örnek sayaçları sıfırlar (kalite sabit kalır)
  • yükseltmeden kısa süre (RECENT_RAISE_SAMPLES) sonra yine düşerse, bir sonraki
    yükseltme için gereken iyi örnek sayısı ikiye katlanır (en fazla MAX_GOOD_SAMPLES):
    sınırda bir hatta kalite sürekli inip çıkmaz.
Bant genişliği tahmini bilinçli olarak kullanılmaz: tarayıcı bunu kendisi uygular ve
bizim koyduğumuz bit hızı sınırı tahmini aşağıda tutar (kendi kendini besleyen döngü).
Ses hiç kısılmaz: yalnızca görüntünün çözünürlüğü ve kare hızı düşer, böylece zayıf
hatta ses için bant kalır.

Kural static/serverless.html (P2PCore.QualityPolicy) ve static/js/quality.js ile
birebir aynıdır; tests/test_serverless_html.py aynı örnek dizileriyle karşılaştırır.
"""

import asyncio

LOSS_BAD, RTT_BAD = 0.08, 0.40          # kayıp oranı (0-1), RTT (saniye)
LOSS_GOOD, RTT_GOOD = 0.02, 0.25
BAD_SAMPLES_TO_DROP = 2
GOOD_SAMPLES_TO_RAISE = 5
MAX_GOOD_SAMPLES = 30          # geri çekilme üst sınırı (~60 sn)
RECENT_RAISE_SAMPLES = 15      # yükseltmeden sonraki ~30 sn
MAX_LEVEL = 3
INTERVAL_SEC = 2.0

# Masaüstü kamerası en fazla 640x480@15 verir (desktop/voip_tracks.CameraTrack)
DESKTOP_LEVELS = [(640, 480, 15), (480, 360, 12), (320, 240, 10), (160, 120, 8)]

LABELS = [
    "",
    "Bağlantı zayıf — görüntü kalitesi düşürüldü",
    "Bağlantı zayıf — görüntü düşük kalitede",
    "Bağlantı çok zayıf — görüntü en düşük kalitede, ses öncelikli",
]


def classify(loss, rtt) -> str:
    """Bir ölçüm örneği: "bad" | "good" | "neutral". Bilinmeyen değer (None) iyi sayılır."""
    if (loss is not None and loss > LOSS_BAD) or (rtt is not None and rtt > RTT_BAD):
        return "bad"
    if (loss is None or loss < LOSS_GOOD) and (rtt is None or rtt < RTT_GOOD):
        return "good"
    return "neutral"


class QualityPolicy:
    """Basamak (0 = en iyi … MAX_LEVEL = en düşük) ve gecikmeli (histerezisli) geçiş."""

    def __init__(self):
        self.level, self.bad, self.good = 0, 0, 0
        self.need_good = GOOD_SAMPLES_TO_RAISE
        self.since_raise = None       # son yükseltmeden beri örnek sayısı (hiç yoksa None)

    def step(self, loss, rtt) -> bool:
        """Yeni örneği işler; basamak değiştiyse True."""
        kind = classify(loss, rtt)
        if self.since_raise is not None:
            self.since_raise += 1
        if kind == "bad":
            self.bad, self.good = self.bad + 1, 0
            if self.bad >= BAD_SAMPLES_TO_DROP and self.level < MAX_LEVEL:
                if self.since_raise is not None and self.since_raise <= RECENT_RAISE_SAMPLES:
                    self.need_good = min(MAX_GOOD_SAMPLES, self.need_good * 2)
                self.level, self.bad = self.level + 1, 0
                return True
        elif kind == "good":
            self.good, self.bad = self.good + 1, 0
            if self.good >= self.need_good and self.level > 0:
                self.level, self.good, self.since_raise = self.level - 1, 0, 0
                return True
        else:
            self.bad = self.good = 0
        return False


def stats_sample(report):
    """aiortc getStats() raporundan (kayıp oranı, RTT sn). Karşı tarafın RTCP raporu
    henüz gelmediyse (None, None). Görüntü akışı varsa onunki, yoksa sesinki."""
    best = None
    for s in report.values():
        if getattr(s, "type", "") == "remote-inbound-rtp":
            if best is None or (s.kind == "video" and best.kind != "video"):
                best = s
    if best is None:
        return None, None
    loss = best.fractionLost / 256 if best.fractionLost is not None else None   # RTCP: 0-255
    return loss, best.roundTripTime


async def monitor(pc, camera, on_change, interval=INTERVAL_SEC):
    """Görüşme süresince kaliteyi izler; basamak değişince kamerayı ayarlar ve
    on_change(level) çağırır (arayüz güncellemesi çağıranın işi: run_on_ui)."""
    policy = QualityPolicy()
    while pc.connectionState not in ("closed", "failed") and getattr(camera, "running", False):
        await asyncio.sleep(interval)
        try:
            loss, rtt = stats_sample(await pc.getStats())
        except Exception:
            continue
        if policy.step(loss, rtt):
            w, h, fps = DESKTOP_LEVELS[policy.level]
            camera.set_quality(w, h, fps)
            print(f"[Kalite] basamak {policy.level}: {w}x{h}@{fps} (kayıp={loss}, rtt={rtt})")
            on_change(policy.level)
