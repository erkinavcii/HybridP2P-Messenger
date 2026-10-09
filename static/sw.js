// static/sw.js — PWA service worker: uygulamayı "kurulabilir" yapar ve
// bağlantı yokken arayüzü açabilir.
//
// Politika: ÖNCE AĞ. Sunucuya ulaşılabildiği sürece her zaman güncel dosya
// kullanılır (sunucu no-cache gönderir; eski JS çalıştırma sorunu geri gelmez).
// Önbellek yalnızca çevrimdışıyken arayüz kabuğunu göstermek içindir.
//
// Asla önbelleğe alınmayanlar: /api/*, /ws/*, /health (canlı ve kişiye özel
// veriler) ve başka sitelerden gelen istekler. Şifresi çözülmüş mesaj/medya
// zaten IndexedDB'de durur; service worker hiçbir içeriği görmez/saklamaz.

const CACHE = "hybridp2p-shell-v3";
const SHELL = [
    "/",
    "/serverless.html",
    "/manifest.webmanifest",
    "/css/styles.css",
    "/js/app.js", "/js/avatar.js", "/js/crypto.js", "/js/db.js", "/js/linkpreview.js",
    "/js/prefs.js", "/js/quality.js", "/js/state.js", "/js/ui.js", "/js/voice.js", "/js/voip.js", "/js/ws.js",
    "/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon-maskable-512.png",
];
const NEVER_CACHE = ["/api/", "/ws/", "/health"];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;
    if (NEVER_CACHE.some((p) => url.pathname.startsWith(p))) return;

    event.respondWith(
        fetch(req)
            .then((res) => {
                if (res.ok && res.type === "basic") {
                    const copy = res.clone();
                    caches.open(CACHE).then((c) => c.put(req, copy));
                }
                return res;
            })
            .catch(async () => {
                const hit = await caches.match(req);
                if (hit) return hit;
                if (req.mode === "navigate") return caches.match("/");
                return Response.error();
            })
    );
});
