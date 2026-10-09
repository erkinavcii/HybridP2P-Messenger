# 🔐 HybridP2P Messenger — Proje İlerleme Takibi

> **Son Güncelleme:** 2026-06-08  
> **Durum:** Faz 1 ve Faz 2 Tamamlandı ✅ — Faz 3 (Yol Haritası) Beklemede 🔄

---

## 📋 Faz 1: MVP (Minimum Viable Product) — Temel Altyapı

### 🔒 Kriptografi Modülü (`crypto_utils.py`)
- [x] RSA-4096 anahtar çifti üretimi
- [x] AES-256-GCM simetrik şifreleme
- [x] Hibrit şifreleme (RSA-OAEP + AES-GCM)
- [x] PEM serileştirme / deserileştirme
- [x] Yerel anahtar depolama (dosya sistemi)
- [x] Birim testi (self-test) — ✅ Geçti

### 🖥️ Röle Sunucusu (`server.py`)
- [x] FastAPI + Uvicorn kurulumu
- [x] SQLite veritabanı şeması (users, offline_msgs, chat_settings)
- [x] REST: `POST /api/register` — Kullanıcı kaydı + public key
- [x] REST: `GET /api/public_key/{username}` — Anahtar takası
- [x] REST: `GET /api/users` — Kullanıcı listesi
- [x] REST: `POST /api/send_offline` — Çevrimdışı mesaj depolama
- [x] REST: `GET /api/fetch_messages/{username}` — Mesaj çekme + silme
- [x] REST: `GET /api/chat_settings/{username}` — Ephemeral sync
- [x] REST: `POST /api/ephemeral_toggle` — REST fallback toggle
- [x] WebSocket: `/ws/{username}` — Gerçek zamanlı iletim
- [x] WebSocket: `message` tipi — Online/Offline alıcı tespiti
- [x] WebSocket: `ephemeral_toggle` tipi — Her iki taraftan toggle + offline kuyruğu
- [x] WebSocket: Bağlantıda bekleyen mesaj + toggle otomatik teslimi
- [x] Zero-Knowledge: Teslim sonrası mesaj silme
- [x] CORS middleware
- [x] Sunucu syntax testi — ✅ Geçti

### 💾 Yerel Mesaj Geçmişi (`message_store.py`) — YENİ
- [x] MessageStore sınıfı (kullanıcı başına SQLite)
- [x] Chat kaydı oluşturma/okuma
- [x] Ephemeral mod state yönetimi (kim açtı, ne zaman)
- [x] `save_message()` — ephemeral/view-once kontrolü otomatik
- [x] `get_messages()` — sohbet geçmişini yükleme
- [x] `get_all_chats()` — son mesajlarla chat listesi
- [x] `clear_chat_history()` — geçmiş silme
- [x] Sistem mesajı kaydı (ephemeral bildirimleri)
- [x] Birim testi — ✅ Geçti

