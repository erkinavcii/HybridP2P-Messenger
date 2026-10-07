"""Deployment: üretim giriş noktası, ters vekil arkasında istemci IP'si, kendinden imzalı sertifika.

Docker imajı `python server.py` çalıştırır; testler de aynı giriş noktasını kullanır.
"""

import ipaddress
import os
import socket
import ssl
import subprocess
import sys
import threading
import time

import pytest
import requests
from cryptography import x509

from conftest import ROOT, _free_port

sys.path.insert(0, str(ROOT / "deploy"))
import gen_cert  # noqa: E402


# ─────────────────────────── Vekil arkasında rate limit ───────────────────────────

def _start_entrypoint(tmp_path, forwarded_allow_ips):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("HYBRIDP2P_")}
    env.update({
        "HYBRIDP2P_HOST": "127.0.0.1",
        "HYBRIDP2P_PORT": str(port),
        "HYBRIDP2P_DB_PATH": str(tmp_path / "deploy.db"),
        "HYBRIDP2P_FORWARDED_ALLOW_IPS": forwarded_allow_ips,
    })
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if requests.get(f"{base}/health", timeout=0.5).status_code == 200:
                return proc, base
        except requests.RequestException:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("server.py başlamadı")


def _register_from(base, client_ip):
    return requests.post(f"{base}/api/register", headers={"X-Forwarded-For": client_ip},
                         json={"username": "X", "public_key": "", "timestamp": "", "signature": ""},
                         timeout=5).status_code


@pytest.mark.parametrize("trusted", [True, False])
def test_rate_limit_uses_real_client_ip_only_from_trusted_proxy(tmp_path, trusted):
    # Test istemcisi 127.0.0.1'den bağlanır, yani "vekil" odur.
    proc, base = _start_entrypoint(tmp_path, "127.0.0.1" if trusted else "10.255.255.1")
    try:
        first = [_register_from(base, "198.51.100.1") for _ in range(21)]
        assert first[:20] == [400] * 20 and first[20] == 429   # /api/register: 20/dk
        other = _register_from(base, "198.51.100.2")
        if trusted:
            assert other == 400   # başka istemci → kendi kotası
        else:
            # Güvenilmeyen kaynağın başlığı yok sayılır (sahte IP ile sınır aşılamaz):
            # herkes bağlantının gerçek adresiyle sayılır → aynı dolu kota
            assert other == 429
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_entrypoint_options_default_to_production(monkeypatch):
    import importlib
    for k in ("HYBRIDP2P_RELOAD", "HYBRIDP2P_FORWARDED_ALLOW_IPS"):
        monkeypatch.delenv(k, raising=False)
    import server.config as config
    importlib.reload(config)
    assert config.RELOAD is False
    assert config.FORWARDED_ALLOW_IPS == "127.0.0.1"
    monkeypatch.setenv("HYBRIDP2P_RELOAD", "1")
    importlib.reload(config)
    assert config.RELOAD is True
    monkeypatch.delenv("HYBRIDP2P_RELOAD")
    importlib.reload(config)


# ─────────────────────────── Kendinden imzalı sertifika ───────────────────────────

@pytest.mark.parametrize("site,expected", [
    ("203.0.113.5", "203.0.113.5"),
    ("https://203.0.113.5:443/", "203.0.113.5"),
    ("[2001:db8::1]:443", "2001:db8::1"),
    ("mesaj.local", "mesaj.local"),
])
def test_clean_host(site, expected):
    assert gen_cert._clean_host(site) == expected


def test_cert_has_ip_san_and_is_never_replaced(tmp_path):
    cert_path, key_path, created = gen_cert.generate("203.0.113.5", tmp_path)
    assert created
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("203.0.113.5")]
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days >= 3650 - 1
    fp = gen_cert.fingerprint(cert)

    # İkinci çalıştırma mevcut sertifikayı DEĞİŞTİRMEMELİ (sabitlemeler kırılır)
    _, _, created_again = gen_cert.generate("198.51.100.9", tmp_path)
    assert not created_again
    assert gen_cert.fingerprint(x509.load_pem_x509_certificate(cert_path.read_bytes())) == fp
    if os.name == "posix":
        assert (key_path.stat().st_mode & 0o077) == 0   # anahtar yalnızca sahibine açık


def test_main_skips_in_domain_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HYBRIDP2P_TLS", "sen@example.com")
    monkeypatch.setenv("HYBRIDP2P_CERT_DIR", str(tmp_path))
    assert gen_cert.main(["gen_cert.py"]) == 0
    assert not (tmp_path / "cert.pem").exists()


def test_generated_cert_serves_tls_with_matching_fingerprint(tmp_path):
    """Üretilen sertifika gerçek bir TLS sunucusunda çalışır; istemcinin gördüğü
    sertifikanın parmak izi fingerprint.txt'teki ile aynıdır (masaüstü bunu sabitleyecek)."""
    assert gen_cert.main(["gen_cert.py", "127.0.0.1", str(tmp_path)]) == 0
    expected = (tmp_path / "fingerprint.txt").read_text().strip()

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True):
                pass
        except OSError:   # istemci el sıkışmadan sonra hemen kapatır (SSLError dahil)
            pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()

    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE      # CA yok: güven parmak izinden gelir
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with client_ctx.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
    listener.close()
    seen = gen_cert.fingerprint(x509.load_der_x509_certificate(der))
    assert seen == expected
