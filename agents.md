# 🤖 Agent Görev ve Takip Kılavuzu (agents.md)

Bu dosya, HybridP2P-Messenger projesinde çalışan yapay zeka programlama asistanlarının (Antigravity vb.) hedefleri, mevcut durumu, kuralları ve yapılacak işleri tutarlı bir şekilde takip etmesini sağlamak amacıyla oluşturulmuştur.

---

## 1) TEKNOLOJİ YIĞINI & MİMARİ PRENSİPLER

- **Masaüstü İstemci:** Flet (Python)
- **Web İstemci:** HTML5 / JavaScript (ES6 Modülleri / Vanilla CSS)
- **Röle Sunucu:** FastAPI (Python), SQLite (aiosqlite)
- **Zero-Knowledge & E2EE:** Sunucu mesajları, dosyaları veya özel anahtarları (private key) asla düz metin (plaintext) olarak göremez. Şifreleme tamamen cihaz/tarayıcı tarafında gerçekleşir.
- **Hybrid / Fallback Yapı:** Gerçek zamanlı işlemler için WebSocket kullanılır; WebSocket bağlantısı koptuğunda sistem otomatik olarak REST API fallback moduna geçer.
- **Pure P2P (Sunucusuz Arama):** Merkezi sunucu kapalıyken dahi STUN üzerinden doğrudan cihazlar arası (WebRTC) sesli/görüntülü görüşme.

---

## 2) GENEL ÇALIŞMA PRENSİPLERİ

### 2.1) TEK SEFERDE DOSYA LİMİTİ
Hangi işlem olursa olsun tek seferde maksimum (birbiriyle alakalı) 7 dosya halinde çalışmak zorundasın. İşlemi birbiriyle bağlantılı batch'lere bölmek zorundasın. Eğer bunun aksi talep edilirse DUR ve EK ONAY iste.

### 2.2) UYDURMAK YASAK (NO INVENTING)
Eğer herhangi bir operasyonda bilgi ya da referans eksikliği/hatası yaşıyorsan buradaki eksik/hatalı bilgiyi uydurman yasak. Böyle bir durumda operasyonu durdur ve kullanıcıya sorarak ilerle.

### 2.3) ÖNCE PLANLA, SONRA KODLA
Kod üretmeden önce şunları yapmak zorundasın:
- Bir dosya dökümü hazırla (hangi dosyalar değişecek/eklenecek/silinecek + neden)
- Eğer varsa yeni bağımlılıklar matrisi (Hangi kütüphane, versiyon + neden)
- Planı sun, onay almadan asla implementasyona başlama.

### 2.4) MİMARİ KURALLAR VE REFERANSLAR (GOVERNANCE)

Mimari ve teknik kararlar ile bilinen sorunlar `README.md`, `KNOWN_ISSUES.md` ve `futuresplanning.md` belgelerinde tutulur; mimaride sapma veya değişiklik yapılacağı zaman bu belgelerin güncellenmesi ZORUNDADIR.

Uygulamanın E2EE ve Zero-Knowledge mimarisini korumak için aşağıdaki kurallar BAĞLAYICIDIR:
- **Flet UI ve Thread Güvenliği:** Arka plandan tetiklenen UI güncellemeleri donmaları önlemek için mutlaka `run_on_ui` veya `page.run_task` ile sarmalanmalıdır. `client.py` üzerindeki mevcut asenkron handler'lar referanstır.
- **Uçtan Uca Şifreleme (E2EE):** Tüm iletişim alıcının RSA public key'i üzerinden AES-256-GCM simetrik şifrelemesi ile korunur. Özel anahtarlar (`private_key`) asla diske şifresiz yazılmaz veya sunucuya iletilemez.
- **IndexedDB Standartları:** Web tarayıcı istemcisinde (`static/index.html`) anahtar ve grup verileri `localStorage` yerine tarayıcı içi `IndexedDB` veritabasında saklanır.
- **WebSocket ve REST API Fallback:** Canlı bağlantı için asenkron WebSocket iletişimi önceliklidir; bağlantı koptuğunda REST API (`/api/send_ws_fallback`) fallback moduna geçilmelidir.
- **Pure P2P Arama:** Sunucusuz doğrudan cihazlar arası görüşmeler için zlib/deflate sıkıştırmalı Base64 SDP takası (`pack_sdp` / `unpack_sdp`) standardı uygulanır.

