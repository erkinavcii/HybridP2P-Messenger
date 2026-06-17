# 🤖 Agent Görev ve Takip Kılavuzu (agents.md)

Bu dosya, HybridP2P-Messenger projesinde çalışan yapay zeka programlama asistanlarının (Antigravity vb.) hedefleri, mevcut durumu ve yapılacak işleri tutarlı bir şekilde takip etmesini sağlamak amacıyla oluşturulmuştur.

---

## 🎯 Proje Hedefi & Mimari Prensipler
1. **Zero-Knowledge & E2EE:** Sunucu mesajları, dosyaları veya özel anahtarları (private key) asla düz metin (plaintext) olarak göremez. Şifreleme tamamen cihaz/tarayıcı tarafında gerçekleşir.
2. **Hybrid / Fallback Yapı:** Gerçek zamanlı işlemler için WebSocket kullanılır; WebSocket bağlantısı koptuğunda sistem otomatik olarak REST API fallback moduna geçer.
3. **Pure P2P (Sunucusuz Arama):** Merkezi sunucu kapalıyken dahi STUN üzerinden doğrudan cihazlar arası (WebRTC) sesli/görüntülü görüşme.

---

## 🚦 Güncel Durum ve İlerleme

### 🟢 Tamamlanan Görevler (Gitedildi)
- [x] **UI Thread Safety:** `client.py` Flet uygulamasındaki arka plan güncellemeleri `run_on_ui` ve `page.run_task` ile sarmalanarak Win32 takılma (freezing) sorunları çözüldü.
- [x] **Web Client IndexedDB:** `static/index.html` üzerinde LocalStorage yerine IndexedDB'ye geçildi; grup metadata'sı korundu ve view-once mesajların yenileme sonrası sızması engellendi.
- [x] **API /api/ice_servers Güvenliği:** `server.py` üzerinde isteklerin imza doğrulama (X-Signature) ile doğrulanması sağlandı; `TURN_SECRET` parametresine bağlı dinamik, süreli TURN şifresi üretimi eklendi.
- [x] **Pure P2P Entegrasyonu:** Hem masaüstü hem de web istemcisine zlib/deflate sıkıştırmalı Base64 SDP kopyala-yapıştır ile sunucusuz araba özelliği entegre edildi.

---

## 📋 Yol Haritası ve Yapılacak İşler (Backlog)

### 1. Standalone / Basit Sunucusuz Uygulama (Yeni Karar)
*Masaüstü ve web uygulamalarında P2P kodunu entegre ettik ancak bunu tamamen bağımsız, sunucu gerektirmeyen bağımsız tek bir script/sayfa olarak da sunacağız.*
- [ ] **`serverless_client.py`:** Masaüstü için sadece manual SDP kopyala-yapıştır ile sesli/görüntülü P2P görüşme yapabilen, sunucuya kaydolma gerektirmeyen minimalist Python scripti.
- [ ] **`static/serverless.html`:** Web tarayıcısı için sadece getUserMedia + RTCPeerConnection ile çalışan, sunucu bağlantısı veya IndexedDB gerektirmeyen tek sayfalık HTML/JS P2P arayüzü.

### 2. Sinyalleşme ve NAT Optimizasyonları (Faz E.2 & E.3)
- [ ] **BitTorrent DHT Prototipi:** `serverless_client.py` içerisinde oda ismi/parolası hash'i üzerinden infohash arayarak otomatik P2P buluşma (rendezvous) prototipi.
- [ ] **Dinamik Kalite Adaptasyonu (VoIP Polish):** `getStats()` verisine göre zayıf bağlantılarda çözünürlüğü ve kare hızını dinamik düşürme (örn. 720p@30fps -> 360p@15fps), ses önceliğini yüksek tutma.

### 3. Eksik UX & Güvenlik Özellikleri
- [ ] **Özel Anahtar Şifreleme (Private Key Encryption):** Yerel cihazdaki private key'lerin kullanıcı şifresiyle şifrelenip PEM olarak diske yazılması.
- [ ] **Mesaj İmzalama (Digital Signature):** Gönderilen her mesajın RSA-PSS ile imzalanması ve alıcının gönderen kimliğini doğrulaması.

---

## 🛠️ Geliştirme Notları & Hatırlatmalar
- **Vanilla CSS:** Web uygulamasında harici CSS kütüphaneleri (Tailwind vb.) yerine `index.html` içindeki özelleştirilmiş Vanilla CSS kullanılacaktır.
- **Backwards Compatibility:** WebSocket payload yapıları ve API imza formatları değiştirilirken eski istemcilerin çökmeyeceğinden emin olunmalıdır.
- **Git Push:** Çalışmalar bittiğinde veya stabil bir noktaya gelindiğinde değişiklikler `git push` ile uzak depoya gönderilmelidir.
