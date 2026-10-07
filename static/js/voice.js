// static/js/voice.js — Sesli mesajlar (desktop/voice.py ile aynı protokol).
//
// Sesli mesaj, mevcut E2EE dosya yolundan giden sıradan bir dosyadır:
// file_type "audio", ad "voice-<ms>.ogg" (masaüstü / Firefox) ya da
// "voice-<ms>.webm" (Chrome'un MediaRecorder'ı Ogg kaydedemez). Eski istemciler
// bunu indirilebilir bir dosya olarak görür. Sunucu dosyayı ilk indirmede
// sildiği için alıcı hemen indirir ve ses verisini IndexedDB "media" deposunda
// saklar; ephemeral sohbette hiçbir şey diske yazılmaz (yalnızca bellekte).

import { state, API_URL } from './state.js';
import { encryptBytesJS, decryptBytesJS, makeAuthHeadersJS } from './crypto.js';
import { dbGet, dbSet } from './db.js';

export const MAX_SECONDS = 120;
export const MIN_SECONDS = 0.5;
const VOICE_RE = /^voice-\d+\.(ogg|webm)$/;

export function isVoiceFile(originalName, fileType) {
    return fileType === "audio" && VOICE_RE.test(originalName || "");
}

function mimeFor(name) {
    return name.endsWith(".webm") ? "audio/webm" : "audio/ogg";
}

export function fmtDuration(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ── Yerel ses deposu ──
const memoryMedia = new Map();   // ephemeral sohbetler: yalnızca bellek
const urlCache = new Map();      // file_uuid → blob URL (her çizimde yeniden üretme)

function isEphemeral(partner) {
    return !!(state.chats[partner] && state.chats[partner].ephemeral);
}

async function storeAudio(partner, fileUuid, bytes) {
    if (isEphemeral(partner)) memoryMedia.set(fileUuid, bytes);
    else await dbSet("media", fileUuid, bytes);
}

export async function audioUrl(fileUuid, originalName) {
    if (urlCache.has(fileUuid)) return urlCache.get(fileUuid);
    const bytes = memoryMedia.get(fileUuid) || await dbGet("media", fileUuid);
    if (!bytes) return null;
    const url = URL.createObjectURL(new Blob([bytes], { type: mimeFor(originalName) }));
    urlCache.set(fileUuid, url);
    return url;
}

async function measureDuration(bytes) {
    try {
        const ctx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 1, 48000);
        const buf = await ctx.decodeAudioData(bytes.slice(0));
        return buf.duration;
    } catch {
        return 0;   // çözülemese de mesaj gösterilir (oynatıcı kendi süresini bilir)
    }
}

// ── Kayıt ──
let recorder = null;
let chunks = [];
let startedAt = 0;
let limitTimer = null;

function pickMime() {
    for (const m of ["audio/ogg;codecs=opus", "audio/webm;codecs=opus"]) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
    }
    return "";
}

export function isRecording() {
    return !!recorder;
}

export async function startRecording(onLimit) {
    if (recorder) return;
    const mime = pickMime();
    if (!mime) throw new Error("Bu tarayıcı Opus ses kaydını desteklemiyor.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    recorder = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 32000 });
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.start(250);
    startedAt = performance.now();
    limitTimer = setTimeout(() => onLimit && onLimit(), MAX_SECONDS * 1000);
}

function releaseRecorder() {
    clearTimeout(limitTimer);
    if (recorder) recorder.stream.getTracks().forEach(t => t.stop());
    recorder = null;
}

// Kaydı bitirir → { bytes, ext, duration } (çok kısaysa null)
export async function stopRecording() {
    if (!recorder) return null;
    const rec = recorder;
    const duration = (performance.now() - startedAt) / 1000;
    const done = new Promise(r => { rec.onstop = r; });
    rec.stop();
    await done;
    const ext = rec.mimeType.startsWith("audio/ogg") ? "ogg" : "webm";
    releaseRecorder();
    if (duration < MIN_SECONDS) return null;
    const bytes = await new Blob(chunks, { type: rec.mimeType }).arrayBuffer();
    chunks = [];
    return { bytes, ext, duration: Math.min(duration, MAX_SECONDS) };
}

export function cancelRecording() {
    if (!recorder) return;
    recorder.onstop = null;
    try { recorder.stop(); } catch { /* zaten durmuş */ }
    chunks = [];
    releaseRecorder();
}

// ── Gönderme ── (E2EE dosya yolu: şifrele → yükle → file_message)
export async function sendVoice(recipient, recipientPubKey, recording, sendFrame) {
    const originalName = `voice-${Date.now()}.${recording.ext}`;
    const encrypted = await encryptBytesJS(recording.bytes, recipientPubKey);
    const path = "/api/upload_file";
    const bodyText = JSON.stringify({
        sender: state.username, recipient, encrypted_data: encrypted,
        original_name: originalName, file_type: "audio"
    });
    const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", path, bodyText);
    headers["Content-Type"] = "application/json";
    const resp = await fetch(`${API_URL}${path}`, { method: "POST", headers, body: bodyText });
    if (resp.status !== 200) throw new Error(`yükleme başarısız (${resp.status})`);
    const fileUuid = (await resp.json()).uuid;
    const timestamp = new Date().toISOString();
    await sendFrame({
        type: "file_message", sender: state.username, recipient, file_uuid: fileUuid,
        original_name: originalName, file_type: "audio", view_once: false, timestamp
    });
    await storeAudio(recipient, fileUuid, recording.bytes);
    return {
        sender: state.username, is_voice: true, file_uuid: fileUuid, original_name: originalName,
        duration: recording.duration, timestamp, content: "🎤 Sesli mesaj", read: false
    };
}

// ── Alma ── Sunucu ilk indirmede sildiği için hemen indirilir.
export async function receiveVoice(sender, fileUuid, originalName, timestamp) {
    const path = `/api/download_file/${fileUuid}`;
    const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
    const res = await fetch(`${API_URL}${path}`, { headers });
    if (res.status !== 200) throw new Error(`indirme başarısız (${res.status})`);
    const bytes = await decryptBytesJS((await res.json()).encrypted_data, state.privateKeyPem);
    await storeAudio(sender, fileUuid, bytes);
    return {
        sender, is_voice: true, file_uuid: fileUuid, original_name: originalName,
        duration: await measureDuration(bytes), timestamp, content: "🎤 Sesli mesaj",
        read: state.recipient === sender
    };
}
