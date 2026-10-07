// static/js/avatar.js — Uçtan uca şifreli profil fotoğrafları (desktop/avatar.py ile aynı protokol).
//
// Fotoğraf sunucuya asla okunabilir halde gitmez: her kişiye AYRI ayrı, o kişinin
// public key'iyle şifreli ve bizim anahtarımızla imzalı gönderilir (avatar_update).
// İmza verisi alan ayırıcılıdır ("avatar:{gönderen}:{alıcı}:{payload}"), bu yüzden
// bir avatar imzası mesaj imzası yerine kullanılamaz. Gelen resim tarayıcıda
// yeniden kodlanır (EXIF vb. meta veri ve gömülü içerik atılır).

import { state } from './state.js';
import {
    arrayBufferToBase64,
    base64ToArrayBuffer,
    encryptBytesJS,
    decryptBytesJS,
    signDataJS,
    sha256
} from './crypto.js';
import { dbGet, dbSet, dbKeys, getContactPubKey } from './db.js';

export const AVATAR_SIZE = 128;
const MAX_INPUT_BYTES = 15 * 1024 * 1024;      // seçilen dosya
const MAX_RECEIVED_BYTES = 256 * 1024;         // karşıdan gelen (zaten 128px olmalı)
const MAX_PIXELS = 40_000_000;                 // decompression bomb koruması (masaüstüyle aynı)

const cache = new Map();   // kullanıcı adı → data URL (render eşzamanlı okuyabilsin)

export function avatarSigData(sender, recipient, encryptedPayload) {
    return new TextEncoder().encode(`avatar:${sender}:${recipient}:${encryptedPayload}`);
}

function toDataUrl(b64) {
    return `data:image/jpeg;base64,${b64}`;
}

// Herhangi bir resmi ortadan kare kırpıp 128px JPEG'e yeniden kodlar → base64.
export async function normalizeAvatar(bytes, maxBytes = MAX_INPUT_BYTES) {
    if (!bytes || bytes.byteLength === 0) throw new Error("boş dosya");
    if (bytes.byteLength > maxBytes) throw new Error("dosya çok büyük");
    const bitmap = await createImageBitmap(new Blob([bytes]));
    try {
        if (bitmap.width * bitmap.height > MAX_PIXELS) throw new Error("resim çözünürlüğü çok yüksek");
        const side = Math.min(bitmap.width, bitmap.height);
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = AVATAR_SIZE;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side,
                      0, 0, AVATAR_SIZE, AVATAR_SIZE);
        const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", 0.82));
        return arrayBufferToBase64(await blob.arrayBuffer());
    } finally {
        bitmap.close();
    }
}

// ── Önbellek ve okuma ──
export async function loadAvatarCache() {
    cache.clear();
    const own = await dbGet("keys", "own_avatar");
    if (own) cache.set(state.username, toDataUrl(own));
    for (const key of await dbKeys("keys")) {
        if (key.startsWith("avatar_") && !key.startsWith("avatar_sent_")) {
            const b64 = await dbGet("keys", key);
            if (b64) cache.set(key.slice("avatar_".length), toDataUrl(b64));
        }
    }
}

export function avatarUrl(username) {
    return cache.get(username) || null;
}

// Baş harfler veya foto: sohbet listesi / başlık için ortak işaretleme
export function avatarInnerHtml(username, fallbackText) {
    const url = avatarUrl(username);
    return url ? `<img class="avatar-img" src="${url}" alt="">` : fallbackText;
}

// ── Gönderme ──
export async function setOwnAvatar(fileBytes) {
    const b64 = await normalizeAvatar(fileBytes);
    await dbSet("keys", "own_avatar", b64);
    cache.set(state.username, toDataUrl(b64));
    return b64;
}

export async function sendAvatarTo(username, sendFrame) {
    const own = await dbGet("keys", "own_avatar");
    if (!own || username === state.username) return false;
    const pub = await getContactPubKey(username);
    if (!pub) return false;
    const payload = await encryptBytesJS(base64ToArrayBuffer(own), pub);
    const signature = await signDataJS(state.privateKeyPem, avatarSigData(state.username, username, payload));
    await sendFrame({ type: "avatar_update", recipient: username, encrypted_payload: payload, signature });
    await dbSet("keys", `avatar_sent_${username}`, await sha256(own));
    return true;
}

// Bu kişi avatarımızın güncel sürümünü almadıysa gönderir (sohbet açılınca çağrılır).
export async function maybeSendAvatar(username, sendFrame) {
    const own = await dbGet("keys", "own_avatar");
    if (!own) return;
    if ((await dbGet("keys", `avatar_sent_${username}`)) === (await sha256(own))) return;
    try {
        await sendAvatarTo(username, sendFrame);
    } catch (err) {
        console.warn(`[Avatar] '${username}' kişisine gönderilemedi:`, err);
    }
}

// Avatar değişince rehberdeki herkese gönderir.
export async function broadcastAvatar(sendFrame) {
    let sent = 0;
    for (const key of await dbKeys("keys")) {
        if (!key.startsWith("pubkey_")) continue;
        try {
            sent += (await sendAvatarTo(key.slice("pubkey_".length), sendFrame)) ? 1 : 0;
        } catch (err) {
            console.warn("[Avatar] gönderilemedi:", err);
        }
    }
    return sent;
}

// ── Alma ── İmza ZORUNLU: yeni bir tip, imzasız gönderen eski istemci yok.
export async function receiveAvatar(sender, encryptedPayload, signatureB64, verifyFrom) {
    if (!(await verifyFrom(sender, avatarSigData(sender, state.username, encryptedPayload), signatureB64))) {
        console.error(`[Avatar] '${sender}' avatarı imzasız/geçersiz — reddedildi.`);
        return false;
    }
    try {
        const raw = await decryptBytesJS(encryptedPayload, state.privateKeyPem);
        const b64 = await normalizeAvatar(raw, MAX_RECEIVED_BYTES);
        await dbSet("keys", `avatar_${sender}`, b64);
        cache.set(sender, toDataUrl(b64));
        return true;
    } catch (err) {
        console.error(`[Avatar] '${sender}' avatarı işlenemedi:`, err);
        return false;
    }
}

export function forgetAvatar(username) {
    cache.delete(username);
}
