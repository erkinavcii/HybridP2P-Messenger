# 🔍 Proje İncelemesi (Claude Fable 5.2)

> **Tarih:** 2026-11-12 (önceki inceleme: 2026-07-09)
> **İncelenen sürüm:** `d2d2ba8` — 250 otomatik test geçiyor.
> **Kapsam:** Sunucu (`server/`), şifreleme (`crypto_utils.py`), yerel depo (`message_store.py`), masaüstü (`desktop/`), web istemcisi (`static/js/`), sunucusuz sayfa (`static/serverless.html`), Android (`android/`), depo düzeni.
> **Yöntem:** Kod okuması. Aşağıdaki bulgular kodda doğrulandı ama **saldırı olarak çalıştırılıp denenmedi**; her birinin yanında dosya ve satır var. Bu inceleme hiçbir kodu değiştirmedi.

---

> **Durum güncellemesi (2026-11-13):** K1–K4 düzeltildi: K3 `b11b4fd`, K4 `da9a079` + `a420c3d`, K1 `d4aa8fb`, K2 `c052d21` + `c05cc8f`. Tam test paketi 321 test ile geçiyor. Aşağıdaki §2 düzeltmelerden **önceki** durumu anlatır; tarihsel kayıt olarak olduğu gibi bırakıldı. Düzeltmelerin ayrıntısı ve kalan işler: KNOWN_ISSUES.md §12. Düzeltmeler elle (masaüstü arayüzü, gerçek tarayıcı) denenmedi; doğrulama otomatik testlerle.

---

## 1. Özet

Temmuz'dan bu yana proje çok ilerledi: birebir mesaj imzası, modüler masaüstü, testler (2 dosyadan 250 teste), dağıtım dosyaları, sunucusuz mod, telefon sayfası ve APK eklendi. Sunucusuz taraf (`p2p_core`, `p2p_files`, `serverless.html`) projenin en dikkatli yazılmış kısmı: imzalar bağlam etiketli, girdiler sınırlı, dosya adları temizleniyor, Python ve JS karşılıklı test ediliyor.

Asıl sorun şu: **o özen, daha eski olan sunuculu yola henüz taşınmamış.** Aşağıdaki dört kritik bulgunun dördü de sunuculu moddadır ve üçü, projenin temel iddiasını ("sunucu ele geçirilse bile içerik ve kimlik güvende") doğrudan zayıflatır. Hepsinin düzeltmesi küçüktür.

| # | Bulgu | Etki | Düzeltme büyüklüğü | Durum |
|---|---|---|---|---|
| K1 | İstemci, sunucunun gönderdiği metni körlemesine imzalıyor | Sunucu sizin adınıza mesaj ve sunucusuz bağlantı kodu ürettirebilir | Çok küçük | ✅ Düzeltildi (`d4aa8fb`) |
| K2 | Grup anahtarı dağıtımı imzasız, gönderen doğrulanmıyor | Sunucu (ya da grup kimliğini bilen herhangi bir kullanıcı) grubun anahtarını değiştirip sonraki mesajları okuyabilir | Küçük | ✅ Düzeltildi (`c052d21`, `c05cc8f`) |
| K3 | Masaüstü, gelen dosyayı gönderenin verdiği adla diske yazıyor | Dosya İndirilenler dışına, örn. Başlangıç klasörüne yazdırılabilir | Çok küçük | ✅ Düzeltildi (`b11b4fd`) |
| K4 | Web istemcisinde iki XSS yolu | Bir kişi (ya da sunucu) tarayıcıdaki özel anahtarı çalabilir | Küçük | ✅ Düzeltildi (`da9a079`, `a420c3d`) |

---

## 2. Kritik bulgular

### K1 — Sunucu, istemciye istediği metni imzalatabiliyor
WebSocket girişinde sunucu bir "challenge" gönderir, istemci bunu kimlik anahtarıyla imzalar. İstemci metnin biçimine hiç bakmıyor: [desktop/ws_client.py:71](desktop/ws_client.py:71), [static/js/ws.js:292](static/js/ws.js:292).

Aynı anahtar başka imzalar için de kullanılıyor ve bunların biçimi düz metin:
- Birebir mesaj: `gönderen:alıcı:şifreli_paket` ([desktop/ws_client.py:413](desktop/ws_client.py:413))
- Grup mesajı: `gönderen:grup_id:şifreli_paket`
- Sunucusuz bağlantı kodu: `hybridp2p-p2p:v2:…`

