# Bilinen Sorunlar ve Çözüm Önerileri (Known Issues & Proposed Solutions)

Bu dosya, HybridP2P-Messenger projesindeki bilinen kararsızlıkları, Flet ve Flutter mimarisinden kaynaklanan arayüz güncellenmeme sorunlarını ve bunlara yönelik çözüm önerilerini içermektedir.

---

## 1. Flet Arayüzünün Arka Plan Thread'lerinde Güncellenmemesi (UI Freezing)

### Sorun Açıklaması
İstemci (`client.py`) çalışırken, bazı arayüz güncellemeleri ekranda anında görünmemektedir. Kullanıcının arayüze tıklaması, fareyi oynatması veya pencere odağını değiştirmesi durumunda arayüz aniden güncellenmektedir. Bu durum iki ana senaryoda kendini göstermektedir:
1. **Giriş Ekranı Takılması:** Giriş yap butonuna basıldıktan sonra arka planda giriş işlemleri tamamlanır ve `show_inbox_screen()` çağrılır. Ancak arayüz "Signing in..." aşamasında takılı kalır. Kullanıcı ekrana tıklayana veya pencereyi odağa alana kadar gelen kutusu (inbox) ekranına geçiş yapılmaz.
2. **Arama Sayacının Durması:** Sesli/görüntülü arama bağlandıktan sonra süre sayacı (`00:01`, `00:02` vb.) arka planda ilerlese de ekranda güncellenmez. Kullanıcı odağı başka bir pencereye kaydırıp geri geldiğinde süre güncellenir ancak tekrar sabit kalır.

### Kök Neden Analizi (Root Cause)
Flet, Python ve Flutter arasında bir WebSocket köprüsü kurarak çalışır. Windows masaüstü ortamında Flutter, Win32 pencere mesaj döngüsünü (message pump) kullanır.
* Flet arayüzünde bir buton tıklaması gibi olaylar doğrudan Flet'in ana event loop'unda çalışır ve `page.update()` çağrıldığında Win32 mesaj döngüsü tetiklenerek ekran yeniden çizilir.
* Ancak, `client.py` içerisindeki giriş işlem (`do_login`) ayrı bir `threading.Thread` içinde, arama sayacı döngüsü (`_call_timer_loop`) ise WebSocket dinleyicisinin event loop'u olan `ws_loop` üzerinde çalışmaktadır.
* Bu arka plan thread veya harici event loop'lardan doğrudan `page.update()` çağrıldığında, güncelleme verileri kuyruğa eklenir fakat Flutter'ın Win32 penceresine "yeniden çizim (repaint)" sinyali gönderilmez. Bu yüzden arayüz, kullanıcı bir işletim sistemi olayı (tıklama, odaklanma vb.) tetikleyene kadar eski halinde kalır.

