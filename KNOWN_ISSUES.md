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

### Etkilenen Kod Satırları (`client.py`)
* **Giriş İşlemi:** `do_login` fonksiyonu (Satır 1605-1655 arası) `threading.Thread` ile başlatılmakta ve içerisinden `show_inbox_screen()` çağrılmaktadır (Satır 1643).
* **Arama Sayacı:** `_call_timer_loop` coroutine'i (Satır 3646-3657 arası) `ws_loop` üzerinde `asyncio.create_task` ile çalıştırılmakta ve her saniye `page.update()` çağırmaktadır.
* **Diğer Potansiyel Noktalar:**
  - [x] `do_download` (Dosya indirme thread'i)
  - [x] `do_rest` (API istekleri thread'i)
  - [x] `do_file_upload_and_send` (Dosya yükleme thread'i)
  - [ ] `do_rekey` (Anahtar yenileme thread'i)
  - [ ] `do_create` (Grup oluşturma thread'i)
  - [ ] `check_recipient_status_loop` (Kullanıcı durum kontrolü)

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
