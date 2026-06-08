# 🚀 HybridP2P Messenger: Kurulum ve Çalıştırma Kılavuzu

Bu kılavuz, HybridP2P Messenger uygulamasını sunucuya ve iki ayrı bilgisayara kurup karşılıklı olarak güvenli (E2EE) bir şekilde haberleşmek için yapılması gereken adımları içerir. Ayrıca istemciyi tek bir çalıştırılabilir dosyaya (.exe) dönüştürmeyi anlatır.

---

## 💻 1. Geliştirici / Kurulum Gereksinimleri
Eğer uygulamayı kod üzerinden çalıştıracaksanız, her iki bilgisayarda da Python 3.9+ yüklü olmalıdır.

Bağımlılıkları yüklemek için terminalde/komut satırında şu komutu çalıştırın:
```bash
python -m pip install -r requirements.txt
```

---

## ☁️ 2. Sunucu (Röle) Kurulumu ve Yayına Alma
Sunucu (Relay Server) mesajları uçtan uca şifreli olarak geçici saklar ve WebSocket yönlendirmesini yönetir.

### A. Yerel Ağda (Aynı Wi-Fi) veya İnternette Barındırma
1. Sunucu bilgisayarında `server.py` dosyasını çalıştırın:
   ```bash
   python server.py
   ```
2. Sunucu varsayılan olarak `8000` portunda çalışır.
3. **Güvenlik Duvarı (Firewall) Ayarı:** 
   * Dışarıdaki bilgisayarların bağlanabilmesi için sunucu makinesinin güvenlik duvarından **8000** portuna gelen TCP isteklerine izin vermelisiniz.
   * Windows için: *Gelişmiş Güvenlik Özellikli Windows Defender Güvenlik Duvarı* -> *Gelen Kuralları* -> *Yeni Kural* -> *Bağlantı Noktası (Port)* -> *TCP / 8000* seçilerek izin verilir.

---

## 📦 3. İstemciyi (Client) Tek Tıkla Çalışan `.exe` Yapmak
Arkadaşınızın bilgisayarına Python kurmakla uğraşmasını istemiyorsanız, istemciyi (`client.py`) doğrudan çift tıklayıp açabileceği bir `.exe` dosyası haline getirebilirsiniz.

### Adım 1: PyInstaller Yükleyin
Bilgisayarınızda terminali açıp şu komutu çalıştırın:
```bash
python -m pip install pyinstaller
```

### Adım 2: Tek `.exe` Dosyası Üretin
Proje dizinindeyken aşağıdaki komutu çalıştırarak Flet istemcisini paketleyin:
```bash
pyinstaller --clean --onefile --windowed --noconsole --name="HybridP2P-Messenger" client.py
```
*   `--onefile`: Tüm bağımlılıkları tek bir `.exe` içine gömer.
*   `--windowed` / `--noconsole`: Çalışırken arkada siyah komut satırı ekranının açılmasını engeller (sadece Flet arayüzü görünür).

### Adım 3: Çalıştırın
*   Paketleme bittiğinde, proje dizininde oluşan `dist/` klasörünün içinde **`HybridP2P-Messenger.exe`** dosyasını göreceksiniz.
*   Bu `.exe` dosyasını doğrudan arkadaşınıza gönderebilirsiniz. Arkadaşınızın bilgisayarında Python yüklü olması gerekmez.

---

## 🤝 4. İki Bilgisayar Arasında Karşılıklı Bağlantı ve İletişim

Sunucu IP adresinizi öğrendikten sonra (örneğin: `192.168.1.50` veya genel IP adresiniz `85.95.x.x`):

### Adım 1: İstemciyi Başlatın
1. İstemciyi çalıştırın (`client.py` veya derlediğiniz `.exe` dosyası üzerinden).
2. Giriş ekranındaki **"Sunucu Adresi"** alanına sunucunuzun IP ve port bilgisini girin (örn: `192.168.1.50:8000` veya genel IP adresi `85.95.x.y:8000`). Varsayılan olarak `127.0.0.1:8000` ayarlıdır.
3. Kendinize bir kullanıcı adı belirleyin (Örn: `alice` veya `bob`) ve **Giriş Yap** butonuna tıklayın.

### Adım 2: Kendi Kimlik Kartınızı Arkadaşınıza Gönderin
1. Giriş yaptıktan sonra sağ üstteki **Kimliği Kopyala (Contact Card)** butonuna (iki kağıt üst üste ikonu) tıklayın.
2. Bu buton panonuza şu şekilde güvenli bir JSON kimlik kartı kopyalar:
   ```json
   {
     "username": "alice",
     "public_key": "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...",
     "fingerprint": "a5f6e7d8...b2c3d4"
   }
   ```
3. Bu kopyalanan JSON metnini arkadaşınıza (e-posta, Whatsapp, SMS vb. güvenli dış bir kanaldan) iletin.

### Adım 3: Arkadaşınızı Rehbere Ekleyin ve Güvenli Sohbeti Başlatın
1. Arkadaşınızdan gelen JSON kimlik kartı metnini kopyalayın.
2. İstemci arayüzündeki **"Alıcı"** alanına doğrudan bu JSON metnini yapıştırın ve yanındaki **Link (Bağlan)** butonuna tıklayın.
3. Uygulama, arkadaşınızın kimlik kartını otomatik olarak ayrıştırır, Public Key'ini yerel veritabanına kaydeder ve arayüzde doğrudan onunla sohbet başlatır.
4. **İlk Bağlantı Onayı (TOFU):** Eğer arkadaşınızın kimlik kartını JSON olarak almadıysanız ve doğrudan kullanıcı adını yazıp bağlanmak isterseniz, sunucudan çekilen anahtar için karşınıza bir **Parmak İzi Doğrulama** ekranı gelecektir. Buradaki parmak izini arkadaşınızla teyit edip onaylayarak güvenle bağlanabilirsiniz.

---

## 🛠️ 5. Hata Giderme
*   **"Sunucu bağlantı hatası!" Alıyorum:** Sunucu programının açık olduğundan ve sunucu makinesindeki güvenlik duvarının `8000` portunu engellemediğinden emin olun.
*   **"Kullanıcı adı zaten kullanımda" Hatası:** Kayıt olduğunuz bilgisayardaki private key dosyasını silerseniz veya başka bir bilgisayardan aynı kullanıcı adıyla girmeye çalışırsanız sunucu sizi doğrulayamaz. Farklı bir kullanıcı adı seçin veya anahtarlarınızı yedekleyin (Anahtarlar `~/.hybridp2p_messenger/` dizininde saklanır).
