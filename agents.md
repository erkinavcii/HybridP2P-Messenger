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

---

## 5) 📋 YOL HARİTASI VE YAPILACAK İŞLER (BACKLOG)

### 5.1. Standalone / Basit Sunucusuz Uygulama (P2P Standalone)
*Masaüstü ve web uygulamalarında P2P kodunu entegre ettik ancak bunu tamamen bağımsız, sunucu gerektirmeyen bağımsız tek bir script/sayfa olarak da sunacağız.*
- [ ] **`serverless_client.py`:** Masaüstü için sadece manual SDP kopyala-yapıştır ile sesli/görüntülü P2P görüşme yapabilen, sunucuya kaydolma gerektirmeyen minimalist Python scripti.
- [ ] **`static/serverless.html`:** Web tarayıcısı için sadece getUserMedia + RTCPeerConnection ile çalışan, sunucu bağlantısı veya IndexedDB gerektirmeyen tek sayfalık HTML/JS P2P arayüzü.

### 5.2. Modülerleştirme ve Refactoring (Modularization & Refactoring)
*Tek dosyada biriken ve boyutu aşırı büyüyen web istemcisi (`static/index.html` — 4400+ satır) ve röle sunucusu (`server.py` — 1800+ satır) dosyalarının daha temiz, okunabilir ve yönetilebilir modüllere ayrılması.*
- [x] **Web İstemcisi Modülerleştirme:** CSS dosyalarının `static/css/styles.css` olarak dışarı aktarılması ve Javascript kısımlarının `db.js`, `crypto.js`, `ws.js`, `voip.js`, `ui.js`, `app.js` şeklinde ES6 modüllerine bölünmesi.
- [ ] **Sunucu Modülerleştirme:** APIRouter kullanılarak `users.py`, `messages.py`, `groups.py`, `voip.py` olarak ayrılması ve `main.py`, `config.py`, `database.py` şeklinde paket yapısına kavuşturulması.

### 5.3. Sinyalleşme ve NAT Optimizasyonları
- [ ] **BitTorrent DHT Prototipi:** `serverless_client.py` içerisinde oda ismi/parolası hash'i üzerinden infohash arayarak otomatik P2P buluşma (rendezvous) prototipi.
- [ ] **Dinamik Kalite Adaptasyonu (VoIP Polish):** `getStats()` verisine göre zayıf bağlantılarda çözünürlüğü ve kare hızını dinamik düşürme (örn. 720p@30fps -> 360p@15fps), ses önceliğini yüksek tutma.

### 5.4. Eksik UX & Güvenlik Özellikleri
- [ ] **Özel Anahtar Şifreleme (Private Key Encryption):** Yerel cihazdaki private key'lerin kullanıcı şifresiyle şifrelenip PEM olarak diske yazılması.
- [ ] **Mesaj İmzalama (Digital Signature):** Gönderilen her mesajın RSA-PSS ile imzalanması ve alıcının gönderen kimliğini doğrulaması.

---

## 6) 🛠️ GELİŞTİRME NOTLARI & HATIRLATMALAR
- **Vanilla CSS:** Web uygulamasında harici CSS kütüphaneleri (Tailwind vb.) yerine `index.html` içindeki özelleştirilmiş Vanilla CSS kullanılacaktır.
- **Backwards Compatibility:** WebSocket payload yapıları ve API imza formatları değiştirilirken eski istemcilerin çökmeyeceğinden emin olunmalıdır.
