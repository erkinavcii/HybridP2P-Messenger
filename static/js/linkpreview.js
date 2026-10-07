// static/js/linkpreview.js — Gelen link önizlemelerini doğrular ve temizler.
//
// Önizlemeyi GÖNDEREN üretir (desktop/linkpreview.py) ve alıcıya şifreli +
// o mesaja bağlı imzalı gönderir. Web istemcisi yalnızca ALIR: tarayıcı CORS
// nedeniyle başka sitelerin sayfasını okuyamaz, ayrıca alıcı siteye hiç
// bağlanmaz (IP'si sızmaz). Kurallar masaüstündeki sanitize() ile aynıdır.

import { state } from './state.js';
import { decryptMessageJS, base64ToArrayBuffer, arrayBufferToBase64 } from './crypto.js';

const MAX_TITLE = 200, MAX_DESC = 300, MAX_URL = 2048;
const MAX_THUMB_B64 = 60000;
const THUMB_MAX = 240;

export function previewSigData(sender, recipient, encryptedPayload, encryptedPreview) {
    return new TextEncoder().encode(`preview:${sender}:${recipient}:${encryptedPayload}:${encryptedPreview}`);
}

function isHttpUrl(u) {
    try {
        const p = new URL(u);
        return p.protocol === "http:" || p.protocol === "https:";
    } catch {
        return false;
    }
}

export function domainOf(u) {
    try {
        const h = new URL(u).hostname;
        return h.startsWith("www.") ? h.slice(4) : h;
    } catch {
        return "";
    }
}

// Küçük resmi tarayıcıda yeniden kodlar (gömülü içerik / meta veri atılır)
async function reencodeThumb(b64) {
    if (typeof b64 !== "string" || b64.length > MAX_THUMB_B64) return null;
    try {
        const bitmap = await createImageBitmap(new Blob([base64ToArrayBuffer(b64)]));
        try {
            const scale = Math.min(1, THUMB_MAX / Math.max(bitmap.width, bitmap.height));
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.round(bitmap.width * scale));
            canvas.height = Math.max(1, Math.round(bitmap.height * scale));
            canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
            const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", 0.72));
            const out = arrayBufferToBase64(await blob.arrayBuffer());
            return out.length <= MAX_THUMB_B64 ? out : null;
        } finally {
            bitmap.close();
        }
    } catch {
        return null;
    }
}

export async function sanitizePreview(obj) {
    if (typeof obj === "string") {
        try { obj = JSON.parse(obj); } catch { return null; }
    }
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    const url = obj.url;
    if (typeof url !== "string" || url.length > MAX_URL || !isHttpUrl(url)) return null;
    const title = typeof obj.title === "string" ? obj.title.slice(0, MAX_TITLE) : "";
    const description = typeof obj.description === "string" ? obj.description.slice(0, MAX_DESC) : "";
    if (!title && !description) return null;
    return { url, title, description, image: await reencodeThumb(obj.image) };
}

// İmzalı, o mesaja bağlı ve geçerli bir önizleme → temiz nesne; aksi halde null.
// Geçersiz önizleme atılır; mesajın kendisi etkilenmez.
export async function verifiedPreview(sender, encryptedPayload, encryptedPreview, previewSig, verifyFrom) {
    if (!encryptedPreview) return null;
    const data = previewSigData(sender, state.username, encryptedPayload, encryptedPreview);
    if (!(await verifyFrom(sender, data, previewSig))) {
        console.warn(`[LinkPreview] '${sender}' önizleme imzası geçersiz — atıldı.`);
        return null;
    }
    try {
        return await sanitizePreview(await decryptMessageJS(encryptedPreview, state.privateKeyPem));
    } catch (err) {
        console.warn("[LinkPreview] önizleme çözülemedi:", err);
        return null;
    }
}
