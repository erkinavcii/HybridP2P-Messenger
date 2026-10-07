"""deploy/gen_cert.py — Alan adı OLMAYAN kurulum için kendinden imzalı TLS sertifikası.

Sansüre dayanıklı mod: sunucunun yalnızca bir IP adresi vardır; ne alan adı ne de
bir sertifika otoritesi (Let's Encrypt) gerekir. Sertifikayı burada kendimiz
üretiriz; güven, masaüstü istemcisinin bu sertifikanın SHA-256 parmak izini
SABİTLEMESİYLE kurulur (tarayıcı ilk girişte uyarı gösterir).

Davranış (idempotent):
  • /certs/cert.pem yoksa: ECDSA P-256, 10 yıl geçerli sertifika + anahtar üretir.
  • Her çalıştırmada parmak izini yazdırır ve /certs/fingerprint.txt'e kaydeder.
  • Mevcut sertifikayı ASLA değiştirmez (değişirse tüm sabitlemeler kırılır);
    yenilemek için dosyaları bilerek silin ve kullanıcılara yeni parmak izini verin.

Kullanım:  python deploy/gen_cert.py <ip-veya-ad> [çıktı_dizini]
           (Docker'da: HYBRIDP2P_SITE ortam değişkeninden okunur, dizin /certs)
"""

import datetime
import ipaddress
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 3650


def _clean_host(site: str) -> str:
    """'https://1.2.3.4:443' → '1.2.3.4' ; 'mesaj.local' → 'mesaj.local'."""
    host = site.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    if host.startswith("["):                  # [IPv6]:port
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:                  # ad:port veya IPv4:port
        host = host.split(":", 1)[0]
    return host


def fingerprint(cert: x509.Certificate) -> str:
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def generate(site: str, out_dir: Path) -> tuple[Path, Path, bool]:
    """Sertifikayı gerekirse üretir. Dönüş: (cert_yolu, key_yolu, yeni_mi)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = out_dir / "cert.pem", out_dir / "key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path, False

    host = _clean_host(site)
    if not host:
        raise ValueError("sunucu adresi (IP veya ad) boş")
    try:
        san = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HybridP2P self-hosted")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )

    # Anahtar önce kısıtlı izinle yazılır (yalnızca sahibi okuyabilir)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, True


def main(argv: list[str]) -> int:
    # Docker'da her kurulumda çalışır; alan adı modunda (Let's Encrypt) iş yok
    tls = os.getenv("HYBRIDP2P_TLS", "/certs/")
    if len(argv) <= 1 and not tls.strip().startswith("/certs/"):
        print("[gen_cert] Alan adı modu (Let's Encrypt) — kendinden imzalı sertifika gerekmiyor.")
        return 0
    site = argv[1] if len(argv) > 1 else os.getenv("HYBRIDP2P_SITE", "")
    out_dir = Path(argv[2] if len(argv) > 2 else os.getenv("HYBRIDP2P_CERT_DIR", "/certs"))
    try:
        cert_path, _, created = generate(site, out_dir)
    except ValueError as ex:
        print(f"[gen_cert] HATA: {ex}. .env içinde HYBRIDP2P_SITE'ı sunucunun IP'si yapın.", file=sys.stderr)
        return 1
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    fp = fingerprint(cert)
    (out_dir / "fingerprint.txt").write_text(fp + "\n", encoding="utf-8")
    print("=" * 72)
    print("[gen_cert] " + ("YENİ sertifika üretildi" if created else "Mevcut sertifika kullanılıyor")
          + f" — {cert.subject.rfc4514_string()}")
    print("[gen_cert] SHA-256 parmak izi (masaüstü istemcisine bunu girin):")
    print(f"           {fp}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
