"""
crypto_utils.py — Kriptografi Yardımcı Modülü
===============================================
Bu modül, HybridP2P-Messenger uygulamasının uçtan uca şifreleme (E2EE)
altyapısını sağlar.

Kullanılan Algoritmalar:
  • RSA-4096 (OAEP + SHA-256): Asimetrik anahtar çifti üretimi ve
    AES oturum anahtarının sarmalanması (key wrapping).
  • AES-256-GCM: Simetrik mesaj şifreleme. Her mesaj için benzersiz
    bir nonce (IV) üretilir; GCM modu hem gizlilik hem bütünlük sağlar.

Hibrit Şifreleme Akışı:
  1. Gönderici rastgele 256-bit AES anahtarı üretir.
  2. Mesaj düz metin → AES-GCM ile şifrelenir (ciphertext + tag + nonce).
  3. AES anahtarı, alıcının RSA Public Key'i ile RSA-OAEP şifrelenir.
  4. Paket = RSA-şifreli AES anahtarı + nonce + tag + ciphertext
     → Base64 kodlanarak sunucuya gönderilir.

  Çözme işlemi bunun tersidir: alıcı kendi Private Key'i ile AES
  anahtarını açar, ardından mesajı AES-GCM ile çözer.
"""

import os
import json
import base64
from pathlib import Path

# ── Kriptografi kütüphanesi importları ──────────────────────────────
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                    RSA ANAHTAR ÇİFTİ YÖNETİMİ                   ║
# ╚═══════════════════════════════════════════════════════════════════╝

def generate_rsa_keypair(key_size: int = 4096):
    """
    RSA anahtar çifti üretir.

    Args:
        key_size: Anahtar uzunluğu (bit). Varsayılan 4096-bit.

    Returns:
        (private_key, public_key) — cryptography RSA nesneleri.
    """
    # Gizli (Private) anahtar üretimi
    private_key = rsa.generate_private_key(
        public_exponent=65537,  # Standart RSA üsteli
        key_size=key_size,
    )
    # Açık (Public) anahtar, private key'den türetilir
    public_key = private_key.public_key()
    return private_key, public_key


def serialize_private_key(private_key) -> bytes:
    """
    Private key'i PEM formatında byte dizisine dönüştürür.
    Bu anahtar ASLA sunucuya gönderilmez! Yalnızca yerel dosyada saklanır.
    """
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
        # NOT: Üretim ortamında BestAvailableEncryption(password) kullanılmalı.
    )


def serialize_public_key(public_key) -> bytes:
    """
    Public key'i PEM formatında byte dizisine dönüştürür.
    Bu anahtar sunucuya kaydedilir, diğer istemciler tarafından alınır.
    """
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def deserialize_private_key(pem_data: bytes):
    """PEM formatındaki byte verisinden Private Key nesnesi oluşturur."""
    return serialization.load_pem_private_key(pem_data, password=None)


def deserialize_public_key(pem_data: bytes):
    """PEM formatındaki byte verisinden Public Key nesnesi oluşturur."""
    return serialization.load_pem_public_key(pem_data)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                  YEREL ANAHTAR DEPOLAMA (DOSYA)                  ║
# ╚═══════════════════════════════════════════════════════════════════╝

# Anahtarlar kullanıcının ev dizininde güvenli bir klasörde saklanır
KEYS_DIR = Path.home() / ".hybridp2p_messenger"


def save_keys_to_disk(username: str, private_key, public_key):
    """
    Anahtar çiftini diske kaydeder.
    Her kullanıcı için ayrı alt klasör oluşturulur.
    """
    user_dir = KEYS_DIR / username
    user_dir.mkdir(parents=True, exist_ok=True)

    # Private key dosyası (hassas — yalnızca sahip okuyabilmeli)
    priv_path = user_dir / "private_key.pem"
    priv_path.write_bytes(serialize_private_key(private_key))

    # Public key dosyası
    pub_path = user_dir / "public_key.pem"
    pub_path.write_bytes(serialize_public_key(public_key))

    return priv_path, pub_path


def load_keys_from_disk(username: str):
    """
    Daha önce kaydedilmiş anahtar çiftini diskten yükler.

    Returns:
        (private_key, public_key) veya anahtarlar yoksa (None, None).
    """
    user_dir = KEYS_DIR / username
    priv_path = user_dir / "private_key.pem"
    pub_path = user_dir / "public_key.pem"

    if not priv_path.exists() or not pub_path.exists():
        return None, None

    private_key = deserialize_private_key(priv_path.read_bytes())
    public_key = deserialize_public_key(pub_path.read_bytes())
    return private_key, public_key


# ╔═══════════════════════════════════════════════════════════════════╗
# ║             HİBRİT ŞİFRELEME (RSA-OAEP + AES-GCM)              ║
# ╚═══════════════════════════════════════════════════════════════════╝

