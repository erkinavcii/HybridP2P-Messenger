from server.main import app
from server.config import HOST, PORT, MAX_WS_MESSAGE_SIZE, RELOAD, FORWARDED_ALLOW_IPS


def uvicorn_options() -> dict:
    """Ortam değişkenlerinden uvicorn ayarları (geliştirme ve Docker aynı giriş noktası)."""
    return dict(
        host=HOST,
        port=PORT,
        reload=RELOAD,         # HYBRIDP2P_RELOAD=1 → geliştirmede otomatik yeniden yükleme
        log_level="info",
        # Vekil arkasında gerçek istemci IP'si (rate limit için) — bkz. server/config.py
        proxy_headers=True,
        forwarded_allow_ips=FORWARDED_ALLOW_IPS,
        # Protokol seviyesinde sert sınır (varsayılan 16 MB idi). Uygulama sınırının
        # 2 katı: aradaki mesajlar uygulamada kibarca reddedilir, bundan büyük
        # çerçeveler belleğe alınmadan bağlantı kesilir (kod 1009).
        ws_max_size=MAX_WS_MESSAGE_SIZE * 2,
    )


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  HybridP2P Messenger - Relay Server (Modularized)")
    print(f"  REST API: http://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/docs")
    print(f"  WebSocket: ws://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/ws/{{username}}")
    print(f"  Otomatik yeniden yükleme: {'açık' if RELOAD else 'kapalı'} (HYBRIDP2P_RELOAD)")
    print("=" * 60)

    uvicorn.run("server.main:app", **uvicorn_options())
