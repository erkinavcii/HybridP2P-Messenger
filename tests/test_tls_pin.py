"""Masaüstü: https/wss adresleri ve kendinden imzalı sertifika sabitleme (desktop/tls_pin.py).

Gerçek TLS sunucularıyla uçtan uca: doğru parmak izi bağlanır; yanlış parmak izi
hiç bağlanmaz; sabitlemeden sonra aynı adreste başka sertifika sunan bir sunucu
(MITM) hem REST hem WebSocket'te TLS el sıkışmasında reddedilir.
"""

import asyncio
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import websockets

from conftest import ROOT, _free_port

sys.path.insert(0, str(ROOT / "deploy"))
import gen_cert  # noqa: E402
from desktop import net_config, tls_pin  # noqa: E402


@pytest.fixture
def pin_store(tmp_path, monkeypatch):
    monkeypatch.setattr(tls_pin, "_store_dir", lambda: tmp_path / "tls")
    yield tmp_path / "tls"
    net_config.update_server_urls("127.0.0.1:8000")   # modül durumunu sıfırla


def _make_cert(tmp_path, name):
    d = tmp_path / name
    gen_cert.main(["gen_cert.py", "127.0.0.1", str(d)])
    return d / "cert.pem", d / "key.pem", (d / "fingerprint.txt").read_text().strip()


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def _https_server(cert, key, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Health)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ─────────────────────────── Adres ve parmak izi biçimi ───────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("127.0.0.1:8000", (False, "127.0.0.1", 8000)),          # eski biçim aynen çalışır
    ("", (False, "127.0.0.1", 8000)),
    ("server.com", (False, "server.com", 8000)),
    ("https://mesaj.example.com", (True, "mesaj.example.com", 443)),
    ("https://203.0.113.5:8443/", (True, "203.0.113.5", 8443)),
    ("http://10.0.0.2:9000", (False, "10.0.0.2", 9000)),
    ("server.com:443", (True, "server.com", 443)),
    ("wss://[2001:db8::1]", (True, "2001:db8::1", 443)),
])
def test_parse_server_address(text, expected):
    assert net_config.parse_server_address(text) == expected


@pytest.mark.parametrize("bad", ["ftp://x.com", "host:abc", "https://"])
def test_parse_server_address_rejects(bad):
    with pytest.raises(ValueError):
        net_config.parse_server_address(bad)


def test_urls_follow_scheme(pin_store):
    net_config.update_server_urls("https://mesaj.example.com")
    assert (net_config.BASE_URL, net_config.WS_URL) == ("https://mesaj.example.com", "wss://mesaj.example.com")
    assert net_config.TLS_VERIFY is True and net_config.ws_ssl_context() is not None
    net_config.update_server_urls("127.0.0.1:8000")
    assert (net_config.BASE_URL, net_config.WS_URL) == ("http://127.0.0.1:8000", "ws://127.0.0.1:8000")
    assert net_config.ws_ssl_context() is None


def test_pin_requires_https(pin_store):
    with pytest.raises(ValueError):
        net_config.update_server_urls("127.0.0.1:8000", "AB" * 32)


@pytest.mark.parametrize("text", ["ab:cd" + ":ef" * 30, "AB CD" + " EF" * 30, "abcd" + "ef" * 30])
def test_normalize_fingerprint_formats(text):
    assert tls_pin.normalize_fingerprint(text) == "AB:CD:" + ":".join(["EF"] * 30)


@pytest.mark.parametrize("bad", ["", "AB:CD", "ZZ" * 32, "AB" * 33])
def test_normalize_fingerprint_rejects(bad):
    with pytest.raises(ValueError):
        tls_pin.normalize_fingerprint(bad)


# ─────────────────────────── Uçtan uca: REST ───────────────────────────

def test_https_pinning_end_to_end(tmp_path, pin_store):
    cert_a, key_a, fp_a = _make_cert(tmp_path, "a")
    cert_b, key_b, fp_b = _make_cert(tmp_path, "b")
    port = _free_port()
    srv = _https_server(cert_a, key_a, port)
    addr = f"https://127.0.0.1:{port}"
    try:
        # Parmak izi olmadan kendinden imzalı sertifika reddedilir (CA doğrulaması)
        net_config.update_server_urls(addr)
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(net_config.BASE_URL + "/health", verify=net_config.TLS_VERIFY, timeout=5)

        # Yanlış parmak izi: bağlantı kurulmaz, ne görüldüğü raporlanır
        with pytest.raises(tls_pin.PinMismatch) as mm:
            net_config.update_server_urls(addr, fp_b)
        assert mm.value.seen == fp_a

        # Doğru parmak izi: bağlanır
        net_config.update_server_urls(addr, fp_a.lower().replace(":", ""))
        r = requests.get(net_config.BASE_URL + "/health", verify=net_config.TLS_VERIFY, timeout=5)
        assert r.status_code == 200

        # Hatırlanır: sonraki girişte parmak izi yazmadan da çalışır
        net_config.update_server_urls(addr)
        assert requests.get(net_config.BASE_URL + "/health", verify=net_config.TLS_VERIFY,
                            timeout=5).status_code == 200
    finally:
        srv.shutdown()
        srv.server_close()

    # Aynı adreste BAŞKA sertifika sunan sunucu (MITM / izinsiz değişiklik)
    mitm = _https_server(cert_b, key_b, port)
    try:
        # (1) Önceki oturumdaki sabitlenmiş sertifikayla istek: el sıkışmada reddedilir
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(net_config.BASE_URL + "/health", verify=net_config.TLS_VERIFY, timeout=5)
        # (2) Yeniden giriş: hatırlanan parmak izi uyuşmaz → açık uyarı
        with pytest.raises(tls_pin.PinMismatch):
            net_config.update_server_urls(addr)
    finally:
        mitm.shutdown()
        mitm.server_close()


# ─────────────────────────── Uçtan uca: WebSocket ───────────────────────────

def test_wss_uses_pinned_certificate_only(tmp_path, pin_store):
    cert_a, key_a, fp_a = _make_cert(tmp_path, "a")
    cert_b, key_b, _ = _make_cert(tmp_path, "b")

    async def echo(ws):
        async for msg in ws:
            await ws.send(msg)

    async def scenario():
        port = _free_port()
        ctx_a = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx_a.load_cert_chain(cert_a, key_a)
        async with websockets.serve(echo, "127.0.0.1", port, ssl=ctx_a):
            await asyncio.to_thread(net_config.update_server_urls, f"https://127.0.0.1:{port}", fp_a)
            async with websockets.connect(net_config.WS_URL + "/ws/x", ssl=net_config.ws_ssl_context()) as ws:
                await ws.send("merhaba")
                assert await ws.recv() == "merhaba"

        ctx_b = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx_b.load_cert_chain(cert_b, key_b)
        async with websockets.serve(echo, "127.0.0.1", port, ssl=ctx_b):
            with pytest.raises(ssl.SSLCertVerificationError):
                async with websockets.connect(net_config.WS_URL + "/ws/x", ssl=net_config.ws_ssl_context()):
                    pass

    asyncio.run(scenario())
