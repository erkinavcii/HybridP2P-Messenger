// static/js/safe.js — Uzaktan gelen değerleri HTML'e basmadan önce güvenli hale getiren
// saf yardımcılar. DOM'a dokunmaz; bu yüzden Node'dan da içe aktarılıp test edilebilir.

// HTML metni / öznitelik değeri için kaçışlama
export function escapeHtml(text) {
    return String(text ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Dosya adı uzantısından resim MIME türü: yalnızca beyaz liste (svg vb. asla).
// Uzantı ne olursa olsun veri URL'sine ham uzantı basılmaz (öznitelik enjeksiyonu yok).
const IMAGE_MIME = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp" };
export function imageMime(fileName) {
    const name = String(fileName ?? "");
    const i = name.lastIndexOf(".");
    const ext = i >= 0 ? name.slice(i + 1).toLowerCase() : "";
    return Object.prototype.hasOwnProperty.call(IMAGE_MIME, ext) ? IMAGE_MIME[ext] : "image/png";
}

// onclick="fn(${jsArg(x)})" için: JS dize sabiti (JSON) + HTML kaçışı
export function jsArg(value) {
    return escapeHtml(JSON.stringify(String(value ?? "")));
}