def encrypt_message(plaintext: str, recipient_public_key) -> str:
    """
    Mesajı hibrit şifreleme ile şifreler.

    Adımlar:
      1. Rastgele 256-bit AES anahtarı üret
      2. Rastgele 96-bit nonce (IV) üret
      3. Mesajı AES-256-GCM ile şifrele
      4. AES anahtarını alıcının RSA Public Key'i ile şifrele (OAEP)
      5. Tüm bileşenleri Base64-kodlu JSON paketine dönüştür

    Args:
        plaintext: Şifrelenecek düz metin mesaj (UTF-8).
        recipient_public_key: Alıcının RSA Public Key nesnesi.

    Returns:
        Base64-kodlu JSON string (sunucuya gönderilecek ciphertext paketi).
    """
    # ── Adım 1: Rastgele AES-256 oturum anahtarı ──
    aes_key = AESGCM.generate_key(bit_length=256)  # 32 byte

    # ── Adım 2: Rastgele nonce (GCM için 96-bit standart) ──
    nonce = os.urandom(12)  # 12 byte = 96 bit

    # ── Adım 3: Mesajı AES-GCM ile şifrele ──
    aesgcm = AESGCM(aes_key)
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)
    # NOT: GCM otomatik olarak authentication tag'i ciphertext'e ekler

    # ── Adım 4: AES anahtarını RSA-OAEP ile şifrele ──
    encrypted_aes_key = recipient_public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    # ── Adım 5: Tüm bileşenleri JSON paketine koy ──
    packet = {
        "encrypted_aes_key": base64.b64encode(encrypted_aes_key).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    # JSON → UTF-8 bytes → Base64 (tek string olarak iletim kolaylığı)
    packet_json = json.dumps(packet)
    return base64.b64encode(packet_json.encode("utf-8")).decode("ascii")


def decrypt_message(encrypted_packet: str, private_key) -> str:
    """
    Hibrit-şifrelenmiş mesajı çözer.

    Adımlar:
      1. Base64 → JSON paketini aç
      2. RSA Private Key ile AES oturum anahtarını çöz
      3. AES-GCM ile mesajı çöz ve doğrula (auth tag kontrolü)

    Args:
        encrypted_packet: encrypt_message() çıktısı olan Base64 string.
        private_key: Alıcının RSA Private Key nesnesi.

    Returns:
        Düz metin mesaj (str).

    Raises:
        cryptography.exceptions.InvalidTag: Mesaj bütünlüğü bozuksa.
        ValueError: Paket formatı hatalıysa.
    """
    # ── Adım 1: Base64 → JSON → bileşenleri ayır ──
    packet_json = base64.b64decode(encrypted_packet).decode("utf-8")
    packet = json.loads(packet_json)

    encrypted_aes_key = base64.b64decode(packet["encrypted_aes_key"])
    nonce = base64.b64decode(packet["nonce"])
    ciphertext = base64.b64decode(packet["ciphertext"])

    # ── Adım 2: RSA-OAEP ile AES anahtarını çöz ──
    aes_key = private_key.decrypt(
        encrypted_aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    # ── Adım 3: AES-GCM ile mesajı çöz ──
    aesgcm = AESGCM(aes_key)
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)

    return plaintext_bytes.decode("utf-8")


# ╔═══════════════════════════════════════════════════════════════════╗
# ║             DOSYA ŞİFRELEME (Binary — RSA-OAEP + AES-GCM)       ║
# ╚═══════════════════════════════════════════════════════════════════╝

def encrypt_bytes(data: bytes, recipient_public_key) -> str:
    """
    Ham byte verisini (dosya, resim, ses vb.) hibrit şifreleme ile şifreler.
    encrypt_message() ile aynı mekanizma — sadece string yerine bytes alır.

    Args:
        data: Şifrelenecek ham byte verisi.
        recipient_public_key: Alıcının RSA Public Key nesnesi.

    Returns:
        Base64-kodlu JSON string (encrypt_message() ile aynı format).
    """
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, data, None)

    encrypted_aes_key = recipient_public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    packet = {
        "encrypted_aes_key": base64.b64encode(encrypted_aes_key).decode("ascii"),
        "nonce":              base64.b64encode(nonce).decode("ascii"),
        "ciphertext":         base64.b64encode(ciphertext).decode("ascii"),
    }
    return base64.b64encode(json.dumps(packet).encode("utf-8")).decode("ascii")


