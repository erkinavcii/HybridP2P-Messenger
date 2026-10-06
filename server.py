from server.main import app
from server.config import HOST, PORT, MAX_WS_MESSAGE_SIZE

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  HybridP2P Messenger - Relay Server (Modularized)")
    print(f"  REST API: http://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/docs")
    print(f"  WebSocket: ws://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/ws/{{username}}")
    print("=" * 60)

    uvicorn.run(
        "server.main:app",
        host=HOST,
        port=PORT,
        reload=True,           # Geliştirme modunda otomatik yeniden yükleme
        log_level="info",
        # Protokol seviyesinde sert sınır (varsayılan 16 MB idi). Uygulama sınırının
        # 2 katı: aradaki mesajlar uygulamada kibarca reddedilir, bundan büyük
        # çerçeveler belleğe alınmadan bağlantı kesilir (kod 1009).
        ws_max_size=MAX_WS_MESSAGE_SIZE * 2,
    )