Ele geçirilmiş bir sunucu challenge olarak `alice:bob:<kendi ürettiği şifreli paket>` gönderirse Alice'in istemcisi bunu imzalar; sunucu da Bob'a "Alice'ten, imzası geçerli" bir mesaj teslim eder. Daha kötüsü, aynı yolla Alice adına imzalı bir **sunucusuz bağlantı kodu** ürettirip, rehberinde Alice olan herkese "kimlik doğrulandı" rozetiyle Alice gibi görünebilir. Bu, birebir imzanın ve sunucusuz kimlik doğrulamanın koruduğu şeyin tam kendisidir.

**Düzeltme:**
1. Hemen (protokol değişmeden): istemci yalnızca UUID biçimindeki challenge'ı imzalasın, başka her şeyi reddetsin. UUID hiçbir başka imza biçimiyle çakışamaz.
2. Kalıcı: her imza türüne ayrı önek (`hybridp2p-auth:v1:`, `dm:v1:`, `group:v1:` …). Sunucusuz kodlar bunu zaten yapıyor. Eski istemciler için bir geçiş dönemi gerekir (AGENTS.md §6).

### K2 — Grup anahtarı herkes tarafından değiştirilebiliyor
`group_key_dist` paketi geldiğinde istemci içindeki anahtarı çözüp o grubun anahtarı olarak kaydediyor; gönderenin kim olduğuna, grupta olup olmadığına ya da bir imzaya bakmıyor: [desktop/ws_client.py:137](desktop/ws_client.py:137), [static/js/ws.js:495](static/js/ws.js:495). Sunucu da bu paketi üyelik kontrolü yapmadan iletiyor: [server/main.py:385](server/main.py:385).

Sonuç: sunucu tüm üyelere kendi seçtiği anahtarı dağıtırsa, sonraki grup mesajları sunucunun bildiği anahtarla şifrelenir. Mesajlar hâlâ imzalı görünür, ama gizlilik kalmaz. Grup kimliğini bilen herhangi bir kayıtlı kullanıcı da aynısını yapabilir (ya da anahtarı bozup grubu kullanılmaz hâle getirebilir).

**Düzeltme:** Dağıtım paketini imzala (`gkd:gönderen:alıcı:grup_id:paket`); istemci yalnızca grubun kurucusundan ya da daha önce doğruladığı bir üyeden geleni kabul etsin ve anahtar değişimini sohbette görünür bir sistem mesajıyla bildirsin. Sunucu tarafında gönderenin üyeliğini kontrol et.

### K3 — Masaüstünde dosya adı üzerinden klasör dışına yazma
Sunucu üzerinden gelen dosya şu şekilde kaydediliyor: `dest = downloads / original_name` — [desktop/bubbles.py:385](desktop/bubbles.py:385) ve [desktop/bubbles.py:414](desktop/bubbles.py:414). `original_name` gönderenden (ve aradaki sunucudan) gelir ve hiç temizlenmez. `..\..\AppData\…\Startup\x.bat` gibi bir ad ya da tam bir yol verilirse dosya İndirilenler'in dışına yazılır; aynı adlı mevcut dosyanın üzerine de sessizce yazılır. Kullanıcının dosya balonundaki indirme düğmesine basması yeterli.

Ek olarak dosya mesajları imzasız ([server/main.py:316](server/main.py:316)): sunucu, alıcının açık anahtarıyla istediği dosyayı şifreleyip herhangi bir kişi adına gönderebilir.

**Düzeltme:** Sunucusuz modda zaten var olan `p2p_files.safe_filename` ve "çakışırsa yeni ad" mantığını burada da kullan; dosyaları `Downloads/HybridP2P` altına yaz. Dosya mesajına da imza ekle (dosya kimliği + ad + şifreli verinin özeti).

### K4 — Web istemcisinde XSS (iki yol)
Sayfanın CSP'si satır içi betiğe izin veriyor ve her adrese bağlantıya açık ([static/index.html:7](static/index.html:7)), bu yüzden aşağıdakiler gerçek kod çalıştırmaya dönüşür; özel anahtar tarayıcıda durduğu için çalınabilir.

