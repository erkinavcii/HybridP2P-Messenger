import aiosqlite
from contextlib import asynccontextmanager
from server.config import DB_PATH

async def init_database():
    """
    Initializes the SQLite database and creates the required tables.
    Can be safely called every time the application starts.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Kullanıcılar tablosu — public key'leri saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username    TEXT PRIMARY KEY,
                public_key  TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Çevrimdışı mesajlar tablosu — şifreli payload'ları geçici saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS offline_msgs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                sender              TEXT NOT NULL,
                recipient           TEXT NOT NULL,
                encrypted_payload   TEXT NOT NULL DEFAULT '',
                msg_type            TEXT NOT NULL DEFAULT 'message',
                extra_data          TEXT NOT NULL DEFAULT '{}',
                timestamp           TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Chat ayarları — ephemeral mod durumunu saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id     TEXT PRIMARY KEY,
                ephemeral   INTEGER NOT NULL DEFAULT 0,
                changed_by  TEXT,
                changed_at  TEXT
            )
        """)

        # Dosya depolama — sifrelenmis dosya blob'larini gecici saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS file_store (
                uuid            TEXT PRIMARY KEY,
                sender          TEXT NOT NULL,
                recipient       TEXT NOT NULL,
                encrypted_data  TEXT NOT NULL,
                original_name   TEXT NOT NULL,
                file_type       TEXT NOT NULL DEFAULT 'document',
                timestamp       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Gruplar tablosu — grup bilgilerini saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id    TEXT PRIMARY KEY,
                group_name  TEXT NOT NULL,
                creator     TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Grup üyeleri tablosu — grup üyeliklerini saklar
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id    TEXT NOT NULL,
                username    TEXT NOT NULL,
                PRIMARY KEY (group_id, username),
                FOREIGN KEY (group_id) REFERENCES groups(group_id),
                FOREIGN KEY (username) REFERENCES users(username)
            )
        """)

        await db.commit()


@asynccontextmanager
async def db_session():
    """Asenkron veritabanı bağlantısı ve context manager sağlar."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row  # Sütun ismiyle erişim için
    try:
        # sys.path ve importlar için absolute import desteği
        yield db
    finally:
        await db.close()
