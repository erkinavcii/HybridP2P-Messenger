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
- [ ] **Signal Protokolü (Double Ratchet)**: Her mesajda yeni anahtar türetme → tam Forward Secrecy. Bir anahtar ele geçirilse bile geçmiş/gelecek mesajlar korunur
- [ ] **Anahtar Doğrulama (Key Verification)**: QR kod veya güvenlik numarası ile karşı tarafın anahtarını yüz yüze doğrulama (MITM koruması)
- [ ] **Private Key Şifreleme**: Yerel private key'i kullanıcı parolası ile AES şifreleme (cihaz çalınsa bile anahtar güvende)
- [ ] **Mesaj İmzalama (Digital Signature)**: RSA-PSS ile her mesaja dijital imza → gönderici kimlik doğrulama
- [ ] **Anahtar Yenileme (Key Rotation)**: Belirli aralıklarla otomatik yeni anahtar çifti üretme ve dağıtma
- [ ] **Sunucu Tarafı Rate Limiting**: Brute-force ve spam saldırılarına karşı istek sınırlama

### 💬 Mesajlaşma Özellikleri
- [x] **Tek Gorunumlu Mesaj (View-Once)** — per-mesaj toggle, 10s countdown, hic kaydedilmez ✅
- [x] **Dosya/Resim Gonderimi** — AES-256-GCM sifreleme, sunucu Zero-Knowledge, inline resim ✅
- [ ] **Sesli Mesaj**: Mikrofon kaydi → sifrelenmis ses dosyasi gonderimi
- [x] **Grup Sohbeti**: Birden fazla aliciya sifreli simetrik mesaj (Shared Group Key + Rekeying) ✅
- [ ] **Mesaj Duzenleme/Silme**: Gonderilenin her iki taraftan silinmesi
- [x] **Okundu Bilgisi (Read Receipt)**: Mesajin alici tarafindan okunup okunmadigi (masaüstü & web tarafında çift yeşil tik) ✅
- [ ] **Yaziyor... Gostergesi**: Karsi tarafin yazma durumu
- [x] **Mesaj Arama**: Yerel gecmiste arama (Sohbet ve Mesaj Gövdesi Arama) ✅

### 🗄️ Veri Yönetimi
- [x] **Yerel Mesaj Gecmisi (SQLite)** — istemci tarafinda message_store.py ✅
- [ ] **Yedekleme/Geri Yukleme**: Sifreli mesaj gecmisini disa aktarma ve geri yukleme
- [ ] **PostgreSQL Gecisi**: Uretim ortami icin SQLite yerine PostgreSQL (sunucu tarafi)
- [ ] **Redis Pub/Sub**: Cok sunuculu dagitik mimari icin mesaj kuyrugu
- [ ] **Mesaj TTL (Time-to-Live)**: Teslim edilmemis mesajlarin belirli sure sonra otomatik silinmesi

### 🎨 Arayüz ve UX İyileştirmeleri
- [x] **Çoklu Sohbet Sekmesi**: Birden fazla kişiyle eş zamanlı sohbet (WhatsApp tarzı Inbox / Sohbet Listesi) ✅
- [ ] **Kişi Listesi / Rehber**: Kayıtlı kullanıcılar arasında arama ve favoriler
- [ ] **Bildirim Sistemi**: Masaüstü / mobil push bildirimleri
- [ ] **Tema Seçimi**: Açık/koyu mod geçişi + özel renk temaları
- [ ] **Profil Fotoğrafı / Avatar**: Kullanıcı fotoğrafı ekleme
- [ ] **Mesaj Tarih Ayracı**: Gün bazında mesaj gruplama ("Bugün", "Dün")
- [ ] **Link Önizleme**: URL paylaşıldığında başlık ve küçük resim gösterimi
- [ ] **Ses ve Titreşim**: Yeni mesaj geldiğinde bildirim sesi

### 🌐 Ağ ve Altyapı
- [ ] **TLS/HTTPS**: Sunucu iletişimini SSL sertifikası ile şifreleme (transit encryption)
- [ ] **Docker Compose**: Sunucu + veritabanı için tek komutla dağıtım
- [ ] **JWT Kimlik Doğrulama**: REST API için token tabanlı yetkilendirme
- [ ] **NAT Traversal (STUN/TURN)**: Farklı ağlardaki cihazlar arası doğrudan bağlantı
- [ ] **Çoklu Sunucu (Federation)**: Farklı sunuculardaki kullanıcılar arası mesajlaşma (Matrix protokolü gibi)
- [ ] **Tor/Onion Routing**: Anonim bağlantı desteği
- [ ] **Tamamen Sunucusuz P2P Modu**: Manuel SDP (QR Kod/Metin) ve BitTorrent DHT sinyalleşme ile sıfır sunucu iletişimi

### 📱 Platform Desteği
- [ ] **Android APK Derleme**: Flet ile Android paketleme
- [ ] **iOS IPA Derleme**: Flet ile iOS paketleme
- [ ] **Web Versiyonu**: Flet web hedefi ile tarayıcıda çalışma
- [x] **Masaüstü İnstaller**: Windows (.exe), macOS (.dmg), Linux (.deb) paketleme (.exe derlendi) ✅
- [ ] **Çoklu Cihaz Senkronizasyonu**: Aynı hesabı birden fazla cihazda kullanma

### 📊 İzleme ve Yönetim
- [ ] **Admin Paneli**: Sunucu durumunu, kullanıcı sayısını ve mesaj istatistiklerini gösteren web arayüzü
- [ ] **Loglama**: Yapılandırılmış log çıktısı (JSON format, log seviyeleri)
- [ ] **Metrikler**: Prometheus/Grafana ile sunucu performans izleme
- [ ] **Sağlık Kontrolü (Health Check)**: `/health` endpoint'i (uptime, DB durumu)

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
| Yuksek | TLS/HTTPS | Transit sifreleme — Phase 2 |
| Orta | Private Key Sifrelemesi | Cihaz guvenligi |
| Orta | Mesaj Imzalama | Gonderen kimlik dogrulama |
| Dusuk | Docker Compose | Dagilim kolayligi |
| Dusuk | Mobil Paketleme | Flet zaten destekliyor |

---

## 📝 Notlar

- Sunucu `0.0.0.0:8000` adresinde çalışır, LAN üzerinden erişilebilir
- FastAPI otomatik API dokümantasyonu: `http://127.0.0.1:8000/docs`
- Anahtarlar `~/.hybridp2p_messenger/{username}/` klasöründe saklanır
- Sunucu veritabanı: proje dizininde `relay_server.db` (SQLite)
