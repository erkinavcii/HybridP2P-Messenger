# 🔍 Proje İncelemesi (Claude Fable 5)

> **Tarih:** 2026-07-09
> **Kapsam:** Genel mimari, güvenlik iddiaları vs. gerçek durum, kod kalitesi.

---

## Genel İzlenim

Bir hobi/öğrenme projesi olarak ortalamanın çok üzerinde: mimari düşünülmüş, dokümantasyon ([README.md](README.md), [KNOWN_ISSUES.md](KNOWN_ISSUES.md), [progress.md](progress.md)) ciddi projelerden daha iyi durumda, sunucu tarafı temiz modüllere ayrılmış (`server/routes/`, `auth.py`, `websocket_manager.py`). Challenge-response WebSocket kimlik doğrulaması, imzalı REST istekleri, sunucunun `sender` alanını ezmesi (spoofing önlemi), grup üyelik kontrolü, TOFU + MITM uyarısı, grup rekeying protokolü — bunların hepsi doğru düşünülmüş şeyler. Temel özellik seti (E2EE mesaj, dosya, grup, ephemeral, view-once, okundu bilgisi, VoIP) amaç açısından **büyük ölçüde tamam**.

Ama projenin iddiası "network-level surveillance ve sunucu ele geçirilmesine dayanıklılık" olduğu için, çıtayı o iddiaya göre koyarak eleştiriyorum. Orada önemli boşluklar var.

---

## Güvenlik: İddia ile Gerçek Arasındaki Boşluklar

### 1. Forward Secrecy yok (en büyük kripto eksiği)
Her mesajın AES anahtarı alıcının *uzun ömürlü* RSA anahtarıyla sarılıyor ([crypto_utils.py:182](crypto_utils.py:182)). Trafiği pasif olarak kaydeden bir saldırgan, günün birinde cihazdan `private_key.pem`'i ele geçirirse **geçmiş tüm mesajları** çözebilir. "Network-level surveillance'a dayanıklılık" iddiasıyla doğrudan çelişiyor. Roadmap'te Double Ratchet zaten var ve 1 numaralı öncelik bu olmalı.

### 2. Birebir mesajlar imzasız
Grup mesajları RSA-PSS ile imzalanıyor ama 1:1 mesajlarda imza yok (roadmap'te "Mesaj İmzalama" hâlâ boş kutucuk). Sunucu `sender`'ı ezdiği için *başka bir kullanıcı* spoofing yapamaz, ama **ele geçirilmiş sunucunun kendisi** herhangi birinin adına sahte DM enjekte edebilir. Gizlilik korunuyor, özgünlük (authenticity) korunmuyor.

### 3. Metadata sunucuya ve ağa tamamen açık
README "read receipt'ler sunucunun metadata toplamasını engelliyor" diyor ama [main.py:374](server/main.py:374)'te `read_receipt` paketi kimin kimi ne zaman okuduğunu düz metin taşıyor — sunucu bunu görüyor. Ayrıca TLS olmadan `X-Username` header'ı, `/ws/{username}` URL'i ve tüm yönlendirme bilgisi ağda düz metin akıyor. İçerik E2EE ama **kim-kiminle-ne zaman** grafiği tamamen açık. README'nin "TLS olmasa da güvenli" tonu bu yüzden yanıltıcı; TLS'i "opsiyonel iyileştirme" değil zorunluluk olarak konumlandırmak gerekiyor.

### 4. Replay penceresi
[auth.py](server/auth.py) 5 dakikalık timestamp toleransı kullanıyor ama nonce/jti kaydı tutmuyor — yakalanan imzalı bir istek 5 dakika boyunca aynen tekrar oynatılabilir (örn. aynı dosyayı tekrar indirme denemesi, `ephemeral_toggle` tekrarı). Görülen imzaların kısa süreli cache'i bunu kapatır.

### 5. Masaüstünde private key diskte şifresiz
[crypto_utils.py:67](crypto_utils.py:67)'de kodda not olarak zaten yazılmış. Web tarafında parola korumasını yapmış; aynısını desktop'a getirmek düşük maliyetli, yüksek getirili bir iş.

### Doğru Yapılanlar
Bunların dışında kripto kullanımı doğru görünüyor: OAEP + SHA-256, GCM'de mesaj başına taze anahtar + rastgele 96-bit nonce, PSS imzalar, dosya indirme yetki kontrolü ([messages.py:196](server/routes/messages.py:196)) hepsi yerinde.

---

## Kod Kalitesi

- **[client.py](client.py) 4.372 satırlık tek dosya.** Sunucu güzelce `server/` paketine bölünmüş; aynı tedaviyi istemci hak ediyor (ui ekranları / ws istemcisi / voip / rest istemcisi gibi). Şu anki en büyük bakım yükü bu.
- **threading + asyncio karışımı** UI donmalarının kökü — bu KNOWN_ISSUES'ta zaten mükemmel teşhis edilmiş. `threading.Thread` hâlâ ~15 yerde; Çözüm B (hedefli `page.run_task`) planını uygulayıp thread'leri kademeli emekli etmek isabetli olur.
- **[main.py](server/main.py)'deki WebSocket handler'ı ~350 satırlık dev bir if/elif zinciri.** Mesaj tipi → handler fonksiyonu dispatch tablosuna dönüştürmek okunabilirliği ciddi artırır.
- **Test kapsamı zayıf:** iki test dosyası var ama gruplar, rekeying, imza doğrulama, replay, VoIP sinyalleşmesi test edilmiyor. Güvenlik iddialı bir projede en çok test edilmesi gereken yerler tam da bunlar.
- Küçük not: `dist/HybridP2P-Messenger.exe` gitignore'dan önce commit'lenmiş, hâlâ git'te izleniyor ve geçmişi şişiriyor (`git rm --cached` ile çıkarılabilir).

---

## Önerilen Öncelik Sırası

1. **TLS'i zorunlu varsayım yap** + README'deki "TLS'siz de güvenli" ve "read receipt metadata'sı gizli" ifadelerini düzelt (dokümantasyonun dürüstlüğü güvenlik projesinde özelliktir).
2. **1:1 mesajlara RSA-PSS imza** — grup mesajlarında altyapı zaten var, taşıması ucuz.
3. **Desktop private key'i parola ile şifrele** (`BestAvailableEncryption`).
4. **client.py'yi modülerleştir + threading→async geçişi** (KNOWN_ISSUES'taki plan hazır).
5. **Replay nonce cache'i** (küçük iş).
6. **Double Ratchet / forward secrecy** — en büyük iş ama projeyi "gerçek" Signal-sınıfı yapan adım bu. Ara adım olarak X25519 tabanlı ephemeral key agreement bile büyük kazanım olur.

---

## Sonuç

Temel sağlam, özellik seti amaca göre tamam sayılır; asıl mesele artık *yeni özellik* değil, güvenlik iddialarını gerçeğe eşitlemek ve istemci tarafındaki teknik borcu ödemek.
