import asyncio
from fastapi import WebSocket

class ConnectionManager:
    """
    Aktif WebSocket bağlantılarını yöneten sınıf.

    Her kullanıcı bağlandığında username → WebSocket eşlemesi yapılır.
    Mesaj geldiğinde alıcı bağlıysa doğrudan iletilir,
    bağlı değilse çevrimdışı kuyruğa (SQLite) yazılır.

    VoIP için ek state:
      active_calls  : { username -> call_id }  — şu an bir aramadaki kullanıcılar
      call_timers   : { call_id -> asyncio.Task } — 30s timeout görevleri
    """

    def __init__(self):
        # Aktif bağlantılar: {username: WebSocket}
        self.active_connections: dict[str, WebSocket] = {}
        # VoIP: arayan/aranan haritası (bellek içi, DB'ye yazılmaz)
        self.active_calls: dict[str, str] = {}       # username -> call_id
        self.call_timers: dict[str, asyncio.Task] = {}  # call_id -> timeout task

    async def connect(self, username: str, websocket: WebSocket):
        """Yeni WebSocket bağlantısını kabul eder ve kayıt altına alır."""
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"[WS] '{username}' connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, username: str):
        """Bağlantıyı kapatır ve listeden çıkarır. Devam eden aramayı da temizler."""
        self.active_connections.pop(username, None)
        # Kullanıcı bir aramanın içindeyse, call_id'yi temizle
        call_id = self.active_calls.pop(username, None)
        if call_id:
            # Timeout task varsa iptal et
            task = self.call_timers.pop(call_id, None)
            if task and not task.done():
                task.cancel()
        print(f"[WS] '{username}' disconnected. Active connections: {len(self.active_connections)}")

    def is_online(self, username: str) -> bool:
        """Kullanıcının şu anda bağlı olup olmadığını kontrol eder."""
        return username in self.active_connections

    def is_in_call(self, username: str) -> bool:
        """Kullanıcının şu anda bir aramada olup olmadığını kontrol eder."""
        return username in self.active_calls

    def start_call(self, caller: str, callee: str, call_id: str):
        """Aramayı başlat — her iki tarafı active_calls'a ekle."""
        self.active_calls[caller] = call_id
        self.active_calls[callee] = call_id

    def end_call(self, call_id: str):
        """Aramayı sonlandır — her iki tarafı active_calls'tan çıkar."""
        # call_id üzerinden her iki kullanıcıyı bul ve sil
        to_remove = [u for u, cid in self.active_calls.items() if cid == call_id]
        for u in to_remove:
            self.active_calls.pop(u, None)
        # Timeout task varsa iptal et
        task = self.call_timers.pop(call_id, None)
        if task and not task.done():
            task.cancel()

    async def send_to_user(self, username: str, message: dict):
        """Belirli bir kullanıcıya JSON mesaj gönderir."""
        ws = self.active_connections.get(username)
        if ws:
            await ws.send_json(message)
            return True
        return False

# Global bağlantı yöneticisi örneği
manager = ConnectionManager()
