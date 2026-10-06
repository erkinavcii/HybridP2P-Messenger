// static/js/prefs.js — Cihaz geneli arayüz tercihleri (tema, bildirim sesi).
//
// Bilinçli olarak localStorage: tema ilk boyamadan önce eşzamanlı okunmalı
// (index.html'deki satır içi betik), ve bunlar gizli veri değildir. Anahtarlar,
// sohbetler ve grup verileri IndexedDB'de kalır (AGENTS.md §2.4).

const KEY = "hybridp2p_prefs";
const DEFAULTS = { theme: "dark", sound_enabled: true };

function load() {
    try {
        return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
    } catch {
        return { ...DEFAULTS };
    }
}

export function getPref(name) {
    return load()[name];
}

export function setPref(name, value) {
    const prefs = load();
    prefs[name] = value;
    try {
        localStorage.setItem(KEY, JSON.stringify(prefs));
    } catch {
        // Depolama kapalıysa tercih yalnızca bu oturumda geçerli olur
    }
}

export function applyTheme(theme = getPref("theme")) {
    document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
}

export function toggleTheme() {
    const next = getPref("theme") === "light" ? "dark" : "light";
    setPref("theme", next);
    applyTheme(next);
    return next;
}

// ── Bildirim sesi: masaüstündekiyle aynı iki tonlu kısa bip (dosya yok, WebAudio) ──
let audioCtx = null;

export function playNotification() {
    if (!getPref("sound_enabled")) return;
    try {
        audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
        // Tarayıcılar sesi ancak bir kullanıcı etkileşiminden sonra açar; giriş tıklaması yeterli
        if (audioCtx.state === "suspended") audioCtx.resume();
        const t0 = audioCtx.currentTime;
        [[880, 0], [1320, 0.12]].forEach(([freq, offset]) => {
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = "sine";
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.0001, t0 + offset);
            gain.gain.exponentialRampToValueAtTime(0.18, t0 + offset + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.11);
            osc.connect(gain).connect(audioCtx.destination);
            osc.start(t0 + offset);
            osc.stop(t0 + offset + 0.12);
        });
    } catch (err) {
        // Ses çalınamaması hiçbir koşulda mesaj akışını bozmamalı
        console.warn("Bildirim sesi çalınamadı:", err);
    }
}