def decrypt_bytes(encrypted_packet: str, private_key) -> bytes:
    """
    encrypt_bytes() ile şifrelenmiş veriyi çözer.

    Returns:
        Ham byte verisi (orijinal dosya içeriği).

    Raises:
        cryptography.exceptions.InvalidTag: Veri bütünlüğü bozuksa.
    """
    packet_json = base64.b64decode(encrypted_packet).decode("utf-8")
    packet = json.loads(packet_json)

    encrypted_aes_key = base64.b64decode(packet["encrypted_aes_key"])
    nonce             = base64.b64decode(packet["nonce"])
    ciphertext        = base64.b64decode(packet["ciphertext"])

    aes_key = private_key.decrypt(
        encrypted_aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def encrypt_symmetric(plaintext: str, key_bytes: bytes) -> str:
    """
    Simetrik anahtarla (AES-256-GCM) düz metni şifreler.
    Çıktı olarak base64-kodlu JSON paketini döner (nonce ve ciphertext içerir).
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key_bytes)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    packet = {
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    packet_json = json.dumps(packet)
    return base64.b64encode(packet_json.encode("utf-8")).decode("ascii")


def decrypt_symmetric(encrypted_packet: str, key_bytes: bytes) -> str:
    """
    encrypt_symmetric ile şifrelenmiş paketi simetrik anahtarla çözer.
    """
    packet_json = base64.b64decode(encrypted_packet).decode("utf-8")
    packet = json.loads(packet_json)
    nonce = base64.b64decode(packet["nonce"])
    ciphertext = base64.b64decode(packet["ciphertext"])
    aesgcm = AESGCM(key_bytes)
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext_bytes.decode("utf-8")


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                   YARDIMCI FONKSİYONLAR                          ║
# ╚═══════════════════════════════════════════════════════════════════╝

def public_key_to_pem_string(public_key) -> str:
    """Public key'i PEM formatında string olarak döndürür (API iletimi için)."""
    return serialize_public_key(public_key).decode("utf-8")


def pem_string_to_public_key(pem_string: str):
    """PEM string'inden Public Key nesnesi oluşturur."""
    return deserialize_public_key(pem_string.encode("utf-8"))


def get_public_key_fingerprint(public_key) -> str:
    """Public key'in SHA-256 fingerprint'ini okunaklı formatta döner (örn: A1B2 C3D4...)."""
    import hashlib
    pem_bytes = serialize_public_key(public_key)
    sha256_hash = hashlib.sha256(pem_bytes).hexdigest().upper()
    groups = [sha256_hash[i:i+4] for i in range(0, len(sha256_hash), 4)]
    return " ".join(groups)


def sign_data(private_key, data: bytes) -> bytes:
    """Veriyi private key ile imzalar (PKCS1v15 + SHA256)."""
    return private_key.sign(
        data,
        padding.PKCS1v15(),
        hashes.SHA256()
    )


def verify_signature(public_key, signature: bytes, data: bytes) -> bool:
    """İmzayı public key ile doğrular. Hata almazsa True, alırsa False döner."""
    try:
        public_key.verify(
            signature,
            data,
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False


# ╔═══════════════════════════════════════════════════════════════════╗
# ║                     BİRİM TESTİ (Self-Test)                      ║
# ╚═══════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    print("=" * 60)
    print("  🔐 Kriptografi Modülü — Birim Testi")
    print("=" * 60)

    # 1. İki kullanıcı için anahtar çifti üret
    print("\n[1] RSA-4096 anahtar çiftleri üretiliyor...")
    alice_priv, alice_pub = generate_rsa_keypair()
    bob_priv, bob_pub = generate_rsa_keypair()
    print("    ✅ Alice ve Bob için anahtarlar üretildi.")

    # 2. Alice, Bob'a şifreli mesaj gönderir
    original_message = "Merhaba Bob! Bu mesaj uçtan uca şifrelenmiştir. 🔒"
    print(f"\n[2] Orijinal mesaj: {original_message}")

    encrypted = encrypt_message(original_message, bob_pub)
    print(f"    🔒 Şifreli paket (ilk 80 karakter): {encrypted[:80]}...")

    # 3. Bob mesajı kendi private key'i ile çözer
    decrypted = decrypt_message(encrypted, bob_priv)
    print(f"\n[3] Çözülen mesaj: {decrypted}")

    # 4. Doğrulama
    assert original_message == decrypted, "❌ HATA: Mesajlar eşleşmiyor!"
    print("\n    ✅ Şifreleme/Çözme döngüsü başarılı!")

    # 5. Serileştirme testi
    pub_pem = public_key_to_pem_string(alice_pub)
    restored_pub = pem_string_to_public_key(pub_pem)
    test_msg = "Serileştirme testi"
    enc2 = encrypt_message(test_msg, restored_pub)
    dec2 = decrypt_message(enc2, alice_priv)
    assert test_msg == dec2, "❌ HATA: Serileştirme sonrası çözme başarısız!"
    print("    ✅ PEM serileştirme/deserileştirme başarılı!")

    print("\n" + "=" * 60)
    print("  ✅ Tüm testler geçti!")
    print("=" * 60)