---

## 4) 🚦 GÜNCEL DURUM VE İLERLEME

### 🟢 Tamamlanan Görevler
- [x] **UI Thread Safety:** `client.py` Flet uygulamasındaki arka plan güncellemeleri `run_on_ui` ve `page.run_task` ile sarmalanarak Win32 takılma (freezing) sorunları çözüldü.
- [x] **Web Client IndexedDB:** `static/index.html` üzerinde LocalStorage yerine IndexedDB'ye geçildi; grup metadata'sı korundu ve view-once mesajların yenileme sonrası sızması engellendi.
- [x] **API /api/ice_servers Güvenliği:** `server.py` üzerinde isteklerin imza doğrulama (X-Signature) ile doğrulanması sağlandı; `TURN_SECRET` parametresine bağlı TURN şifresi üretimi eklendi.
- [x] **Pure P2P Entegrasyonu:** Hem masaüstü hem de web istemcisine zlib/deflate sıkıştırmalı Base64 SDP kopyala-yapıştır ile sunucusuz arama özelliği entegre edildi.
- [x] **Web İstemcisi Modülerleştirme:** CSS ve devasa script blokları `styles.css`, `state.js`, `crypto.js`, `db.js`, `voip.js`, `ws.js`, `ui.js`, `app.js` şeklinde ES6 modüllerine bölünerek `static/index.html` 400+ satıra düşürüldü.
- [x] **Masaüstü UX:** yazıyor göstergesi, tarih ayırıcı, bildirim sesi, açık/koyu tema, sesli mesaj, E2EE profil fotoğrafı, kişi rehberi, mesaj düzenleme/herkesten silme, link önizleme (gönderen çeker, şifreli gönderir).
- [x] **Web Paritesi (W1–W5):** imzalı birebir mesajlar, yukarıdaki UX özelliklerinin tamamı web'de; masaüstüyle aynı protokoller (birlikte çalışır). Web yalnızca link önizlemeyi gösterir, üretemez (CORS).
- [x] **Deployment (D1–D5):** Docker imajı, docker-compose (Caddy; alan adı + Let's Encrypt ya da yalnızca IP + kendinden imzalı sertifika), masaüstü https/wss + parmak izi sabitleme, coturn (STUN/TURN) ve Google STUN aç/kapa, PWA, Cloudflare Tunnel demosu, `DEPLOY.md`. **Docker'da henüz çalıştırılmadı** — KNOWN_ISSUES §8.
- [x] **Otomatik Testler:** `tests/` altında pytest paketi (sunucu yolları, imzalar, TLS sabitleme, deployment, PWA önbellek listesi …).

---

## 5) 📋 YOL HARİTASI VE YAPILACAK İŞLER (BACKLOG)

### 5.1. Standalone / Basit Sunucusuz Uygulama (P2P Standalone)
*Masaüstü ve web uygulamalarında P2P kodunu entegre ettik ancak bunu tamamen bağımsız, sunucu gerektirmeyen bağımsız tek bir script/sayfa olarak da sunacağız.*
- [x] **`serverless_client.py`:** Sunucuya hiç bağlanmadan/kaydolmadan açılan masaüstü başlatıcısı: imzalı tek kullanımlık kodlarla doğrudan yazılı mesajlaşma, dosya aktarımı ve sesli/görüntülü arama. Aynı mod normal istemcide giriş ekranındaki "Sunucusuz başlat" ile de açılır.
- [x] **`static/serverless.html`:** Tek dosyalık, sunucusuz telefon/tarayıcı sayfası: masaüstüyle aynı imzalı kodlar, kimlik doğrulama, yazılı mesaj ve dosya aktarımı. Varsayılan hiçbir şey saklamaz; isteğe bağlı "Bu cihazda hatırla" IndexedDB kullanır. CSP WebRTC dışında her ağ isteğini yasaklar. Masaüstüyle uyumu Node.js üzerinden test edilir. Sesli/görüntülü arama da var (S3b).
- [x] **Android uygulaması (APK):** `android/` — `serverless.html`'i saran Java uygulaması (`com.erkin.hybridp2p`, debug imzalı); köprüyle dosya kaydetme, paylaşma, pano, ZXing QR okuma, ekranı açık tutma. Emülatörde doğrulandı (KNOWN_ISSUES §11).
- [x] **STUN seçimi (S4):** Masaüstü ve `serverless.html`'de Google + Cloudflare / yalnızca biri / özel STUN-TURN / yalnızca yerel ağ. Kurallar `p2p_core.ice_servers_for` ile sayfadaki `P2PCore.iceServersFor`'da birebir (KNOWN_ISSUES §9).

### 5.2. Modülerleştirme ve Refactoring (Modularization & Refactoring)
*Tek dosyada biriken ve boyutu aşırı büyüyen web istemcisi (`static/index.html` — 4400+ satır), röle sunucusu (`server.py` — 1800+ satır) ve masaüstü istemcisi (`client.py` — 4372 satır) dosyalarının daha temiz, okunabilir ve yönetilebilir modüllere ayrılması.*
- [x] **Web İstemcisi Modülerleştirme:** CSS dosyalarının `static/css/styles.css` olarak dışarı aktarılması ve Javascript kısımlarının `db.js`, `crypto.js`, `ws.js`, `voip.js`, `ui.js`, `app.js` şeklinde ES6 modüllerine bölünmesi.
- [x] **Sunucu Modülerleştirme:** APIRouter kullanılarak `users.py`, `messages.py`, `groups.py`, `voip.py` olarak ayrılması ve `main.py`, `config.py`, `database.py` şeklinde paket yapısına kavuşturulması.
- [x] **Masaüstü İstemcisi Modülerleştirme:** `client.py`'deki ~80 iç içe closure, `server/` paketiyle aynı desende bir `desktop/` paketine (10 mixin dosyası + `net_config.py` + `voip_tracks.py`) taşındı. Closure'lar Python'da dosyalar arası bölünemediği için mixin+tek-sınıf yaklaşımı kullanıldı: `client.py` artık sadece `MessengerApp` sınıfını (tüm mixin'leri miras alan) kurup `state` + ~47 paylaşılan UI kontrolünü `self.` özniteliği olarak tanımlayan ince bir giriş noktası. Detaylı plan: `.claude/plans/reflective-splashing-leaf.md`.