1. **Gelen kutusu önizlemesi:** Son mesajın metni kaçışlanmadan HTML'e basılıyor — [static/js/ui.js:226](static/js/ui.js:226) (`${snippet}`); arama sonuçlarında da aynısı var: [static/js/ui.js:268](static/js/ui.js:268). Mesaj gelir gelmez, tıklama gerekmeden tetiklenir. Metin 30 karakterle sınırlı; bu, saldırıyı zorlaştırır ama engellemez.
2. **Resim dosyası adı:** Dosya uzantısı doğrudan `<img src="data:image/UZANTI;…">` içine konuyor — [static/js/ui.js:758](static/js/ui.js:758), [static/js/ui.js:821](static/js/ui.js:821), basıldığı yer [static/js/ui.js:538](static/js/ui.js:538). Adı `x."onerror="…` olan bir "resim" açıldığında uzunluk sınırı olmadan kod çalışır.

Aynı dosyada `item.partner` ve grup mesajındaki `m.sender` de kaçışlanmıyor ([static/js/ui.js:584](static/js/ui.js:584)); bunlar bugün sunucunun kullanıcı adı kuralına güveniyor.

**Düzeltme:** Kullanıcıdan gelen her şeyi `textContent` ile ya da `escapeHtml` ile bas; uzantıyı sabit bir listeden (png, jpeg, gif, webp) seç. Ardından CSP'yi sıkılaştır: satır içi `onclick`'leri kaldırıp `'unsafe-inline'`'ı sil, `connect-src`'yi `'self'` ile sınırla. `serverless.html` bunu zaten doğru yapıyor.

---

## 3. Diğer hatalar