### 📱 İstemci Arayüzü (`client.py`) — v2.0
- [x] Flet ile koyu tema arayüz tasarımı
- [x] Giriş ekranı (login view)
- [x] Sohbet ekranı (chat view)
- [x] İlk açılışta otomatik anahtar üretimi
- [x] Public key'i sunucuya kaydetme
- [x] Alıcının public key'ini sunucudan çekme
- [x] Mesaj şifreleme ve gönderme
- [x] Gelen mesajları çözme ve gösterme
- [x] WebSocket gerçek zamanlı dinleme (arka plan thread)
- [x] REST API fallback (WebSocket yoksa)
- [x] Çevrimdışı mesaj çekme (refresh butonu)
- [x] Mesaj baloncukları (kendi/karşı taraf)
- [x] Sistem bildirimi baloncuğu
- [x] Durum çubuğu
- [x] Otomatik yeniden bağlanma (exponential backoff)
- [x] **Ephemeral toggle butonu** (app bar'da, chat seviyesi)
- [x] **Iki taraftan toggle** — WebSocket ile aninda sync
- [x] **Offline toggle** — baglaninca teslim edilir
- [x] **Sohbet gecmisi yukleme** — partner secilince onceki mesajlar gelir
- [x] **Sunucu ephemeral sync** — acilista GET /api/chat_settings cagrisi
- [x] **Tek gorunumlu mesaj (view-once)** — per-mesaj toggle, 10s countdown dialog, kaydedilmez
- [x] **Dosya / Resim gonderimi** — FilePicker, AES-256-GCM sifreleme, upload → UUID, receiver indirir
- [x] **Resim inline thumbnail** — data:// URL ile dogrudan gosterim
- [x] **Dosya kaydetme** — ~/Downloads klasorune kaydeder
- [x] **View-once dosya** — indirme sonrasi 10s icinde thumbnail silinir
- [x] **WhatsApp-style Inbox / Chat List (Home screen)** — List of recent chats, avatar/initials, last message snippet, last message timestamp, click-to-open, floating action button for new chat, and dynamic list update.


### 📦 Proje Altyapısı
- [x] `requirements.txt` oluşturma
- [x] Bağımlılık kurulumu (pip install) — ✅ Başarılı
- [x] Mimari dokümantasyon (architecture_walkthrough.md)
- [x] Entegrasyon test dosyası (`test_integration.py`)
- [x] `progress.md` (bu dosya)
- [x] `futures.md` (yol haritası)

---

## 🧪 Faz 2: Test ve Stabilizasyon

- [x] İki istemci ile canlı mesajlaşma testi (alice ↔ bob) — `test_integration.py` ile otomatik test edildi
- [x] Ephemeral toggle iki taraftan test (alice açar, bob görür) — WebSocket & fallback REST sync test edildi
- [x] WebSocket bağlantı kopma / yeniden bağlanma testi — exponential backoff test edildi
- [x] Çevrimdışı mesaj biriktirme ve toplu teslim testi — `test_integration.py` ile otomatik test edildi
- [x] Offline ephemeral toggle testi (bob kapalıyken alice toggle'lar, bob açılınca sync olur) — test edildi
- [x] Geçmiş yükleme testi (uygulama kapanıp açılınca mesajlar yerel SQLite veritabanında saklanır) — test edildi
- [x] Büyük mesaj (>1KB) şifreleme/çözme testi — test edildi
- [x] Yanlış private key ile çözme denemesi (negatif test) — `crypto_utils.py` ve `test_features.py` test edildi
- [x] Sunucu restart sonrası veri kalıcılığı testi — `aiosqlite` entegrasyonu ile test edildi

---

## 🚀 Faz 3: Gelişmiş Özellikler — Yol Haritası

> Aşağıdaki maddeler MVP'nin üzerine eklenebilecek, projeyi profesyonel seviyeye taşıyacak özelliklerdir.

### 🔐 Güvenlik İyileştirmeleri
- [ ] **Signal Protokolü (Double Ratchet)**: Her mesajda yeni anahtar türetme → tam Forward Secrecy. Bir anahtar ele geçirilse bile geçmiş/gelecek mesajlar korunur *(karmaşıklık nedeniyle bilinçli olarak ertelendi; yapılıp yapılmayacağı tartışılacak)*
- [ ] **Anahtar Doğrulama (Key Verification)**: QR kod veya güvenlik numarası ile karşı tarafın anahtarını yüz yüze doğrulama (MITM koruması) *(kısmen: parmak izi rehberde (masaüstü + web) ve kişi kartlarında görünür; QR / güvenlik numarası akışı yok)*
- [x] **Private Key Şifreleme**: Yerel private key'i kullanıcı parolası ile AES şifreleme (cihaz çalınsa bile anahtar güvende) (IndexedDB + Parola korumalı E2EE yedek) ✅
- [x] **Mesaj İmzalama (Digital Signature)**: Birebir mesajlara RSA-PSS imza + alıcı bağlama + ilk-imzada-güven (downgrade koruması); hem WebSocket hem çevrimdışı yolda doğrulanıyor (masaüstü + web) ✅ *(zorunlu doğrulama ayrı karar — bkz. KNOWN_ISSUES.md §5)*
- [ ] **Anahtar Yenileme (Key Rotation)**: Belirli aralıklarla otomatik yeni anahtar çifti üretme ve dağıtma
- [x] **Sunucu Tarafı Rate Limiting**: Brute-force ve spam saldırılarına karşı istek sınırlama ✅

### 💬 Mesajlaşma Özellikleri
- [x] **Tek Gorunumlu Mesaj (View-Once)** — per-mesaj toggle, 10s countdown, hic kaydedilmez ✅
- [x] **Dosya/Resim Gonderimi** — AES-256-GCM sifreleme, sunucu Zero-Knowledge, inline resim ✅
- [x] **Sesli Mesaj**: Mikrofon kaydi → Opus/Ogg (~4 KB/sn) → mevcut E2EE dosya yolu; alici hemen indirip yerelde saklar, yeniden baslatinca da dinlenebilir, ephemeral sohbette diske yazilmaz (masaustu + web; web Chrome'da WebM/Opus kaydeder, masaustu ikisini de tanir) ✅ *(grup sohbetlerinde yok)*
- [x] **Grup Sohbeti**: Birden fazla aliciya sifreli simetrik mesaj (Shared Group Key + Rekeying) ✅
- [x] **Mesaj Duzenleme/Silme**: Kendi mesajini duzenle / herkesten sil — imzali msg_uid (sunucu kimlikleri degistiremez), imzali message_edit/message_delete, yalnizca yazar (masaustu + web, birebir) ✅ *(grup ve ephemeral sohbetlerde, msg_uid oncesi eski mesajlarda yok)*
- [x] **Okundu Bilgisi (Read Receipt)**: Mesajin alici tarafindan okunup okunmadigi (masaüstü & web tarafında çift yeşil tik) ✅
- [x] **Yaziyor... Gostergesi**: Karsi tarafin yazma durumu — birebir sohbetlerde, debounce'lu, yalnizca canli relay (masaustu + web) ✅
- [x] **Mesaj Arama**: Yerel gecmiste arama (Sohbet ve Mesaj Gövdesi Arama) ✅

### 🗄️ Veri Yönetimi
- [x] **Yerel Mesaj Gecmisi (SQLite)** — istemci tarafinda message_store.py ✅
- [ ] **Yedekleme/Geri Yukleme**: Sifreli mesaj gecmisini disa aktarma ve geri yukleme
- [ ] **PostgreSQL Gecisi**: Uretim ortami icin SQLite yerine PostgreSQL (sunucu tarafi)
- [ ] **Redis Pub/Sub**: Cok sunuculu dagitik mimari icin mesaj kuyrugu
- [ ] **Mesaj TTL (Time-to-Live)**: Teslim edilmemis mesajlarin belirli sure sonra otomatik silinmesi

### 🎨 Arayüz ve UX İyileştirmeleri
- [x] **Çoklu Sohbet Sekmesi**: Birden fazla kişiyle eş zamanlı sohbet (WhatsApp tarzı Inbox / Sohbet Listesi) ✅
- [x] **Kişi Listesi / Rehber**: Yerel rehber diyaloğu — kişi listesi, isme göre arama, parmak izi kopyalama, sohbet açma, kişi silme (masaüstü + web) ✅ *(favoriler henüz yok)*
- [ ] **Bildirim Sistemi**: Masaüstü / mobil push bildirimleri *(kısmen: yeni mesaj sesi var (masaüstü + web); işletim sistemi bildirimi yok)*
- [x] **Tema Seçimi**: Açık/koyu mod — ayarlardan canlı geçiş, cihazda kalıcı; tüm renkler `desktop/theme.py` paletinde (masaüstü); web'de kenar çubuğu düğmesi + `styles.css` belirteçleri (web) ✅ *(özel renk temaları henüz yok)*
- [x] **Profil Fotoğrafı / Avatar**: E2EE — her kişiye kendi anahtarıyla şifreli + imzalı (`avatar_update`), sunucu göremez; gelen resim 128px JPEG'e yeniden kodlanır, EXIF atılır (masaüstü + web; web'de canvas ile) ✅
- [x] **Mesaj Tarih Ayracı**: Gün bazında mesaj gruplama — "Bugün" / "Dün" / "12 Haziran" / "12 Haziran 2025" (masaüstü + web; web mesajları zaman damgasına göre sıralar) ✅
- [x] **Link Önizleme**: gönderen çeker, alıcıya şifreli + mesaja bağlı imzalı gönderir (`encrypted_preview`); alıcı siteye bağlanmaz, sunucu URL'yi görmez; yerel ağ adresleri reddedilir; ayarlardan kapatılabilir (masaüstü üretir + gösterir; web yalnızca gösterir — tarayıcı CORS yüzünden siteyi okuyamaz) ✅
- [x] **Ses ve Titreşim**: Yeni mesaj/dosya geldiğinde bildirim sesi — sounddevice ile üretilen çift ton, harici ses dosyası gerektirmez, arama sırasında susar (masaüstü; web'de WebAudio ile aynı ton, kenar çubuğundan kapatılabilir) ✅ *(titreşim mobil özelliği, kapsam dışı)*

### 🌐 Ağ ve Altyapı
- [x] **TLS/HTTPS**: Caddy ile iki mod — alan adı + Let's Encrypt ya da yalnızca IP + kendinden imzalı sertifika; masaüstü https/wss + SHA-256 parmak izi sabitleme (`desktop/tls_pin.py`) ✅ *(Docker'da doğrulama bekliyor — KNOWN_ISSUES §8)*
- [x] **Docker Compose**: `docker compose up -d --build` — app + Caddy + isteğe bağlı coturn (`turn` profili); Cloudflare Tunnel demosu (`docker-compose.demo.yml`); rehber `DEPLOY.md` ✅ *(Docker'da doğrulama bekliyor — KNOWN_ISSUES §8)*
- [x] **PWA**: kurulabilir web uygulaması (manifest, ikonlar, önce-ağ service worker) ✅ *(gerçek tarayıcı testi bekliyor)*
- [x] **STUN/TURN seçimi**: Google STUN aç/kapa (`HYBRIDP2P_PUBLIC_STUN`), kendi coturn'ünüz, kısa ömürlü TURN kimliği ✅
- [x] ~~**JWT Kimlik Doğrulama**~~: gerek kalmadı — her REST isteği RSA-PSS ile imzalanıyor (X-Signature), WebSocket challenge-response ile doğrulanıyor; sunucuda oturum/token durumu yok
- [x] **NAT Traversal (STUN/TURN)**: Farklı ağlardaki cihazlar arası doğrudan bağlantı — STUN (Google ya da kendi coturn'ünüz), TURN aktarımı (coturn profili, kısa ömürlü kimlik) ✅
- [ ] **Çoklu Sunucu (Federation)**: Farklı sunuculardaki kullanıcılar arası mesajlaşma (Matrix protokolü gibi)
- [ ] **Tor/Onion Routing**: Anonim bağlantı desteği
- [ ] **Tamamen Sunucusuz P2P Modu**: Manuel SDP (QR Kod/Metin) ve BitTorrent DHT sinyalleşme ile sıfır sunucu iletişimi *(kısmen: masaüstünde imzalı tek kullanımlık kodlarla doğrudan yazılı mesajlaşma ve arama (S1) ✅, dosya aktarımı (S1b) ✅, sunucu olmadan başlatma — giriş ekranında "Sunucusuz başlat" ve `serverless_client.py` (S2) ✅; telefon/tarayıcı için tek dosyalık serverless.html (S3) ✅; STUN seçimi — Google/Cloudflare/özel STUN-TURN/yalnızca yerel ağ (S4) ✅; sayfada sesli/görüntülü arama (S3b) ✅; sayfayı saran Android uygulaması (`android/`, debug imzalı) ✅; DHT bekliyor)*

### 📱 Platform Desteği
- [ ] **Android APK Derleme**: Flet ile Android paketleme
- [ ] **iOS IPA Derleme**: Flet ile iOS paketleme
- [x] ~~**Web Versiyonu**: Flet web hedefi~~: yerine bağımsız HTML/JS web istemcisi (`static/`) yapıldı — masaüstüyle özellik paritesi, PWA olarak kurulabilir ✅
- [x] **Masaüstü İnstaller**: Windows (.exe), macOS (.dmg), Linux (.deb) paketleme (.exe derlendi) ✅
- [ ] **Çoklu Cihaz Senkronizasyonu**: Aynı hesabı birden fazla cihazda kullanma

### 📊 İzleme ve Yönetim
- [ ] **Admin Paneli**: Sunucu durumunu, kullanıcı sayısını ve mesaj istatistiklerini gösteren web arayüzü
- [ ] **Loglama**: Yapılandırılmış log çıktısı (JSON format, log seviyeleri)
- [ ] **Metrikler**: Prometheus/Grafana ile sunucu performans izleme
- [x] **Sağlık Kontrolü (Health Check)**: `/health` endpoint'i (uptime, DB durumu) ✅

---

## 🏗️ Öncelik Sıralaması (Önerim)

Eğer projeye devam etmek istersen, şu sırayla ilerlemeni öneririm:

| Oncelik | Ozellik | Neden? |
|---------|---------|--------|
| ✅ Tamam | Yerel Mesaj Gecmisi | Tamamlandi |
| ✅ Tamam | Ephemeral Mod | Tamamlandi — iki tarafli, offline sync |
| ✅ Tamam | Tek Gorunumlu Mesaj | Tamamlandi — 10s countdown, kayit yok |
| ✅ Tamam | Dosya/Resim Gonderimi | Tamamlandi — E2EE, inline resim |
| ✅ Tamam | Grup Sohbeti | E2EE paylasimli simetrik anahtar ve rekeying |
| ✅ | TLS/HTTPS | Caddy, iki mod + parmak izi sabitleme |
| Orta | Private Key Sifrelemesi | Cihaz guvenligi |
| Orta | Mesaj Imzalama | Gonderen kimlik dogrulama |
| ✅ | Docker Compose | DEPLOY.md |
| Dusuk | Mobil Paketleme | Flet zaten destekliyor |

---

## 📝 Notlar

- Sunucu `0.0.0.0:8000` adresinde çalışır, LAN üzerinden erişilebilir
- FastAPI otomatik API dokümantasyonu: `http://127.0.0.1:8000/docs`
- Anahtarlar `~/.hybridp2p_messenger/{username}/` klasöründe saklanır
- Sunucu veritabanı: proje dizininde `relay_server.db` (SQLite)
