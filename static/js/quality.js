// static/js/quality.js — Zayıf bağlantıda görüntü kalitesini otomatik düşürme (tarayıcı).
//
// Her 2 sn'de getStats(): kayıp > %8 ya da RTT > 400 ms üst üste 2 kez → bir basamak
// düşer; kayıp < %2 ve RTT < 250 ms üst üste 5 kez → bir basamak çıkar. Yükseltmeden
// kısa süre sonra yine düşerse sonraki yükseltme için gereken sayı ikiye katlanır (en
// fazla 30): sınırdaki hatta inip-çıkma olmaz. Ses hiç kısılmaz ve ağda önceliklidir.
// Bant tahmini (qualityLimitationReason / availableOutgoingBitrate) bilinçli olarak
// kullanılmaz: tarayıcı bunu zaten uygular ve bizim bit hızı sınırımız tahmini aşağıda
// tutar — "kısıtlı" sinyali kendi kendini besler, kalite hiç geri çıkmazdı.
// Kural desktop/call_quality.py ve static/serverless.html (P2PCore.QualityPolicy) ile
// birebir aynıdır; tests/test_serverless_html.py üçünü aynı dizilerle karşılaştırır.

export const LOSS_BAD = 0.08, RTT_BAD = 0.40;
export const LOSS_GOOD = 0.02, RTT_GOOD = 0.25;
export const BAD_SAMPLES_TO_DROP = 2, GOOD_SAMPLES_TO_RAISE = 5, MAX_LEVEL = 3;
export const MAX_GOOD_SAMPLES = 30, RECENT_RAISE_SAMPLES = 15;
export const INTERVAL_MS = 2000;
// Kısa kenar yüksekliği, kare hızı, üst bit hızı
export const WEB_LEVELS = [
    { height: 720, fps: 30, bitrate: 1_500_000 },
    { height: 480, fps: 24, bitrate: 800_000 },
    { height: 360, fps: 15, bitrate: 400_000 },
    { height: 180, fps: 10, bitrate: 150_000 },
];

export function classify(loss, rtt) {
    if ((loss != null && loss > LOSS_BAD) || (rtt != null && rtt > RTT_BAD)) return "bad";
    if ((loss == null || loss < LOSS_GOOD) && (rtt == null || rtt < RTT_GOOD)) return "good";
    return "neutral";
}

export class QualityPolicy {
    constructor() {
        this.level = 0; this.bad = 0; this.good = 0;
        this.needGood = GOOD_SAMPLES_TO_RAISE;
        this.sinceRaise = null;       // son yükseltmeden beri örnek sayısı (hiç yoksa null)
    }
    /** Yeni örneği işler; basamak değiştiyse true. */
    step(loss, rtt) {
        const kind = classify(loss, rtt);
        if (this.sinceRaise !== null) this.sinceRaise += 1;
        if (kind === "bad") {
            this.bad += 1; this.good = 0;
            if (this.bad >= BAD_SAMPLES_TO_DROP && this.level < MAX_LEVEL) {
                if (this.sinceRaise !== null && this.sinceRaise <= RECENT_RAISE_SAMPLES)
                    this.needGood = Math.min(MAX_GOOD_SAMPLES, this.needGood * 2);
                this.level += 1; this.bad = 0; return true;
            }
        } else if (kind === "good") {
            this.good += 1; this.bad = 0;
            if (this.good >= this.needGood && this.level > 0) { this.level -= 1; this.good = 0; this.sinceRaise = 0; return true; }
        } else { this.bad = 0; this.good = 0; }
        return false;
    }
}

/** RTCStatsReport → { loss (0-1), rtt (sn) }. Görüntü akışı varsa onunki. */
export function statsSample(report) {
    let best = null, pairRtt = null;
    report.forEach((r) => {
        if (r.type === "remote-inbound-rtp" && (!best || (r.kind === "video" && best.kind !== "video"))) best = r;
        if (r.type === "candidate-pair" && r.nominated && r.state === "succeeded" && r.currentRoundTripTime != null)
            pairRtt = r.currentRoundTripTime;
    });
    const loss = best && best.fractionLost != null ? best.fractionLost : null;
    const rtt = best && best.roundTripTime != null ? best.roundTripTime : pairRtt;
    return { loss, rtt };
}

/** Görüntü göndericisini basamağa göre sınırlar (çözünürlük, kare hızı, bit hızı). */
export async function applyLevel(sender, level) {
    const track = sender.track; if (!track) return;
    const st = track.getSettings ? track.getSettings() : {};
    const short = Math.min(st.width || 1280, st.height || 720);
    const L = WEB_LEVELS[level];
    const p = sender.getParameters();
    if (!p.encodings || !p.encodings.length) p.encodings = [{}];
    Object.assign(p.encodings[0], { maxBitrate: L.bitrate, maxFramerate: L.fps,
                                    scaleResolutionDownBy: Math.max(1, short / L.height) });
    await sender.setParameters(p);
}

/** Ses akışına ağ önceliği verir (zayıf hatta önce görüntü feda edilir). */
export async function prioritizeAudio(pc) {
    for (const s of pc.getSenders()) {
        if (!s.track || s.track.kind !== "audio") continue;
        try {
            const p = s.getParameters();
            if (!p.encodings || !p.encodings.length) p.encodings = [{}];
            Object.assign(p.encodings[0], { priority: "high", networkPriority: "high" });
            await s.setParameters(p);
        } catch (e) { /* eski tarayıcı: öncelik desteklenmiyor */ }
    }
}

export function qualityLabel(level, hasVideo) {
    if (!level) return "";
    if (!hasVideo) return "Bağlantı zayıf — ses öncelikli";
    return level === MAX_LEVEL ? `Bağlantı çok zayıf — görüntü ${WEB_LEVELS[level].height}p, ses öncelikli`
                               : `Bağlantı zayıf — görüntü ${WEB_LEVELS[level].height}p'ye düşürüldü`;
}

/** Görüşme boyunca izler; durdurma fonksiyonu döner. onChange(level) */
export function startQualityMonitor(pc, onChange, intervalMs = INTERVAL_MS) {
    const policy = new QualityPolicy();
    prioritizeAudio(pc);
    const timer = setInterval(async () => {
        if (pc.connectionState === "closed" || pc.connectionState === "failed") return clearInterval(timer);
        let s;
        try { s = statsSample(await pc.getStats()); } catch (e) { return; }
        if (!policy.step(s.loss, s.rtt)) return;
        const sender = pc.getSenders().find((x) => x.track && x.track.kind === "video");
        if (sender) { try { await applyLevel(sender, policy.level); } catch (e) { console.warn("kalite ayarlanamadı:", e); } }
        onChange(policy.level, !!sender);
    }, intervalMs);
    return () => clearInterval(timer);
}