### Etkilenen Kod Satırları (Güncel: `desktop/` paketi — bkz. Bölüm 4)
* **Giriş İşlemi:** `do_login` artık `desktop/login_screen.py` içinde (`LoginScreenMixin.on_login_click`), `threading.Thread` ile başlatılıyor ve `page.run_task(login_success_ui)` üzerinden `show_inbox_screen()`'e geçiyor.
* **Arama Sayacı:** `_call_timer_loop` artık `desktop/call_screen.py` içinde (`CallScreenMixin._call_timer_loop`), her adımda `run_on_ui(...)` ile sarmalı çalışıyor.
* **Durum:** Bu iki nokta da **zaten düzeltilmiş** halde bulundu — 2026-07-22'deki modülerleştirme sırasında yapılan tam kod taramasında hem `do_login`/`login_success_ui` hem `_call_timer_loop`'un `page.run_task`/`run_on_ui` ile doğru şekilde sarmalandığı doğrulandı. Aşağıdaki çözüm önerileri artık tarihsel referans niteliğindedir.
* **Diğer Potansiyel Noktalar:**
  - [x] `do_download` (Dosya indirme thread'i) — `desktop/bubbles.py`
  - [x] `do_rest` (API istekleri thread'i) — `desktop/ws_client.py`
  - [x] `do_file_upload_and_send` (Dosya yükleme thread'i) — `desktop/chat_screen.py`
  - [x] `do_rekey` / `do_create` / `do_leave` (Grup işlemleri thread'leri) — `desktop/inbox_screen.py`
  - [x] `check_recipient_status_loop` (Kullanıcı durum kontrolü) — `desktop/chat_screen.py`
  - [x] `_update_ephemeral_ui` — **2026-07-22'de bulunan ve düzeltilen canlı bir örnek**: bu fonksiyon `page.update()`'i doğrudan çağırıyordu ve `sync_chat_settings` üzerinden `background_sync` thread'inden (arka plan thread'i) tetiklenebiliyordu — aynı donma sınıfının belgelenmemiş bir örneğiydi. `desktop/chat_logic.py`'de `run_on_ui(...)` ile sarmalanarak düzeltildi.

### Çözüm Önerileri ve Strateji

#### Çözüm Önerisi A: Güvenli Arayüz Güncelleme Fonksiyonu (`safe_update`)
Flet'in `page.run_task(coroutine)` metodu, verilen coroutine'i Flet'in kendi ana event loop'unda thread-safe olarak çalıştırır ve Win32 mesaj döngüsünü tetikler. Arka plan işlemlerindeki `page.update()` çağrılarını bu yöntemle sarmalayabiliriz.

```python
def safe_update():
    async def _update():
        page.update()
    page.run_task(_update)
```
* **Artıları:** Mevcut kodu minimum ölçüde değiştirir. `page.update()` çağrılarını doğrudan `safe_update()` ile değiştirerek hızlıca uygulanabilir.
* **Eksileri:** Bu çözüm sadece arayüzün "çizilmesini" (rendering) Flet loop'una taşır. Ancak UI durumunu (state) değiştiren asıl mantık hâlâ arka plan thread'inde çalışmaya devam eder. Bu durum, özellikle karmaşık ekran geçişlerinde yarış durumu (race condition) veya thread-safety riskleri doğurabilir.

#### Çözüm Önerisi B: Hedefli `page.run_task` Kullanımı (Tavsiye Edilen Asıl Yaklaşım)
Arayüz bileşenlerini güncelleyen ve ardından sayfa yenilemesi tetikleyen mantık bloklarını (logic) bir bütün olarak Flet'in kendi event loop'unda çalıştırmaktır.

* **Giriş ekranı geçişi için:**
  ```python
  async def transition_to_inbox():
      show_inbox_screen()
      sync_chat_settings()
      sync_user_groups_from_server()
      fetch_offline_messages()
      start_websocket_listener()
  page.run_task(transition_to_inbox)
  ```
* **Arama sayacı için:**
  ```python
  async def update_timer_ui(val: str):
      call_timer_text.value = val
      page.update()
  
  # Sayaç döngüsünde (ws_loop/arka planda):
  page.run_task(update_timer_ui, f"{mins:02d}:{secs:02d}")
  ```
* **Artıları:** Hem veri durum mutasyonları (state changes) hem de render tetiklemeleri Flet'in kendi event loop'unda senkronize şekilde yürütülür. Race condition olasılığını sıfıra indirir.

---

### İmplementasyon ve Yol Haritası (Karma Strateji)

Tüm `page.update()` çağrılarını körü körüne tek bir yönteme dönüştürmek yerine, veri ve geçiş karmaşıklığına göre iki yöntemi bir arada kullanmak en sağlıklı yaklaşımdır:

1. **Periyodik ve Basit UI Güncellemeleri (Sayaç vb.):**
   * Durum (state) oldukça basittir (sadece süre string'i güncellenir). Bu nedenle **Çözüm A (safe_update)** veya basit bir **hedefli run_task** yeterlidir.
2. **Karmaşık Ekran ve Durum Geçişleri (Giriş Yapma vb.):**
   * Birden fazla kontrolün durumu değişir, yeni ekran eklenir, eski ekran temizlenir. Sıralama ve thread-safety kritik olduğundan **Çözüm B (Hedefli run_task)** kullanılması zorunludur.
3. **Diğer Arka Plan İşlemleri (`do_download`, `do_rest` vb.):**
   * UI bileşeni oluşturulup/güncellenip hemen ardından `update()` çağrılıyorsa, arayüzün tutarlılığı için **Çözüm B** ile event loop'a taşınmalıdır.

#### Uygulama Adımları
1. **Adım 1:** En izole ve basit olan **Arama Sayacı** sorununu çözerek işe başlayın. Çözümü uyguladıktan sonra sayaç akışını gözlemleyin.
2. **Adım 2:** Sayacın düzgün aktığı teyit edildikten sonra, daha kritik olan **Giriş Geçişi** sorununu Çözüm B ile düzeltin.
3. **Adım 3:** Diğer arka plan işlemlerindeki `page.update()` noktalarını tek tek test ederek iyileştirin.

---

## 2. Görüntülü Aramada Kırmızı Ekran ve Image `src` Hatası

### Sorun Açıklaması
Görüntülü arama sırasında veya sonrasında Flet arayüzünde kırmızı hata ekranı belirmekte ve konsolda şu hata çıktı olarak görünmektedir:
`AssertionError: A valid src or src_base64 value must be specified.`

### Kök Neden Analizi (Root Cause)
Flet'in `ft.Image` kontrolü, serialize edilirken ya geçerli bir `src` (URL/dosya yolu) ya da `src_base64` (Base64 kodlu veri) parametresine ihtiyaç duyar.
Arama sonlandırıldığında çağrılan `cleanup_call()` fonksiyonu (Satır 3736-3739 arası) video önizleme bileşenlerini temizlemek için aşağıdaki atamaları yapmaktadır:
```python
local_video_preview.src_base64 = None
remote_video_view.src_base64 = None
```
Bu sırada `src` niteliği de boş veya tanımsız kaldığı için, Flet `page.update()` esnasında bileşen görünmez (`visible = False`) olsa dahi bileşenin durumunu doğrulamaya (validate) çalışır ve hata fırlatır.

### Çözüm Önerisi
`cleanup_call()` içinde `src_base64` değerini `None` yapmak yerine, başlangıçta tanımlanan şeffaf 1x1 piksel GIF görselini (`transparent_placeholder`) atamak bu sorunu çözer.

```python
# Örnek Çözüm:
local_video_preview.src_base64 = None
local_video_preview.src = transparent_placeholder
local_video_preview.visible = False

remote_video_view.src_base64 = None
remote_video_view.src = transparent_placeholder
remote_video_view.visible = False
```

---

## 3. WebSocket Kütüphane Sürüm Uyuşmazlığı (`websockets.open` Kaldırılması)

### Sorun Açıklaması
Yeni `websockets` (v14.0+) sürümlerinde `websockets.open` kullanımı kaldırılmıştır (deprecated). Bu durum istemcinin sunucuya bağlanırken çökmesine sebep olmaktaydı.

### Çözüm Durumu
Bu sorun `client.py` içerisinde `websockets.open` çağrısı yerine `websockets.connect` kullanılarak düzeltilmiştir. `requirements.txt` dosyasında `websockets>=13.0` olarak güncellenerek geriye dönük uyumluluk güvenceye alınmıştır.

---

## 4. `client.py` Modülerleştirmesi (2026-07-22) ve Yeni Bulgular

`client.py` (4372 satır, ~80 iç içe closure) `server/` paketiyle aynı desende bir `desktop/` paketine bölündü (bkz. README.md "File Structure" ve `agents.md` §5.2). Python'da closure'lar dosyalar arası bölünemediği için mixin+tek-sınıf yaklaşımı kullanıldı: `client.py` artık sadece `MessengerApp` sınıfını kurup `state` ve ~47 paylaşılan UI kontrolünü `self.` özniteliği olarak tanımlayan ince bir giriş noktası. Detaylı plan ve dosya haritası: `.claude/plans/reflective-splashing-leaf.md`.

Bu iş sırasında tam kod taraması yapılırken iki bulgu ortaya çıktı:

### 4.1 — Düzeltildi: `_update_ephemeral_ui`'da sarmalanmamış `page.update()`
Bölüm 1'deki not güncellendi, bkz. yukarısı.

### 4.2 — ✅ Çözüldü (2026-10-08, S1): Pure P2P ile `ws_loop` arasında yarış penceresi
Sunucusuz bağlantılar artık sunucu WebSocket'inin loop'unu (`state["ws_loop"]`) kullanmıyor; `desktop/p2p_core.py` kendi arka plan loop'unu kuruyor (`get_loop()` / `run()`). `cleanup_call()` hangi loop'ta açıldıysa (`state["call_loop"]`) bağlantıyı orada kapatıyor. Yarış penceresi ortadan kalktı ve sunucu yokken kullanım (S2) için engel kalmadı.

### 4.3 — ✅ Çözüldü (2026-10-08, S1): Pure P2P kimlik doğrulaması
Eskiden bağlantı kodları imzasızdı: kodu taşıyan kanalda araya giren biri kodu kendi koduyla değiştirip iki tarafla da (DTLS uçtan uca şifreli olsa bile) kendisi konuşabilirdi. Artık `h2` kodları gönderenin RSA kimlik anahtarıyla imzalı; imza SDP'nin tamamını (dolayısıyla DTLS parmak izini) ve cevap kodunda yanıtlanan teklifin özetini kapsıyor. Karşı taraf rehberle karşılaştırılıyor (doğrulandı / yeni / anahtar değişmiş → engel / imza geçersiz → engel). Eski `z1`/`v1` kodlar yalnızca uyarıyla ve yalnızca aramalar için kabul ediliyor. Ayrıntı: `desktop/p2p_core.py` başlığı.

**Kalan sınırlar:**
- İmzalı kod ~2,8 KB: QR'ın en büyük boyutuna ancak sığıyor, ekrandan okutmak zor olabilir; kopyala-yapıştır her zaman çalışır.
- Sunucusuz mod hâlâ herkese açık STUN kullanıyor (§9); STUN seçimi S4'te.
- Ana web istemcisi (`index.html`) sunucusuz modda `h2` kodlarını yalnızca aramalar için okuyor, kimliği doğrulamıyor. Tarayıcıda sunucusuz mesajlaşma/dosya için `serverless.html` kullanılmalı (S3: tam doğrulama, mesaj, dosya).
- `serverless.html`: sesli/görüntülü arama henüz yok (S3b). Tarayıcıda alınan/gönderilen dosya en fazla 256 MB (dosya bellekte tutulur). QR'ı kamerayla okutma yalnızca BarcodeDetector'ı olan tarayıcılarda (Android Chrome); iPhone'da kopyala/paylaş kullanılır. Sayfa dosyadan (`file://`) açıldığında Chrome bunu güvenli sayar; bazı mobil dosya yöneticileri sayfayı güvenli olmayan bir bağlamda açabilir — o durumda şifreleme API'si çalışmaz ve sayfa kimlik üretemez (gerçek telefonda test edilmedi; Android emülatöründe — Pixel, Chrome — localhost üzerinden uçtan uca doğrulandı: kimlik, imzalı kod takası, iki yönde mesaj ve dosya).
- Dosya aktarımı (S1b): yalnızca iki taraf da açıkken; yarıda kesilirse kaldığı yerden devam etmez (yeniden gönderilmeli). aiortc'nin veri kanalı hızı sınırlıdır — çok büyük dosyalar (yüzlerce MB) yavaş olabilir. Sohbet geçmişine dosyanın kendisi değil, yalnızca "gönderildi/alındı" kaydı düşer.

---

## 6. Mesaj Düzenleme/Silme — Bilinen Sınırlar (2026-10-06)

- **Eski mesajlar:** `msg_uid` özelliğinden önce gönderilmiş mesajların kararlı kimliği yok; düzenlenemez/silinemez (⋮ menüsü görünmez).
- **Ephemeral sohbetler:** mesajlar diske yazılmadığı için düzenlenecek kayıt yok; menü gösterilmez. Gelen düzenleme/silme ephemeral sohbette uygulanmaz.
- **Gruplar ve tek görünümlük mesajlar:** kapsam dışı (v1 yalnızca birebir, kalıcı metin mesajları).
- **Karşı taraf eski sürümse:** düzenleme/silme çerçevelerini yok sayar; değişiklik yalnızca sizin tarafınızda görünür.
- **"Herkesten sil" bir garanti değildir:** alıcı mesajı silinmeden önce okumuş, kopyalamış veya ekran görüntüsü almış olabilir; değiştirilmiş bir istemci silme isteğini uygulamayabilir. Bu, tüm mesajlaşma uygulamaları için geçerlidir.
- **Web istemcisi:** destekliyor (W3, 2026-10-06) — aynı protokol, masaüstüyle birlikte çalışır. Web'de mesajlar IndexedDB'deki sohbet kaydında düzenlenir/silinir.

---

## 7. Link Önizleme — Bilinen Sınırlar (2026-10-06)

- **Site gönderenin IP'sini görür:** önizlemeyi gönderen çektiği için kaçınılmaz. Ayarlardan ("Link önizleme") kapatılabilir; Tor modu gelirse çekim de onun üzerinden yapılmalı.
- **İçerik gönderenin beyanıdır:** imza önizlemenin o kişiden geldiğini kanıtlar, sayfanın gerçekten öyle olduğunu kanıtlamaz. Kötü niyetli bir kişi, linkin gittiği yerden farklı bir başlık veya resim gösterebilir. Kartta alan adı gösterilir; tıklanınca açılan adres mesajdaki URL'dir.
- **Gecikme:** önizleme hazırlanırken (en fazla ~8 sn) o mesaj ve arkasındaki birebir mesajlar sırayla bekler; sıra bozulmaz, ekranda mesaj hemen görünür.
- **Düzenleme önizlemeyi kaldırır:** düzenlenen mesajın eski önizlemesi her iki tarafta silinir, yenisi üretilmez.
- **DNS yeniden bağlama (rebinding):** adres kontrolü ile bağlantı arasında DNS yanıtı değişirse koruma teorik olarak aşılabilir. Pratikte risk düşük; tam çözüm, kontrol edilen IP'ye doğrudan bağlanmaktır.
- **Kapsam dışı:** gruplar ve tek görünümlük mesajlar.
- **Web istemcisi yalnızca gösterir:** gelen önizlemeyi doğrular, temizler ve kart olarak gösterir; kendisi üretemez (tarayıcı CORS nedeniyle başka sitenin sayfasını okuyamaz). Web'den gönderilen linkler önizlemesiz gider.
- **Sesli mesaj biçimi (web):** Chrome yalnızca WebM/Opus kaydedebildiği için web `voice-…webm` gönderir; masaüstü iki uzantıyı da tanır, eski masaüstü sürümleri `.webm`'i sıradan dosya olarak gösterir. Ogg/Opus oynatma tarayıcı desteğine bağlıdır (Chrome/Firefox/Edge oynatır; eski Safari sürümleri oynatamayabilir).

---

## 5. Birebir Mesaj İmzalama — Web Paritesi (2026-07-25, güncellendi 2026-10-06)

✅ **Kapatıldı:** web istemcisi (`static/js/`) artık birebir mesajları masaüstüyle aynı biçimde (`{sender}:{recipient}:{encrypted_payload}`, RSA-PSS) imzalıyor ve hem canlı hem çevrimdışı yolda aynı kurallarla doğruluyor. Kişi başına "imzalıyor" bayrağı IndexedDB `keys` deposunda `signs_<kullanıcı>` olarak tutuluyor.

Doğrulama hâlâ geçiş kuralıyla çalışıyor:

| Gönderenin durumu | Davranış |
|---|---|
| Geçerli imza | Kabul + kişi "imzalıyor" olarak işaretlenir |
| Geçersiz imza | **Reddedilir** + uyarı gösterilir |
| İmza yok, kişi daha önce hiç imzalamamış | Kabul (eski istemci sürümleri için) |
| İmza yok, kişi daha önce imzalamış | **Reddedilir** (downgrade saldırısı) |

### Kalan açık nokta
- **Zorunlu doğrulama:** iki istemci de imzaladığına göre "imza yoksa ve kişi hiç imzalamamışsa kabul et" kuralı kaldırılabilir. Bu, güncellenmemiş istemcilerden gelen mesajları reddetmek demek; bilinçli bir karar olarak ayrıca verilmeli.
- **Web uyarısı kalıcı değil:** masaüstü engellenen mesaj uyarısını sohbet geçmişine yazıyor; web yalnızca o an açık sohbette gösteriyor.

**Not:** Grup mesajlarında imza zaten **zorunlu** (imzasız/geçersiz grup mesajı her zaman reddediliyor).

---

## 8. Deployment — Docker ile Henüz Doğrulanmadı (2026-10-07)

**Durum: BEKLEMEDE.** Geliştirme makinesinde Docker kurulu olmadığı için aşağıdakiler yazıldı ve testlerle kısmen doğrulandı, ama **Docker'da hiç çalıştırılmadı**. Kullanıcı kararı: deployment işleri (D1–D5, PWA, belgeler) bittikten sonra Docker Desktop kurulup topluca test edilecek.

Docker olmadan doğrulananlar (pytest): üretim giriş noktası (`python server.py`), vekil arkasında gerçek istemci IP'si / sahte `X-Forwarded-For` reddi, `deploy/gen_cert.py` sertifika üretimi ve TLS el sıkışması, masaüstü https/wss + parmak izi sabitleme (gerçek uvicorn TLS sunucusuyla uçtan uca).

**Docker kurulunca yapılacak test listesi:**
1. `docker compose build` — imaj derleniyor mu, boyutu makul mü (yalnızca `requirements-server.txt`).
2. B modu (yalnızca IP): `.env` → `HYBRIDP2P_SITE=127.0.0.1`; `docker compose up -d`; `docker compose logs certgen` parmak izini yazıyor mu; tarayıcı `https://127.0.0.1` (uyarı sonrası) çalışıyor mu; masaüstü parmak iziyle bağlanıyor mu; WebSocket `wss://` üzerinden geçiyor mu.
3. Caddyfile: `{$HYBRIDP2P_TLS}` iki argümana (cert + key) doğru açılıyor mu; HTTP→HTTPS yönlendirmesi.
4. Kalıcılık: `docker compose down && up` sonrası kullanıcılar/kuyruk duruyor mu (`data` volume); sertifika değişmiyor mu (`certs` volume).
5. Konteyner sağlık kontrolü (`/health`) ve root olmayan kullanıcıyla `/data` yazma izni.
6. Rate limit Caddy arkasında istemci başına mı çalışıyor (`HYBRIDP2P_FORWARDED_ALLOW_IPS=*`).
7. `env_file: required: false` sözdizimi Docker Compose v2.24+ ister — eski sürümde hata verirse belgeye yazılmalı.
8. coturn profili (D5): `COMPOSE_PROFILES=turn` ile başlıyor mu; iki farklı ağdan (biri mobil veri) arama TURN üzerinden kuruluyor mu; özel IP'lere aktarım reddediliyor mu.
9. Cloudflare Tunnel demosu (D4) — eklendiğinde.
10. **PWA (D3), gerçek Chrome/Edge ile:** Claude'un gömülü tarayıcı paneli service worker kaydına izin vermiyor ("unknown error when fetching the script"), bu yüzden orada doğrulanamadı. Kontrol: adres çubuğunda "Uygulamayı yükle" çıkıyor mu; kurulan uygulama ayrı pencerede açılıyor mu; sunucu kapatılınca arayüz önbellekten açılıyor mu (bağlantı yok uyarısıyla); sunucu açılınca yeni sürüm hemen geliyor mu (önce-ağ politikası). pytest yalnızca manifest/ikonları ve önbellek listesinin eksiksiz ve sunulabilir olduğunu doğrular.

---

## 9. Pure P2P (Sunucusuz) Mod Herkese Açık STUN Kullanır (2026-10-07)

Sunucu üzerinden yapılan aramalarda STUN/TURN listesi sunucudan gelir ve `HYBRIDP2P_PUBLIC_STUN=0` ile Google tamamen devre dışı bırakılabilir (D5). **Pure P2P modu ise bir sunucuya bağlanmadığı için** bu ayarı göremez; `desktop/pure_p2p.py` Google ve Cloudflare STUN sunucularını sabit kullanır. STUN olmadan farklı ağlardaki iki cihaz birbirini bulamayacağı için bu bilinçli bir tercih; bedeli, STUN sağlayıcısının arayanların IP adresini görmesi (içerik değil).

İleride yapılabilir: masaüstü ayarlarına "özel STUN adresi" alanı (ör. kendi coturn'ünüz) ve web'deki sunucusuz moda aynısı.

