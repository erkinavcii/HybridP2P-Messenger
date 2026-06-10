import sys

def main():
    try:
        with open('client.py', 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print("client.py not found.")
        return

    # Colors UI Updates
    replacements = {
        # Theme / BG
        '#0a0e1a': '#09090b', # main bg (zinc-950)
        '#0f1328': '#18181b', # appbar bg (zinc-900)
        '#0d1124': '#18181b', # input bg
        '#141832': '#18181b', # dialog bg
        '#1e1b30': '#18181b', # alert bg
        '#151a2e': '#27272a', # system bubble bg (zinc-800)
        '#1e2337': '#27272a', # their bubble bg, borders
        '#2a2f4e': '#3f3f46', # input borders (zinc-700)
        
        # Accents
        '#6c63ff': '#8b5cf6', # purple primary
        '#a29bfe': '#a78bfa', # purple light
        '#ff6b6b': '#ef4444', # red error
        '#ff6b6b44': '#ef444444', 
        '#4caf50': '#22c55e', # green success
        '#a0a0ff': '#a78bfa', # file icon
        'font_family="Segoe UI"': 'font_family="Inter, sans-serif"',

        # Translation
        '"Tek gorunumlu mesaj (view-once): per-mesaj 👁 toggle, otomatik kapanır"': '"View-once messages: per-message 👁 toggle, auto-closes"',
        '"Dosya/resim gönderimi: AES-GCM ile şifreli upload → UUID → receiver indirir"': '"File/Image transfer: AES-GCM encrypted upload → UUID → receiver downloads"',
        '"Resimler inline thumbnail olarak gösterilir"': '"Images displayed as inline thumbnails"',
        '"Diğer dosyalar kaydet butonuyla indirilir"': '"Other files downloaded with save button"',
        '"HybridP2P Messenger"': '"HybridP2P Messenger"',
        '"Gonderici"': '"Sender"',
        '"Sen"': '"You"',
        '"Tek Gorunumlu Mesaj"': '"View-Once Message"',
        '"Tek gorunumlu mesaj gonderildi."': '"View-once message sent."',
        '"[Cozme hatasi: "': '"[Decryption error: "',
        '"Kapat (Sil)"': '"Close (Delete)"',
        '"Kapat"': '"Close"',
        '"Bu pencere kapatildiginda mesaj sohbetten kalici olarak silinecektir."': '"This message will be permanently deleted from the chat once closed."',
        '"Tek gorunumlu mesaj"': '"View-once message"',
        '"Gormek icin dokun"': '"Tap to view"',
        '"Tek Gorunumlu Dosya"': '"View-Once File"',
        '"Indir"': '"Download"',
        '"Indiriliyor..."': '"Downloading..."',
        '"Gonderdiginiz dosya: "': '"File sent: "',
        '"Dosya indirildi ve kaydedildi:\\n"': '"File downloaded and saved:\\n"',
        '"Bu dosya yerel Downloads klasorune kaydedildi. Pencere kapatildiginda mesaj sohbetten kalici olarak silinecektir."': '"This file has been saved to your local Downloads folder. It will be permanently deleted from the chat once closed."',
        '"Bu dosya pencere kapatildiginda sohbetten kalici olarak silinecektir."': '"This file will be permanently deleted from the chat once closed."',
        '"Indirme basarisiz veya zaten indirildi."': '"Download failed or already downloaded."',
        '"Hata: "': '"Error: "',
        '"Kaydedildi: "': '"Saved: "',
        '"Goruntule"': '"View"',
        '"Indir ve Coz"': '"Download & Decrypt"',
        '"Tek gorunumlu"': '"View-once"',
        '"Mevcut anahtarlar yuklendi."': '"Existing keys loaded."',
        '"RSA-4096 uretiliyor..."': '"Generating RSA-4096 keys..."',
        '"Anahtarlar olusturuldu."': '"Keys generated."',
        '"Sunucudan cevrimdisi mesajlar talep ediliyor"': '"Requesting offline messages from server"',
        '" cevrimdisi mesaj alindi."': '" offline messages received."',
        '" yeni cevrimdisi mesaj alindi."': '" new offline messages received."',
        '"Cevrimdisi mesaj yok."': '"No offline messages."',
        '" kullanicisindan gelen cevrimdisi mesaj basariyla cozuldu."': '"\'s offline message successfully decrypted."',
        '"Mesaj cozme hatasi: "': '"Message decryption error: "',
        '"[Hata: "': '"[Error: "',
        '"Cevrimdisi mesajlar alinamadi."': '"Failed to fetch offline messages."',
        '"Cevrimdisi mesajlar sunucudan cekilemedi: "': '"Failed to fetch offline messages from server: "',
        '" adlisindan yeni mesaj var!"': '" sent a new message!"',
        '" adlisindan dosya var!"': '" sent a file!"',
        '"Once bir alici secin!"': '"Please select a recipient first!"',
        '"Gecici sohbet modu ACILDI — mesajlar kaydedilmiyor"': '"Ephemeral mode ON — messages are not saved"',
        '"Mesaj kayit modu ACILDI — mesajlar kaydediliyor"': '"Message history ON — messages are saved"',
        '"Gecici mod ACIK — kapat"': '"Ephemeral mode ON — disable"',
        '"Gecici sohbete gec"': '"Switch to Ephemeral Chat"',
        '"Kimlik kartiniz panoya kopyalandi!"': '"Contact card copied to clipboard!"',
        '"Kopyalama hatasi: "': '"Copy error: "',
        '" gecici modu ACTI — kayit durdu"': '" turned ON ephemeral mode — saving stopped"',
        '" kayit modunu ACTI"': '" turned ON message saving"',
        '"Tek gorunumlu ACIK — kapat"': '"View-once ON — disable"',
        '"Tek gorunumlu gonder"': '"Send as view-once"',
        '"Once bir aliciya veya gruba baglanin!"': '"Please connect to a recipient or group first!"',
        '"Dosya sifreleniyor: "': '"Encrypting file: "',
        '"Dosya cok buyuk! Max 10 MB."': '"File too large! Max 10 MB."',
        '"Upload basarisiz: "': '"Upload failed: "',
        '" gonderildi."': '" sent."',
        '"Dosya gonderim hatasi: "': '"File send error: "',
        '"WebSocket baglantisi kuruluyor..."': '"Establishing WebSocket connection..."',
        '"Handshake hatası: Sunucudan challenge alınamadı."': '"Handshake error: No challenge received from server."',
        '"Kimlik doğrulama başarısız."': '"Authentication failed."',
        '"Kimlik doğrulama hatası: "': '"Authentication error: "',
        '"Baglanti kuruldu."': '"Connection established."',
        '" sizi gruba ekledi. Anahtar alindi."': '" added you to the group. Key received."',
        '"Grup anahtari cozme hatasi: "': '"Group key decryption error: "',
        '"HATA: \'"': '"ERROR: \'"',
        '"\' kullanicisinin grup imza dogrulamasi basarisiz!"': '"\'s group signature verification failed!"',
        '"UYARI: \'"': '"WARNING: \'"',
        '"\' adli kullanicinin kimligi dogrulanamadi (Taklit Tesebbusu)!"': '"\'s identity could not be verified (Impersonation Attempt)!"',
        '"dan yeni mesaj!"': '" sent a new message!"',
        '"Grup mesaji cozme hatasi: "': '"Group message decryption error: "',
        '"\' adlisina iletildi."': '"\' delivered."',
        '"\' cevrimdisi. Mesaj saklandı."': '"\' is offline. Message stored."',
        '"Baglanti kapandi/koptu: "': '"Connection closed/dropped: "',
        '"Beklenmedik hata: "': '"Unexpected error: "',
        '"WS koptu. {reconnect_delay}s sonra tekrar..."': '"WS disconnected. Reconnecting in {reconnect_delay}s..."',
        '"Mesaj sunucuya WebSocket uzerinden gonderiliyor..."': '"Sending message via WebSocket..."',
        '"HATA: WebSocket baglantisi aktif degil!"': '"ERROR: WebSocket connection is not active!"',
        '"WebSocket kapali. Mesaj \'"': '"WebSocket closed. Message \'"',
        '"\' REST API ile gonderiliyor..."': '"\' is being sent via REST API..."',
        '"Fallback ile \'"': '"Successfully sent \'"',
        '"\' basariyla gonderildi."': '"\' via fallback."',
        '"Mesaj REST ile iletildi."': '"Message delivered via REST."',
        '"HATA: Fallback basarisiz ("': '"ERROR: Fallback failed ("',
        '"Mesaj iletilemedi!"': '"Message could not be delivered!"',
        '"HATA: Fallback baglanti hatasi: "': '"ERROR: Fallback connection error: "',
        '"Thread baslatma hatasi: "': '"Thread start error: "',
        '"Mesaj \'"': '"Encrypting message for \'"',
        '"\' icin RSA-4096 ile sifreleniyor..."': '"\' using RSA-4096..."',
        '"HATA: \'"': '"ERROR: \'"',
        '"\' kullanicisinin public key\'i bulunamadi!"': '"\'s public key not found!"',
        '"Sifreleme hatasi: "': '"Encryption error: "',
        '"Grup senkronizasyon hatasi: "': '"Group synchronization error: "',
        '"Hos geldiniz!"': '"Welcome!"',
        '"Dosya / Resim gonder"': '"Send File / Image"',
        '"Sunucu Adresi"': '"Server Address"',
        '"Ornek: 127.0.0.1:8000 veya sunucu.com:8000"': '"Example: 127.0.0.1:8000 or server.com:8000"',
        '"Kullanici Adi"': '"Username"',
        '"Ornek: alice"': '"Example: alice"',
        '"Giris Yap"': '"Sign In"',
        '"En az 2 karakter!"': '"At least 2 characters required!"',
        '"Lutfen bekleyin..."': '"Please wait..."',
        '"Giris yapiliyor..."': '"Signing in..."',
        '"Sunucu baglanti hatasi! (Sunucu acik mi?)"': '"Server connection error! (Is it running?)"',
        '"Giris basarisiz. Sunucu baglanti hatasi."': '"Sign in failed. Server connection error."',
        '"Giris sirasinda hata olustu: "': '"Error during sign in: "',
        '"Uctan Uca Sifrelenmiş Mesajlasma"': '"End-to-End Encrypted Messaging"',
        '"Alici"': '"Recipient"',
        '"Ornek: bob"': '"Example: bob"',
        '"Mesajinizi yazin..."': '"Type your message..."',
        '"Alici adi veya grup ID bos olamaz!"': '"Recipient name or group ID cannot be empty!"',
        '"Bu sohbet GECICI moddadir — mesajlar kaydedilmiyor"': '"This chat is in EPHEMERAL mode — messages are not saved"',
        '" ile sohbet basladi."': '" chat started."',
        '" icin yeni anahtar kabul edildi."': '" new key accepted for "',
        '"Baglanti guvenlik nedeniyle reddedildi."': '"Connection rejected for security reasons."',
        '"Baglanti onaylanmadi."': '"Connection not approved."',
        '"Kendi kimlik kartinizi ekleyemezsiniz!"': '"You cannot add your own contact card!"',
        '" kimlik karti basariyla import edildi!"': '" contact card imported successfully!"',
        '"Kimlik karti yukleme hatasi: "': '"Contact card import error: "',
        '"Kendinize mesaj gonderemezsiniz!"': '"You cannot send a message to yourself!"',
        '"Hata: Bu grubun sifreleme anahtari sizde yok!"': '"Error: You don\'t have the encryption key for this group!"',
        '" grubu ile sohbet basladi."': '" group chat started."',
        '"GUVENLIK UYARISI!"': '"SECURITY WARNING!"',
        '"DIKKAT: \'"': '"ATTENTION: \'"',
        '"\' kullanicisinin sunucudaki kimlik anahtari yerel kaydinizdan farkli!\\n\\n"': '"\'s identity key on the server is different from your local record!\\n\\n"',
        '"Bu durum bir MITM dinleme saldirisina veya anahtar yenilenmesine isaret edebilir.\\n\\n"': '"This could indicate a MITM attack or a key renewal.\\n\\n"',
        '"Sunucudaki yeni anahtari kabul etmek istiyor musunuz?"': '"Do you want to accept the new key from the server?"',
        '"Reddet (Guvenli)"': '"Reject (Safe)"',
        '"Yeni Anahtari Kabul Et"': '"Accept New Key"',
        '"Ilk Baglanti & Kimlik Dogrulama"': '"First Connection & Authentication"',
        '"\' kullanicisi ile ilk defa baglanti kuruluyor."': '"\' is connecting for the first time."',
        '"Sunucudan alinan kimlik parmak izi (Fingerprint):"': '"Identity fingerprint received from server:"',
        '"Guvenliginiz icin bu parmak izini arkadasinizla baska bir kanaldan dogrulamaniz onerilir."': '"For your safety, verify this fingerprint with your friend through another channel."',
        '"Iptal Et (Guvenli)"': '"Cancel (Safe)"',
        '"Anahtari Onayla ve Baglan"': '"Approve Key & Connect"',
        '" bulunamadi."': '" not found."',
        '"Hata: Grubun sifreleme anahtari bulunamadi!"': '"Error: Group encryption key not found!"',
        '"Grup sifreleme hatasi: "': '"Group encryption error: "',
        '"Grup Yonetimi"': '"Group Management"',
        '"Kimligi Kopyala (Contact Card)"': '"Copy Contact Card"',
        '"Cevrimdisi mesajlari cek"': '"Fetch Offline Messages"',
        '"Aliciya baglan"': '"Connect to Recipient"',
        '"Gonder (E2EE)"': '"Send (E2EE)"',
        '"Grup Adi"': '"Group Name"',
        '"Ornek: Aile"': '"Example: Family"',
        '"Uyeler"': '"Members"',
        '"Ornek: bob, charlie (virgülle ayirin)"': '"Example: bob, charlie (comma separated)"',
        '"Sohbete Basla"': '"Start Chat"',
        '"Anahtari Yenile (Rekey)"': '"Refresh Key (Rekey)"',
        '"Gruptan Cik"': '"Leave Group"',
        '"Grup adi bos olamaz!"': '"Group name cannot be empty!"',
        '" grubunun anahtari yenilendi ve dagitildi."': '" group key refreshed and distributed."',
        '" grubundan ciktiniz."': '" group left."',
        '"Gruptan cikma hatasi: "': '"Error leaving group: "',
        '" grubu olusturuldu."': '" group created."',
        '"Grup olusturma hatasi: "': '"Group creation error: "',
        '"Yeni Grup Olustur"': '"Create New Group"',
        '"Grup Olustur"': '"Create Group"',
        '"Gruplarim"': '"My Groups"',
        '"Kullanici: "': '"User: "',
    }

    # Custom regex replacements for more tricky parts
    import re
    
    # Border radius
    content = re.sub(r'top_left=18, top_right=18,\s*bottom_left=4 if is_mine else 18,\s*bottom_right=18 if is_mine else 4,', 
                     r'top_left=14, top_right=14,\n                        bottom_left=4 if is_mine else 14,\n                        bottom_right=14 if is_mine else 4,', content)
                     
    content = re.sub(r'radius=12', r'radius=8', content)

    for k, v in replacements.items():
        content = content.replace(k, v)

    with open('client.py', 'w', encoding='utf-8') as f:
        f.write(content)

    print("UI modernized and English localization applied.")

if __name__ == '__main__':
    main()
