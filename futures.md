# 🛣️ HybridP2P Messenger — Gelecek Özellikler (Futures Roadmap)

> Bu dosya, MVP sonrası eklenmesi planlanan özelliklerin detaylı spesifikasyonlarını içerir.
> Her özellik için kullanıcı akışı, teknik yaklaşım ve bağımlılıklar belgelenmiştir.

---

## 📦 Özellik 1: Yerel Mesaj Geçmişi

### Ne İstiyoruz?
Uygulama kapansa bile mesajlar kaybolmasın. Chat bazında "kaydet / kaydetme" seçeneği olsun. Sunucuya gönderilen mesajlar zaten şifreli blob — yerel geçmiş de aynı şekilde cihazda şifreli saklanacak.

### Kullanıcı Akışı
```
Sohbet Ayarları (⚙️ ikonu, chat başlığında):
  ┌─────────────────────────────────┐
  │  Bu sohbet için mesaj geçmişi   │
  │  ○ Yerel kaydet (varsayılan)    │  ← şifreli SQLite'a yaz
  │  ○ Kaydetme (ephemeral mod)     │  ← bellekte tut, kapanınca git
  └─────────────────────────────────┘
```

### Teknik Yaklaşım
- **İstemci tarafı SQLite** (`~/.hybridp2p_messenger/{username}/messages.db`)
- Tablo: `messages(id, chat_id, sender, plaintext_encrypted, timestamp, is_view_once, is_read)`
- `plaintext_encrypted`: Mesaj düz metin → kullanıcının kendi **symmetric key'i** ile AES-GCM şifrelenir (bu key cihazda, uygulama parolasından türetilir)
- Chat başlarken `local_history` flag'i `True/False` olarak saklanır

### Önemli Detay
Sunucu zaten Zero-Knowledge — yerel geçmiş de öyle olmalı. Plaintext asla diske ham yazılmaz. Bir "uygulama şifresi" / PIN girilirse bundan anahtar türetilir (PBKDF2), o anahtar mesajları şifreler.

### Bağımlılıklar
- Faz 1 MVP tamamlandı ✅
- Uygulama PIN/şifre sistemi (basit versiyon: sabit bir PIN, gelişmiş: OS keychain)

---

## 👁️ Özellik 2: Tek Görünümlü Mesaj (View-Once)

### Ne İstiyoruz?
WhatsApp'taki gibi: Belirli bir mesajı gönderirken "bu mesaj bir kez görüntülensin, sonra silinsin" diyebilmek. **Chat ayarı ne olursa olsun** (local kaydet aktif bile olsa) o tekil mesaj view-once olabilsin.

### Kullanıcı Akışı
```
Mesaj giriş alanı, gönder butonunun yanında 👁️ ikonu:
  
  [  Mesajı yaz...              ] [👁️] [➤]
                                    ↑
                             Tıklayınca toggle:
                             🔵 Tek görünümlü AÇIK
                             ⚪ Normal gönderim

Alıcı tarafında:
  ┌──────────────────────────────┐
  │  👁️ Tek görünümlü mesaj      │
  │  [Görüntülemek için dokun]   │
  └──────────────────────────────┘
  
  → Dokunulunca açılır, 10 saniye sonra veya uygulama
    arka plana alınınca otomatik silinir.
```

### Teknik Yaklaşım
**Şifreleme paketi değişmez** — sadece metadata eklenir:
```json
{
  "encrypted_aes_key": "...",
  "nonce": "...",
  "ciphertext": "...",
  "view_once": true       ← bu flag eklenir
}
```

