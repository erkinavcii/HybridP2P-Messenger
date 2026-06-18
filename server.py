from server.main import app
from server.config import HOST, PORT

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
    )