### Sunucu
| # | Hata | Yer | Not |
|---|---|---|---|
| S1 | Başkasının sohbet ayarı sızıyor | [server/routes/messages.py:161](server/routes/messages.py:161) | `LIKE 'ali_%'` sorgusunda `_` joker karakterdir: `ali`, `alice` ile `bob` arasındaki geçici mod kaydını da görür. Ayrıca `a_b`+`c` ile `a`+`b_c` aynı `chat_id`'yi üretir ([:45](server/routes/messages.py:45)). Ayırıcıyı kullanıcı adında olamayacak bir karakter yap (ör. `\|`) ve tam eşleşmeyle sorgula. |
| S2 | Arama bitmeden bağlantısı kopanın karşı tarafı "meşgul" kalıyor | [server/websocket_manager.py:35](server/websocket_manager.py:35) | `disconnect` yalnızca kopan kullanıcıyı `active_calls`'tan siliyor. Diğeri yeniden bağlanana kadar aranamaz ve aramanın bittiği ona bildirilmez. `end_call(call_id)` çağrılmalı ve karşıya `call_end` gönderilmeli. |
| S3 | REST ile mesaj çekerken mesaj kaybolabilir | [server/routes/messages.py:594](server/routes/messages.py:594) | Okuma ile silme arasında gelen mesaj da silinir. WebSocket yolundaki gibi kimlik (id) listesiyle silinmeli. |
| S4 | Kuyruk ve dosya deposu sınırsız | [server/routes/messages.py:50](server/routes/messages.py:50), [:233](server/routes/messages.py:233) | WebSocket yolunda alıcının var olup olmadığına bakılmıyor, kullanıcı başına sınır ve süre sonu yok; indirilmeyen dosyalar sonsuza dek duruyor. Tek bir kullanıcı diski doldurabilir. WebSocket mesajlarında hız sınırı da yok ([server/main.py:276](server/main.py:276)). |
| S5 | İmzalı istek 5 dakika boyunca tekrar oynatılabilir | [server/auth.py](server/auth.py) | Temmuz incelemesinden beri açık. Görülen imzaların kısa süreli kaydı yeter. |
| S6 | Zaman aşımı hatası yanlış kodla dönüyor | [server/auth.py:16](server/auth.py:16) | 401 fırlatılıp hemen altındaki `except Exception` ile 400 "geçersiz format"a çevriliyor. |
| S7 | Grup yönetimi gevşek | [server/routes/groups.py](server/routes/groups.py) | Her üye herkesi ekleyebiliyor; eklenen kullanıcının var olduğu denetlenmiyor (SQLite'ta yabancı anahtar denetimi kapalı); gruptan çıkan kurucu erişimini koruyor; grup adı/kimliği için uzunluk sınırı yok. |
| S8 | Her istekte yeni veritabanı bağlantısı, WAL kapalı | [server/database.py:80](server/database.py:80) | Yük altında "database is locked" riski. WAL + `busy_timeout` + tek paylaşılan bağlantı. |

### Masaüstü
| # | Hata | Yer | Not |
|---|---|---|---|
| M1 | 200'den uzun sohbette en yeni mesajlar görünmüyor | [message_store.py:325](message_store.py:325) | `ORDER BY timestamp ASC LIMIT 200` en **eski** 200 mesajı getirir. En yeni 200'ü alıp ters çevirmek ve yukarı kaydırınca devamını yüklemek gerekir. |
| M2 | `group_` ile başlayan kullanıcı adı grup sanılıyor | [message_store.py:146](message_store.py:146) | Kullanıcı adı kuralı `group_x` adına izin veriyor. Ya bu öneki kayıtta yasakla ya da grup bilgisini addan çıkarma. |
| M3 | Kişi kartı içe aktarma mevcut anahtarın üzerine sessizce yazıyor | [desktop/chat_screen.py:406](desktop/chat_screen.py:406) | Anahtar değişimi uyarısı atlanıyor. Sunucusuz ekrandaki içe aktarma bunu doğru yapıyor; aynı kontrol buraya gelmeli. |
| M4 | Kişi güncellenince "imzalıyor" bayrağı ve avatar siliniyor | [message_store.py:483](message_store.py:483) | `INSERT OR REPLACE` satırı baştan yazar; imzasız mesaja karşı koruma sıfırlanır. `ON CONFLICT DO UPDATE` kullanılmalı. |
| M5 | Mesaj sırası gönderenin saatine bağlı | [message_store.py:339](message_store.py:339) | Sıralama, gönderenin verdiği zaman damgası metnine göre. Saati yanlış bir cihaz sohbeti karıştırır; yerel sıra numarasıyla sırala, saati yalnızca göster. |
| M6 | Hatalar sessizce yutuluyor | `desktop/` genelinde ~48 `except Exception:` + 35 `except…pass` | Arama medyasındaki üç eski hata (KNOWN_ISSUES §10) aylarca bu yüzden görünmedi. En azından günlüğe yazılmalı. |

### Doğrulanmamış, bakılmaya değer
- Geçici (ephemeral) mod anahtarı imzasız ve sunucuda tutuluyor: sunucu modu kapatıp mesajların diske yazılmasını sağlayabilir.
- Okundu bilgisi ve "yazıyor" imzasız; sunucu sahte okundu üretebilir (etkisi küçük).

---

## 4. Temmuz incelemesindeki maddelerin durumu

| Madde | Durum |
|---|---|
| Birebir mesajlar imzasız | ✅ Yapıldı — K1 kapatıldı (2026-11-13), imza artık sunucuya karşı da koruyor; imza biçimlerine önek eklenmesi açık (aşağıdaki "Kalan işler") |
| `client.py` tek dev dosya | ✅ `desktop/` paketine bölündü |
| Test kapsamı zayıf | ✅ 250 test; ancak K1–K4 gibi "kötü niyetli sunucu / kötü niyetli kişi" senaryoları test edilmiyor |
| TLS zorunlu varsayım | ✅ Dağıtım dosyaları ve parmak izi sabitleme var; Docker'da hiç çalıştırılmadı (KNOWN_ISSUES §8) |
| Tekrar oynatma (replay) | ❌ Açık (S5) |
| Özel anahtar diskte şifresiz | ⏸ Bilerek ertelendi; yayından önce şart |
| Forward secrecy | ⏸ Bilerek ertelendi |
| Meta veri sunucuya açık | ❌ Açık; mimari gereği kısmen kaçınılmaz, README'de açık yazılmalı |
| WebSocket işleyicisi dev `if/elif` | ❌ Hâlâ ~350 satır ve REST yedek yolunda birebir kopyası var ([server/routes/messages.py:360](server/routes/messages.py:360)) |
| threading + asyncio karışımı | ◐ Azaldı; `desktop/`'ta hâlâ 26 `threading.Thread` |

---

## 5. Geliştirilebilecek yerler

**Güvenlik mimarisi**
- **Tek bir "imzalı zarf":** Mesaj, dosya, grup anahtarı, avatar, düzenleme, geçici mod — hepsi aynı önekli, sürümlü biçimle imzalansın ve tek yerde doğrulansın. K1, K2 ve dosya imzası eksikliği aynı kökten geliyor: her özellik kendi biçimini uydurmuş.
- **Düşman senaryosu testleri:** "Sunucu şu paketi uydurursa istemci reddeder mi?" türünde testler. Bugünkü testler çoğunlukla doğru çalışan yolu sınıyor.
- **Şifrelemeye bağlam (AAD) ekle:** AES-GCM çağrılarında gönderen/alıcı/grup bilgisi ek veri olarak verilmiyor ([crypto_utils.py:178](crypto_utils.py:178)).
- **Web CSP'sini `serverless.html` düzeyine çıkar.**

**Kod yapısı**
- WebSocket ve REST yedek yolunu tek bir "tür → işleyici" tablosunda birleştir; şu an her yeni mesaj türü üç yere eklenmek zorunda ve biri unutulursa mesaj sessizce kaybolur.
- Dosyalar base64 içinde base64 olarak taşınıyor ve SQLite'a metin olarak yazılıyor (~1,8 kat boyut, tamamı bellekte). İkili yükleme ve diskte dosya olarak saklama, 10 MB sınırını da rahatlatır.
- Kalite kuralı üç yerde kopya (Python, `quality.js`, sayfa içi). Test eşitliği koruyor ama sayfa içi kopya derleme adımıyla üretilebilir.

**Depo düzeni**
- CI yok: her push'ta `pytest` ve Android derlemesi çalıştıran bir GitHub Actions iş akışı, testlerin değerini katlar.
- Kökteki `test_features.py` ve `test_integration.py` eski, elle çalıştırılan betikler; `pytest` bunları görmüyor. Taşı ya da sil.
- Belgeler dağınık ve çakışıyor: `futures.md`, `futuresplanning.md` (848 satır), `progress.md`, `KNOWN_ISSUES.md`, `AGENTS.md`. Yol haritası tek dosyada toplanmalı.
- Bağımlılıklar yalnızca alt sınırla (`>=`) tanımlı; PyAV sürüm değişikliği arama medyasını aylarca bozmuştu. Kilit dosyası (ör. `pip-compile`) ekle.
- Arayüz dili karışık (Türkçe/İngilizce aynı ekranda). Metinleri tek sözlükte toplamak hem tutarlılık hem çeviri sağlar.

**Henüz gerçek ortamda denenmeyenler** (kod var, kanıt yok): Docker dağıtımı, PWA kurulumu, gerçek mikrofon/kamerayla masaüstü araması, gerçek telefonda APK ve QR okuma.

---

## 6. Yeni özellik önerileri

Öncelik, projenin amacına (sansüre dayanıklı, kendi sunucunda ya da sunucusuz iletişim) katkıya göre sıralı.

1. **QR'ı resim olarak paylaşma ve resimden okuma.** Planı hazır, onay bekliyor. Yan yana olmayan iki kişinin kodu WhatsApp vb. üzerinden iletebilmesi için gerekli.
2. **Davetle kayıt / kapalı sunucu.** Bugün sunucuya ulaşabilen herkes kaydolabiliyor ve tüm kullanıcı listesini görebiliyor ([server/routes/users.py:149](server/routes/users.py:149)). Arkadaş grubu senaryosu için davet kodu ve "kayıt kapalı" ayarı.
3. **Güvenlik numarası karşılaştırma ekranı.** İki tarafın parmak izinden türetilen tek bir kısa sayı ya da QR; "doğrulandı" işareti. Bugün 64 karakterlik iki parmak izini gözle karşılaştırmak gerekiyor.
4. **Sunucusuz modda yeniden bağlanma.** Bir kez doğrulanmış iki cihaz için, sonraki bağlantıda uzun kod yerine kısa bir kod ya da (yol haritasındaki) DHT buluşması. Şu an her bağlantıda iki uzun kod değiş tokuşu şart.
5. **Özel anahtarın parolayla şifrelenmesi + anahtar yedeği.** Yayın öncesi zorunlu; yanına "kurtarma dosyası" (parolayla şifreli anahtar + rehber) eklenirse cihaz değiştirmek de çözülür.
6. **Çoklu cihaz.** Aynı kimliği telefon ve bilgisayarda kullanmak bugün anahtar dosyasını elle taşımayı gerektiriyor; mesajlar yalnızca tek cihaza ulaşıyor.
7. **İleri gizlilik (forward secrecy).** Tam Double Ratchet yerine ara adım: oturum başına X25519 anahtar değişimi. Uzun vadeli anahtar çalınsa bile geçmiş trafik çözülemez.
8. **Kaybolan mesajlar (süreli).** Geçici mod aç/kapa yerine "24 saat sonra sil" gibi süre; sunucu kuyruğu için de süre sonu (S4'ü de çözer).
9. **Grup iyileştirmeleri.** Yönetici rolü, üye çıkınca otomatik anahtar yenileme bildirimi, grupta dosya ve düzenleme/silme (bugün yalnızca birebirde).
10. **Bildirimler.** Masaüstünde sistem bildirimi; Android uygulamasında görüşme sırasında ön plan hizmeti (arka planda bağlantının kesilmemesi için).
11. **Sunucusuz modda görüşme sırasında yazışma ve dosya.** Arama bağlantısına veri kanalı eklemek yeterli; masaüstü ve sayfa aynı anda güncellenmeli.
12. **Engelleme ve sessize alma.** Kayıtlı herkes herkese yazabiliyor; istemci tarafı engel listesi basit ve etkili.
13. **Tor / vekil desteği.** Sansür senaryosunda sunucuya erişim ve link önizleme için (KNOWN_ISSUES §7'de de geçiyor).

---

## 7. Önerilen sıra

1. ✅ **K1 → K3 → K4 → K2.** Yapıldı (2026-11-13); her biri için "saldırı reddediliyor" testi yazıldı. Kalan işler aşağıda.
2. **M1** (yeni mesajların görünmemesi) ve **S2** (aranamama) — kullanıcının doğrudan karşılaşacağı hatalar.
3. **S1, S3, S4, M3, M4** — veri sızıntısı, kayıp ve kötüye kullanım.
4. **CI** — bundan sonraki her düzeltmeyi korur.
5. **Tek imzalı zarf + işleyici tablosu** — K1/K2 türü hataların yeniden doğmasını önleyen yapısal iş.
6. Yeni özellikler: önce QR resmi (1), davetle kayıt (2), güvenlik numarası (3).
7. Yayından önce: özel anahtar şifreleme, yayın imzalı APK, Docker ve gerçek cihaz testleri.

---

## Kalan işler (K1–K4 sonrası, 2026-11-13)

Düzeltmeler sırasında ortaya çıkan ya da bilerek açık bırakılan işler. Ayrıntı: KNOWN_ISSUES.md §12.

- **K2 tekrar oynatma:** Grup anahtarı paketinin imzalı metninde zaman damgası ya da nonce yok; kötü niyetli bir sunucu aynı göndericinin eski ama geçerli imzalı bir paketini yeniden oynatıp bir üyeyi eski anahtara döndürebilir.
- **K2 üyelik ve yetki:** Alıcılar kendi üye listesini tutmuyor (çıkarılmış üyenin geçerli imzalı paketi dürüst olmayan bir sunucudan geçebilir); her üye anahtarı yenileyebiliyor, "yalnızca kurucu" kuralı açık bir karar.
- **Dosya mesajları imzasız:** K3 yalnızca dosya adını düzeltti; sunucu hâlâ bir kişi adına dosya uydurabilir. Tek görünümlük dosya penceresi de hâlâ "Downloads folder" diyor (gerçek klasör `Downloads/HybridP2P`).
- **İmza öneki:** Birebir (`gönderen:alıcı:paket`) ve grup (`gönderen:grup_id:paket`) imzalarında bağlam öneki yok; K1 yalnızca UUID kuralıyla kapatıldı. Önek için eski istemcilerle geçiş dönemi gerekir.
- **`'unsafe-inline'`:** Web CSP'sinde `script-src` içinde duruyor, çünkü satır içi `onclick` işleyicileri duruyor. Ayrıca K4 gerçek tarayıcıda denenmedi.
- **Eski istemci uyumu (K2):** Eski istemcilerin imzasız anahtar yenilemeleri artık reddediliyor; yeni istemci eski sunucuya bağlıyken de imza alanı kaybolduğu için yenilemeler reddedilir.

---

## Sonuç

Özellik seti amaca göre fazlasıyla yeterli ve sunucusuz taraf sağlam. Eksik olan yeni özellik değil: sunuculu modun, sunucusuz modda zaten uygulanmış olan "karşı tarafa ve sunucuya güvenme" disiplinine getirilmesi. Dört kritik bulgu kapatıldığında projenin README'deki iddiası ile kodu arasındaki en büyük fark kapanmış olur.
