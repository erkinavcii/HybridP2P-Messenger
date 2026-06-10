"""
message_store.py — Yerel Mesaj Geçmişi Yöneticisi
===================================================
Bu modül, istemci cihazında yerel mesaj geçmişini SQLite ile yönetir.

Temel Prensipler:
  • Plaintext mesajlar cihazda saklanır — cihaz şifreli olduğu varsayımıyla
    (iOS/Android full-disk encryption, Windows BitLocker vb.)
  • Ephemeral moddaki mesajlar asla diske yazılmaz (RAM'de tutulur, kapanınca gider)
  • View-once mesajlar da asla diske yazılmaz
  • Sunucudaki veriler zaten Zero-Knowledge — bu modül sadece yerel tarafı yönetir

Veritabanı konumu:
  ~/.hybridp2p_messenger/{username}/messages.db
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone


KEYS_DIR = Path.home() / ".hybridp2p_messenger"


class MessageStore:
    """
    Yerel mesaj geçmişi ve chat ayarlarını yöneten sınıf.
    Her kullanıcı için ayrı bir SQLite veritabanı dosyası kullanılır.
    """

    def __init__(self, username: str):
        self.username = username
        self.db_path = KEYS_DIR / username / "messages.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Veritabanı Başlatma ─────────────────────────────────────────

    def _init_db(self):
        """
        Gerekli tabloları oluşturur.
        Uygulama her açılışında güvenle çağrılabilir (IF NOT EXISTS).
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Chat kayıtları — her sohbet çifti için bir satır
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id      TEXT PRIMARY KEY,
                partner      TEXT NOT NULL,
                ephemeral    INTEGER NOT NULL DEFAULT 0,
                changed_by   TEXT,
                changed_at   TEXT,
                created_at   TEXT NOT NULL,
                is_group     INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Mesaj geçmişi
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      TEXT NOT NULL,
                sender       TEXT NOT NULL,
                content      TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                is_mine      INTEGER NOT NULL DEFAULT 0,
                msg_type     TEXT NOT NULL DEFAULT 'text',
                is_read      INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        # Grup simetrik anahtarları tablosu
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_keys (
                group_id     TEXT PRIMARY KEY,
                key_hex      TEXT NOT NULL
            )
        """)

        # Contacts tablosu — lokal kimlik kartları için
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                username     TEXT PRIMARY KEY,
                public_key   TEXT NOT NULL,
                fingerprint  TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)

        # Eski veritabanları için is_group sütununu ekle
        try:
            cursor.execute("ALTER TABLE chats ADD COLUMN is_group INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # Eski veritabanları için msg_type sütununu ekle
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN msg_type TEXT DEFAULT 'text'")
        except sqlite3.OperationalError:
            pass

        # Eski veritabanları için is_read sütununu ekle
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()

    # ── Yardımcı ───────────────────────────────────────────────────

    def _chat_id(self, partner: str) -> str:
        """
        İki kullanıcı arasındaki tutarlı chat ID üretir.
        Sıralama yapılır → alice_bob == bob_alice.
        Grup ID'leri doğrudan döndürülür.
        """
        if partner.startswith("group_"):
            return partner
        return "_".join(sorted([self.username, partner]))

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Chat Yönetimi ───────────────────────────────────────────────

    def get_or_create_chat(self, partner: str) -> str:
        """
        Bir partner için chat kaydı döndürür veya oluşturur.
        Returns: chat_id
        """
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        try:
            exists = conn.execute(
                "SELECT chat_id FROM chats WHERE chat_id = ?", (cid,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO chats (chat_id, partner, ephemeral, created_at) VALUES (?, ?, 0, ?)",
                    (cid, partner, self._now())
                )
                conn.commit()
            return cid
        finally:
            conn.close()

    def get_chat_info(self, partner: str) -> dict:
        """Chat bilgilerini döndürür (ephemeral durumu dahil)."""
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM chats WHERE chat_id = ?", (cid,)
            ).fetchone()
            if row:
                return dict(row)
            return {"chat_id": cid, "partner": partner, "ephemeral": 0}
        finally:
            conn.close()

    # ── Ephemeral Mod Yönetimi ──────────────────────────────────────

    def is_ephemeral(self, partner: str) -> bool:
        """
        Bu chat şu an ephemeral (geçici) modda mı?
        Ephemeral moddaki mesajlar yerel geçmişe kaydedilmez.
        """
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT ephemeral FROM chats WHERE chat_id = ?", (cid,)
            ).fetchone()
            return bool(row[0]) if row else False
        finally:
            conn.close()

    def set_ephemeral(self, partner: str, ephemeral: bool, changed_by: str = None):
        """
        Chat'in ephemeral modunu günceller.

        Args:
            partner: Sohbet edilen kullanıcı.
            ephemeral: True → geçici mod açık, False → kayıt açık.
            changed_by: Modu kimin değiştirdiği (bildirim için).
        """
        self.get_or_create_chat(partner)  # Yoksa oluştur
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """UPDATE chats
                   SET ephemeral = ?, changed_by = ?, changed_at = ?
                   WHERE chat_id = ?""",
                (1 if ephemeral else 0, changed_by or self.username, self._now(), cid)
            )
            conn.commit()
        finally:
            conn.close()

    # ── Mesaj Kaydetme ──────────────────────────────────────────────

    def save_message(
        self,
        partner: str,
        sender: str,
        content: str,
        is_mine: bool,
        timestamp: str = None,
        is_view_once: bool = False,
        msg_type: str = "text",
        is_read: int = None,
    ) -> bool:
        """
        Mesajı yerel geçmişe kaydeder.

        Kaydetmez eğer:
          - Chat ephemeral modundaysa
          - Mesaj view_once ise

        Returns:
            True → kaydedildi, False → kaydedilmedi (ephemeral/view_once)
        """
        # ── Ephemeral veya view-once → asla kaydetme ──
        if self.is_ephemeral(partner) or is_view_once:
            return False

        self.get_or_create_chat(partner)
        cid = self._chat_id(partner)
        ts = timestamp or self._now()

        if is_read is None:
            is_read = 1 if is_mine else 0

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO messages
                   (chat_id, sender, content, timestamp, is_mine, msg_type, is_read)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cid, sender, content, ts, 1 if is_mine else 0, msg_type, is_read)
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def save_system_event(self, partner: str, content: str):
        """
        Sistem mesajı kaydeder (örn: "Ephemeral mod açıldı").
        Bu mesajlar ephemeral modda bile kaydedilir — geçmiş için değil,
        bilgi amaçlı ve kısa olduğundan RAM'e de yazılabilir.
        Şu an sadece non-ephemeral'da kaydediyoruz.
        """
        self.save_message(
            partner=partner,
            sender="sistem",
            content=content,
            is_mine=False,
            msg_type="system",
        )

    # ── Mesaj Okuma ─────────────────────────────────────────────────

    def mark_as_read(self, partner: str):
        """Sohbetteki okunmamış mesajları okundu olarak işaretler."""
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE messages SET is_read = 1 WHERE chat_id = ? AND is_mine = 0 AND is_read = 0",
                (cid,)
            )
            conn.commit()
        finally:
            conn.close()

    def mark_sent_messages_as_read(self, partner: str, max_timestamp: str):
        """Karşı tarafın gönderdiğimiz mesajları okuduğunu yerel DB'de işaretler."""
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE messages SET is_read = 1 WHERE chat_id = ? AND is_mine = 1 AND timestamp = ? AND is_read = 0",
                (cid, max_timestamp)
            )
            conn.commit()
        except Exception as ex:
            print(f"[DB Error] mark_sent_messages_as_read error: {ex}")
        finally:
            conn.close()

    def get_messages(self, partner: str, limit: int = 200) -> list:
        """
        Belirli bir partnerle olan mesaj geçmişini döndürür.
        En eskiden en yeniye sıralı.
        """
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT sender, content, timestamp, is_mine, msg_type, is_read
                   FROM messages
                   WHERE chat_id = ?
                   ORDER BY timestamp ASC
                   LIMIT ?""",
                (cid, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_chats(self) -> list:
        """Tüm chat'leri son mesajlarıyla ve okunmamış sayılarıyla listeler (chat listesi için)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT c.chat_id, c.partner, c.ephemeral, c.changed_at, c.is_group,
                          m.content as last_message, m.timestamp as last_time,
                          (SELECT COUNT(*) FROM messages WHERE chat_id = c.chat_id AND is_mine = 0 AND is_read = 0) as unread_count
                   FROM chats c
                   LEFT JOIN messages m ON m.id = (
                       SELECT MAX(id) FROM messages WHERE chat_id = c.chat_id
                   )
                   ORDER BY COALESCE(m.timestamp, c.created_at) DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def search_chats_and_messages(self, query: str) -> dict:
        """
        Arama sorgusuna göre eşleşen sohbetleri ve mesajları döndürür.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            # 1. Partner ismi eşleşen sohbetler
            chat_rows = conn.execute(
                """SELECT c.chat_id, c.partner, c.ephemeral, c.changed_at, c.is_group,
                          m.content as last_message, m.timestamp as last_time,
                          (SELECT COUNT(*) FROM messages WHERE chat_id = c.chat_id AND is_mine = 0 AND is_read = 0) as unread_count
                   FROM chats c
                   LEFT JOIN messages m ON m.id = (
                       SELECT MAX(id) FROM messages WHERE chat_id = c.chat_id
                   )
                   WHERE c.partner LIKE ?
                   ORDER BY COALESCE(m.timestamp, c.created_at) DESC""",
                (f"%{query}%",)
            ).fetchall()

            # 2. İçeriği eşleşen mesajlar
            msg_rows = conn.execute(
                """SELECT c.partner, c.is_group, m.sender, m.content, m.timestamp
                   FROM messages m
                   JOIN chats c ON m.chat_id = c.chat_id
                   WHERE m.content LIKE ? AND m.msg_type NOT IN ('system', 'file')
                   ORDER BY m.timestamp DESC
                   LIMIT 50""",
                (f"%{query}%",)
            ).fetchall()

            return {
                "chats": [dict(r) for r in chat_rows],
                "messages": [dict(r) for r in msg_rows]
            }
        finally:
            conn.close()

    # ── Temizlik ────────────────────────────────────────────────────

    def clear_chat_history(self, partner: str):
        """Belirli bir chat'in tüm mesajlarını siler."""
        cid = self._chat_id(partner)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (cid,))
            conn.commit()
        finally:
            conn.close()

    def clear_all_history(self):
        """Tüm mesaj geçmişini siler (nükleer seçenek)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM messages")
            conn.commit()
        finally:
            conn.close()

    # ── Grup Sohbeti Yönetimi ───────────────────────────────────────

    def get_or_create_group_chat(self, group_id: str, group_name: str) -> str:
        """
        Bir grup için chat kaydı döndürür veya oluşturur.
        Returns: group_id
        """
        conn = sqlite3.connect(self.db_path)
        try:
            exists = conn.execute(
                "SELECT chat_id FROM chats WHERE chat_id = ?", (group_id,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO chats (chat_id, partner, ephemeral, is_group, created_at) VALUES (?, ?, 0, 1, ?)",
                    (group_id, group_name, self._now())
                )
                conn.commit()
            else:
                # Update group name in case it changed
                conn.execute(
                    "UPDATE chats SET partner = ? WHERE chat_id = ?",
                    (group_name, group_id)
                )
                conn.commit()
            return group_id
        finally:
            conn.close()

    def save_group_key(self, group_id: str, key_hex: str):
        """Grubun simetrik AES anahtarını yerel olarak kaydeder."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO group_keys (group_id, key_hex) VALUES (?, ?)",
                (group_id, key_hex)
            )
            conn.commit()
        finally:
            conn.close()

    def get_group_key(self, group_id: str) -> bytes:
        """Grubun yerel simetrik anahtarını bytes olarak döndürür."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT key_hex FROM group_keys WHERE group_id = ?", (group_id,)
            ).fetchone()
            if row:
                return bytes.fromhex(row[0])
            return None
        finally:
            conn.close()

    def save_contact(self, username: str, public_key: str, fingerprint: str):
        """Kullanıcının public key ve fingerprint bilgilerini yerel contacts tablosuna kaydeder."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO contacts (username, public_key, fingerprint, created_at)
                   VALUES (?, ?, ?, ?)""",
                (username, public_key, fingerprint, self._now())
            )
            conn.commit()
        finally:
            conn.close()

    def get_contact(self, username: str) -> dict:
        """Kayıtlı bir kullanıcının yerel bilgilerini döndürür."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM contacts WHERE username = ?", (username,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_group_message(
        self,
        group_id: str,
        sender: str,
        content: str,
        is_mine: bool,
        timestamp: str = None,
        msg_type: str = "text"
    ) -> bool:
        """Grup mesajını yerel geçmişe kaydeder."""
        ts = timestamp or self._now()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO messages
                   (chat_id, sender, content, timestamp, is_mine, msg_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (group_id, sender, content, ts, 1 if is_mine else 0, msg_type)
            )
            conn.commit()
            return True
        finally:
            conn.close()