**Alıcı tarafı davranışı:**
1. `view_once: true` gelen mesaj → yerel geçmişe **kaydedilmez** (chat'in genel ayarından bağımsız)
2. Kullanıcı "görüntüle" dediğinde plaintext RAM'de tutulur, ekranda gösterilir
3. `X` saniye sonra (ayarlanabilir: 10s, 30s, veya ekrandan çıkınca) widget temizlenir, RAM'den silinir
4. Alıcı başarıyla görüntülediğinde sunucuya `view_once_ack` gönderilir → gönderici bilgilendirilir

**Ekran görüntüsü koruması:**
- Flet'te Android/iOS tarafında `FLAG_SECURE` desteği (ekran görüntüsü engelleme) — mobil paketlemede aktif edilecek
- Masaüstünde teknik engel yok ama sosyal caydırıcı: "Bu mesaj görüntülendi" bildirimi

### Önemli Detay
Sunucu view_once flag'ini görebilir (şifreli payload içinde değil, header'da) ama bu sorun değil — zaten mesajın içeriğini göremez. Flag sadece routing davranışını etkiler.

### Bağımlılıklar
- Özellik 1 (Yerel Mesaj Geçmişi) tamamlanmış olmalı — çünkü "yerel geçmişe yazma" mantığına entegre edilecek

---

## 📎 Özellik 3: Dosya ve Resim Gönderimi

### Ne İstiyoruz?
Resim, PDF, video gibi dosyaları uçtan uca şifrelenmiş olarak gönderebilmek. Mesaj baloncuğunda resimler inline görünsün, diğer dosyalar indirme linki olarak gösterilsin.

### Kullanıcı Akışı
```
Mesaj alanının yanında 📎 ikonu → dosya seçici açılır
  
Gönderici:
  [📎] → Dosya seç → Önizleme → [Gönder]
  
  Gönderim sırasında:
  ████████░░ %80  "foto.jpg şifreleniyor..."
  
Alıcı tarafında (resim):
  ┌────────────────┐
  │  [resim thumb] │  ← inline küçük resim
  │  📷 foto.jpg   │
  │  2.4 MB        │
  └────────────────┘
  
Alıcı tarafında (diğer dosya):
  ┌──────────────────────────────┐
  │  📄 rapor.pdf   1.2 MB  [⬇] │
  └──────────────────────────────┘
```

### Teknik Yaklaşım
Dosyalar da **hibrit şifreleme** ile şifrelenir, mesajlarla aynı prensip:

```
1. Dosya içeriği → AES-256-GCM ile şifrelenir (chunk'lar halinde, büyük dosya için)
2. AES key → Alıcının RSA public key'i ile şifrelenir
3. Şifreli dosya → Sunucuya upload edilir (binary blob olarak, S3-like endpoint)
4. Sunucu dosyayı saklar → Alıcıya "dosya hazır" WebSocket bildirimi
5. Alıcı → Download → AES key'i private key ile çözer → Dosyayı çözer → Gösterir
```

**Sunucu tarafı yeni endpoint'ler:**
```
POST /api/upload_file      → Şifreli dosyayı al, UUID ver
GET  /api/download_file/{uuid} → Şifreli blob'u ver
DELETE /api/file/{uuid}    → Dosya teslim edilince sil (Zero-Knowledge)
```

**Boyut limitleri (önerim):**
- Resim: max 10 MB
- Dosya: max 50 MB  
- Video: max 100 MB (ilerisi için stream şifreleme gerekir)

**Thumbnail:**
- Resim gönderilirken gönderici tarafında küçük thumbnail oluşturulur
- Thumbnail da ayrıca şifrelenerek mesaj paketine eklenir (hızlı önizleme için)

### Bağımlılıklar
- Sunucu tarafında dosya depolama (başlangıç: lokal disk, ilerisi: MinIO/S3)
- `Pillow` kütüphanesi (thumbnail üretimi için)
- `python-multipart` (FastAPI dosya upload için)

---

## 👥 Özellik 4: Grup Sohbeti

### Ne İstiyoruz?
3+ kişinin aynı kanalda konuşabilmesi. E2EE korunurken — yani sunucu grup mesajlarını da okuyamasın.

