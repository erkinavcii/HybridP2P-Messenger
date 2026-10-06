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

### 4.2 — Belgelendi (düzeltilmedi, kapsam dışı): Pure P2P dialogu ile `ws_loop` arasında yarış penceresi
`desktop/pure_p2p.py` (`open_pure_p2p_dialog`), WebRTC teklif/cevap üretimini `state["ws_loop"]` üzerinde `asyncio.run_coroutine_threadsafe(...)` ile çalıştırır. Bu event loop, `desktop/ws_client.py`'deki `_ws_listen` coroutine'i tarafından `WsClientMixin._run_ws_loop`'un başlattığı arka plan thread'inde kurulur (`self.state["ws_loop"] = asyncio.get_running_loop()`). Pure P2P dialogu sadece giriş yaptıktan sonra (inbox ekranından) açılabildiği ve giriş `background_sync` içinde `start_websocket_listener()`'ı hemen tetiklediği için pratikte bu sorun neredeyse hiç tetiklenmez — ama teorik olarak, kullanıcı giriş yaptıktan hemen sonra, WS thread'i `ws_loop`'u set etmeden önce Pure P2P dialogunu açarsa `state["ws_loop"]` hâlâ `None` olabilir ve `asyncio.run_coroutine_threadsafe(_setup_offer(), None)` hata fırlatır.

**Durum:** Bu modülerleştirme kapsamında davranış değiştirilmedi (agents.md §2.3 gereği, plan onaylanmadan davranış değişikliği yapılmaz) — sadece bulgu olarak belgeleniyor. Düzeltme önerisi: `open_pure_p2p_dialog` başında `if not self.state.get("ws_loop"): log_status("Lütfen birkaç saniye bekleyip tekrar deneyin"); return` gibi bir ön kontrol eklemek.

### 4.3 — Ayrıca belgelenmiş: Pure P2P güvenlik notu
`open_pure_p2p_dialog`, hiçbir RSA/AES E2EE çağrısı yapmaz (sıfır çağrı `encrypt_message`/`decrypt_message`/`sign_data`/`verify_signature`'a) — güvenliği tamamen WebRTC'nin kendi DTLS-SRTP'sine bırakır ve genel STUN sunucularını sabit kodlar (`/api/ice_servers`'ı kullanmaz). Bu, tasarım gereği (sunucusuz mod E2EE anahtar değişimi altyapısına ihtiyaç duymaz) — bir hata değil, ama README'nin genel E2EE iddialarıyla karıştırılmaması için burada not edildi.

---

## 6. Mesaj Düzenleme/Silme — Bilinen Sınırlar (2026-10-06)

- **Eski mesajlar:** `msg_uid` özelliğinden önce gönderilmiş mesajların kararlı kimliği yok; düzenlenemez/silinemez (⋮ menüsü görünmez).
- **Ephemeral sohbetler:** mesajlar diske yazılmadığı için düzenlenecek kayıt yok; menü gösterilmez. Gelen düzenleme/silme ephemeral sohbette uygulanmaz.
- **Gruplar ve tek görünümlük mesajlar:** kapsam dışı (v1 yalnızca birebir, kalıcı metin mesajları).
- **Karşı taraf eski sürümse:** düzenleme/silme çerçevelerini yok sayar; değişiklik yalnızca sizin tarafınızda görünür.
- **"Herkesten sil" bir garanti değildir:** alıcı mesajı silinmeden önce okumuş, kopyalamış veya ekran görüntüsü almış olabilir; değiştirilmiş bir istemci silme isteğini uygulamayabilir. Bu, tüm mesajlaşma uygulamaları için geçerlidir.
- **Web istemcisi:** henüz desteklemiyor (web paritesi turunda).

---

## 7. Link Önizleme — Bilinen Sınırlar (2026-10-06)

- **Site gönderenin IP'sini görür:** önizlemeyi gönderen çektiği için kaçınılmaz. Ayarlardan ("Link önizleme") kapatılabilir; Tor modu gelirse çekim de onun üzerinden yapılmalı.
- **İçerik gönderenin beyanıdır:** imza önizlemenin o kişiden geldiğini kanıtlar, sayfanın gerçekten öyle olduğunu kanıtlamaz. Kötü niyetli bir kişi, linkin gittiği yerden farklı bir başlık veya resim gösterebilir. Kartta alan adı gösterilir; tıklanınca açılan adres mesajdaki URL'dir.
- **Gecikme:** önizleme hazırlanırken (en fazla ~8 sn) o mesaj ve arkasındaki birebir mesajlar sırayla bekler; sıra bozulmaz, ekranda mesaj hemen görünür.
- **Düzenleme önizlemeyi kaldırır:** düzenlenen mesajın eski önizlemesi her iki tarafta silinir, yenisi üretilmez.
- **DNS yeniden bağlama (rebinding):** adres kontrolü ile bağlantı arasında DNS yanıtı değişirse koruma teorik olarak aşılabilir. Pratikte risk düşük; tam çözüm, kontrol edilen IP'ye doğrudan bağlanmaktır.
- **Kapsam dışı:** gruplar, tek görünümlük mesajlar ve (şimdilik) web istemcisi. Web istemcisi `encrypted_preview` alanını yok sayar.

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