### 5.3. Sinyalleşme ve NAT Optimizasyonları
- [ ] **BitTorrent DHT Prototipi:** `serverless_client.py` içerisinde oda ismi/parolası hash'i üzerinden infohash arayarak otomatik P2P buluşma (rendezvous) prototipi.
- [x] **Dinamik Kalite Adaptasyonu (VoIP Polish):** `getStats()` kaybı/RTT'si ile görüntü basamak basamak düşer/çıkar (geri çekilmeli), ses öncelikli. Aynı kural `desktop/call_quality.py`, `static/js/quality.js` ve `serverless.html`'de; testlerle karşılaştırılıyor. Bu iş sırasında masaüstü arama medyasındaki eski hatalar da düzeltildi (KNOWN_ISSUES §10).

### 5.4. Eksik UX & Güvenlik Özellikleri
- [ ] **Özel Anahtar Şifreleme (Private Key Encryption):** Yerel cihazdaki private key'lerin kullanıcı şifresiyle şifrelenip PEM olarak diske yazılması. *(Geliştirme sürecinde anahtara kolay erişim için bilinçli olarak ertelendi; yayın öncesi ele alınacak.)*
- [x] **Mesaj İmzalama (Digital Signature):** Birebir mesajlar masaüstü ve web'de RSA-PSS ile imzalanıyor, alıcı doğruluyor (ilk imzada güven + downgrade koruması). *(Açık karar: imzasız mesajı tamamen reddetmek — KNOWN_ISSUES §5.)*

---

## 6) 🛠️ GELİŞTİRME NOTLARI & HATIRLATMALAR
- **Vanilla CSS:** Web uygulamasında harici CSS kütüphaneleri (Tailwind vb.) yerine `static/css/styles.css` içindeki Vanilla CSS kullanılır; renkler yalnızca `:root` belirteçlerinde tanımlıdır (açık tema `:root[data-theme="light"]`).
- **Backwards Compatibility:** WebSocket payload yapıları ve API imza formatları değiştirilirken eski istemcilerin çökmeyeceğinden emin olunmalıdır.