### Kullanıcı Akışı
```
Ana ekranda [+ Grup Oluştur] butonu:
  
  Grup Adı: [Aile Grubu          ]
  Üyeler:   [kardeş] [anne] [+ekle]
  [Oluştur]

Grup chat'i normal chat gibi görünür, sadece
sol üstte grup ikonu ve üye sayısı gösterilir.
```

### Teknik Yaklaşım — E2EE Grup Şifrelemesi

Bu en karmaşık özellik. İki yaklaşım var:

**Yaklaşım A — Basit (N kez şifreleme):**
```
Gönderici, her mesajı her üye için ayrı ayrı şifreler:
  mesaj → [Alice key'iyle şifrele] + [Bob key'iyle şifrele] + [Charlie key'iyle şifrele]
  
Sunucuya 3 ayrı şifreli paket gider, her biri sadece o kişi açabilir.
```
- ✅ Basit, mevcut kod üzerine inşa edilebilir
- ❌ 10 kişilik grupta mesaj 10x büyür, ağır

**Yaklaşım B — Sender Keys (Signal'in Grup Protokolü):**
```
Grup oluşturulunca bir "Grup Anahtarı" üretilir.
Bu grup anahtarı her üyeye RSA ile ayrı ayrı iletilir.
Sonraki tüm mesajlar bu tek grup anahtarıyla şifrelenir.
Üye ayrılırsa grup anahtarı yenilenir (re-keying).
```
- ✅ Verimli, mesaj sadece 1x şifrelenir
- ✅ Signal, WhatsApp'ın kullandığı protokol
- ❌ Daha karmaşık implementasyon

**Öneri:** Başlangıç için Yaklaşım A (küçük grup, az üye varsayımı), ilerisi için B.

**Sunucu tarafı yeni tablolar:**
```sql
groups (group_id, group_name, created_by, created_at)
group_members (group_id, username, joined_at)
```

**Yeni endpoint'ler:**
```
POST /api/group/create         → Grup oluştur
POST /api/group/{id}/add       → Üye ekle  
POST /api/group/{id}/leave     → Gruptan çık
GET  /api/group/{id}/members   → Üye listesi (public key'leriyle)
POST /api/group/{id}/send      → Grup mesajı gönder (offline depolama ile)
```

### Bağımlılıklar
- Özellik 1 (Yerel Mesaj Geçmişi) — grup mesaj geçmişi için
- Özellik 3 (Dosya Gönderimi) — grupta da dosya paylaşımı için

---

## 📋 Öncelik Sırası ve Bağımlılık Haritası

```
MVP ✅
  │
  ├──► Özellik 1: Yerel Mesaj Geçmişi  [Bağımsız, ilk yapılacak]
  │         │
  │         ├──► Özellik 2: Tek Görünümlü Mesaj  [Özellik 1'e bağlı]
  │         │
  │         └──► Özellik 4: Grup Sohbeti  [Özellik 1'e bağlı]
  │                   │
  ├──► Özellik 3: Dosya/Resim Gönderimi  [Bağımsız, paralel yapılabilir]
  │         │
  │         └──► Özellik 4 ile birleşir (grupta dosya paylaşımı)
  │
  └──► Özellik 5: E2EE Sesli Arama (VoIP)  [Bağımsız, UDP/Sinyalleşme gerekir]
```

| # | Özellik | Karmaşıklık | Etki | Bağımlılık |
|---|---------|-------------|------|------------|
| 1 | Yerel Mesaj Geçmişi | Orta | Yüksek | — |
| 2 | Tek Görünümlü Mesaj | Düşük | Yüksek | Özellik 1 |
| 3 | Dosya/Resim Gönderimi | Yüksek | Yüksek | — |
| 4 | Grup Sohbeti (Yaklaşım A) | Orta | Çok Yüksek | Özellik 1 |
| 5 | E2EE Sesli Arama (VoIP) | Çok Yüksek | Kritik | MVP / Sinyalleşme |

---

## 📞 Özellik 5: Uçtan Uca Şifreli Sesli Arama (E2EE VoIP)

### Ne İstiyoruz?
İki kullanıcı arasında tamamen uçtan uca şifrelenmiş (E2EE) ve gerçek zamanlı (real-time) sesli arama (VoIP) yapabilmek. Görüşme verileri sunucuya uğramadan doğrudan peer-to-peer (P2P) UDP akışı şeklinde iletilecektir. Sunucu görüşme içeriğini asla dinleyemez veya çözemez.

### Kullanıcı Akışı
```
Sohbet Ekranı üst barında Arama [📞] ikonu:

  Alice [📞] ikonuna tıklar ➔ "Calling Bob..." arama ekranı açılır (çalma sesi).
  
  Bob'un ekranında popup / arama penceresi açılır:
    ┌─────────────────────────────────┐
    │  Incoming Voice Call            │
    │  Alice is calling...            │
    │  [Decline (🔴)]   [Accept (🟢)]  │
    └─────────────────────────────────┘
    
  Bob Kabul ederse (🟢):
    ➔ Arama başlar, arama süresi sayacı gösterilir.
    ➔ Mikrofon / Hoparlör ikonları ile ses kontrol edilir.
    ➔ "End Call" butonu aramayı kapatır.
```

### Teknik Yaklaşım

Sesli aramanın performansı ve güvenliği için aşağıdaki mimari kurulacaktır:

1. **Uçtan Uca Şifreleme (ECDH Key Exchange)**:
   * Arama kurulduğu an Alice ve Bob, geçici (ephemeral) bir Diffie-Hellman veya Elliptic-Curve Diffie-Hellman (ECDH) anahtar değişimi yapar.
   * Bu değişim, mevcut E2EE sinyalleşme kanalımız (WebSocket/REST) üzerinden güvenli bir şekilde aktarılır ve tarafların kalıcı RSA anahtarları ile imzalanır.
   * Taraflar ortak bir simetrik ses anahtarı (AES-256) türetir. Ses paketleri asimetrik (RSA) değil, simetrik (AES) olarak şifrelenir.

2. **Peer-to-Peer Ses Akışı (WebRTC & NAT Traversal)**:
   * Gerçek zamanlı ses için UDP protokolü tercih edilir. Gecikmeyi önlemek için P2P bağlantı esastır.
   * **STUN/TURN**: Güvenlik duvarlarını aşmak (NAT traversal / UDP hole punching) için STUN sunucuları kullanılacaktır. P2P kurulamayan çok kısıtlı ağlarda TURN sunucusu üzerinden şifreli röle yapılır (sunucu veriyi çözemez, sadece iletir).
   * İstemcide Python WebRTC implementasyonu için `aiortc` kütüphanesi veya `PyAudio` (Ses yakalama) + `opuslib` (Ses sıkıştırma) + `cryptography` ile özel UDP socket motoru entegre edilebilir.

3. **Sinyalleşme Protokolü**:
   * Arama istekleri (OFFER/ANSWER) ve ağ adres adayları (ICE Candidates), röle sunucumuz (`server.py`) üzerindeki WebSocket kanalı aracılığıyla takas edilir.

### Bağımlılıklar
- Sinyalleşme için WebSocket sunucu altyapısı ✅
- `aiortc` veya `PyAudio` + `opuslib` + `cryptography`
- Genel STUN/TURN sunucu adresleri (örneğin Google public STUN sunucuları)

---

## 🔖 Notlar

- Tüm özellikler Zero-Knowledge prensibini koruyacak — sunucu hiçbir zaman plaintext veya özel anahtar görmeyecek
- View-once mesajlar yerel geçmişe hiç yazılmaz, grup üyelerinde de aynı kural
- Dosya upload'ları teslim sonrası sunucudan silinir (offline_msgs ile aynı prensip)
- Grup anahtarı yönetimi (re-keying) ileri aşama için ayrı bir mini-protokol gerektirir
