"""desktop/p2p_files.py — Sunucusuz modda doğrudan dosya aktarımı (arayüzden bağımsız).

Protokol (aynı veri kanalı, sıralı + güvenilir):
  gönderen → {"t":"file_offer","fid","name","size","sha256"}
  alıcı    → {"t":"file_accept","fid"}  ya da  {"t":"file_reject","fid"}
  gönderen → ikili parçalar: 16 bayt fid + en fazla CHUNK_SIZE bayt veri
  gönderen → {"t":"file_end","fid"}
  iki taraf da her an → {"t":"file_cancel","fid"}

Kanal DTLS ile uçtan uca şifreli; burada bütünlük ve dayanıklılık sağlanır:
  • Alıcı onay vermeden hiçbir parça kabul edilmez (istenmeyen dosya yazılamaz).
  • Bildirilen boyut aşılırsa aktarım iptal edilir; sonunda boyut ve SHA-256
    doğrulanır, tutmazsa yarım dosya silinir.
  • Veri önce geçici (.part) dosyaya yazılır; doğrulanınca son adına taşınır.
  • Dosya adı temizlenir (yol, sürücü harfi, ".." , kontrol karakterleri,
    Windows'ta yasak adlar); var olan dosyanın üzerine asla yazılmaz.
"""

import hashlib
import os
import re
import secrets
from pathlib import Path

CHUNK_SIZE = 16 * 1024                 # tarayıcılarla uyumlu güvenli boyut
MAX_FILE_BYTES = 1024 * 1024 * 1024    # 1 GB
MAX_ACTIVE_INCOMING = 3
FID_BYTES = 16
SEND_BUFFER_HIGH = 1024 * 1024         # gönderici bu kadar tampondayken bekler

_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


class FileTransferError(Exception):
    pass


def new_fid() -> str:
    return secrets.token_hex(FID_BYTES)


def safe_filename(name: str) -> str:
    """Karşı tarafın verdiği adı güvenli, tek bileşenli bir dosya adına çevirir."""
    name = (name or "").replace("\\", "/").split("/")[-1]       # yol bileşenlerini at
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", name)        # kontrol + Windows'ta yasak
    name = name.strip(" .")                                     # "..", baştaki/sondaki nokta-boşluk
    stem = name.split(".")[0].upper()
    if not name or stem in _WIN_RESERVED:
        name = f"dosya_{name}" if name else "dosya"
    if len(name) > 150:                                          # uzantıyı koruyarak kısalt
        root, dot, ext = name.rpartition(".")
        name = (root[:140] + "." + ext[:9]) if dot and len(ext) <= 10 else name[:150]
    return name


def unique_path(folder: Path, name: str) -> Path:
    """Var olan dosyanın üzerine yazmamak için 'ad (2).ext' biçiminde benzersiz yol."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    if not p.exists():
        return p
    root, dot, ext = name.rpartition(".")
    if not dot:
        root, ext = name, ""
    for i in range(2, 10_000):
        cand = folder / (f"{root} ({i}).{ext}" if ext else f"{root} ({i})")
        if not cand.exists():
            return cand
    raise FileTransferError("benzersiz dosya adı bulunamadı")


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / "HybridP2P"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ─────────────────────────── çerçeveler ───────────────────────────

def chunk_frame(fid: str, data: bytes) -> bytes:
    return bytes.fromhex(fid) + data


def split_chunk(raw: bytes) -> tuple[str, bytes]:
    if len(raw) <= FID_BYTES or len(raw) > FID_BYTES + CHUNK_SIZE:
        raise FileTransferError("geçersiz parça boyutu")
    return raw[:FID_BYTES].hex(), raw[FID_BYTES:]


# ─────────────────────────── gelen dosya ───────────────────────────

class IncomingFile:
    """Kabul edilmiş tek bir gelen dosya. feed() parçaları yazar, finish() doğrular."""

    def __init__(self, fid: str, name: str, size: int, sha256: str, folder: Path):
        if not (0 < size <= MAX_FILE_BYTES):
            raise FileTransferError(f"dosya boyutu sınır dışı ({fmt_size(size)})")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
            raise FileTransferError("geçersiz SHA-256")
        self.fid, self.size, self.sha256 = fid, size, sha256
        self.name = safe_filename(name)
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.part = folder / f".{fid}.part"
        self._f = open(self.part, "wb")
        self._h = hashlib.sha256()
        self.received = 0
        self.final_path = None

    def feed(self, data: bytes):
        if self._f is None:
            raise FileTransferError("aktarım kapalı")
        if self.received + len(data) > self.size:
            self.abort()
            raise FileTransferError("bildirilen boyut aşıldı")
        self._f.write(data)
        self._h.update(data)
        self.received += len(data)

    def finish(self) -> Path:
        if self._f is None:
            raise FileTransferError("aktarım kapalı")
        self._f.close()
        self._f = None
        if self.received != self.size:
            self._discard()
            raise FileTransferError(f"eksik dosya ({self.received}/{self.size} bayt)")
        if self._h.hexdigest() != self.sha256:
            self._discard()
            raise FileTransferError("SHA-256 tutmuyor — dosya bozuk ya da değiştirilmiş")
        self.final_path = unique_path(self.folder, self.name)
        os.replace(self.part, self.final_path)
        return self.final_path

    def abort(self):
        if self._f is not None:
            self._f.close()
            self._f = None
        self._discard()

    def _discard(self):
        try:
            self.part.unlink()
        except FileNotFoundError:
            pass

    @property
    def progress(self) -> float:
        return self.received / self.size if self.size else 1.0


class IncomingRegistry:
    """Bekleyen teklifler ve kabul edilmiş aktarımlar; istenmeyen parçaları reddeder."""

    def __init__(self, folder: Path | None = None):
        self.folder = folder or default_download_dir()
        self.offers: dict[str, dict] = {}
        self.active: dict[str, IncomingFile] = {}

    def offer(self, frame: dict):
        if len(self.offers) + len(self.active) >= MAX_ACTIVE_INCOMING * 2:
            raise FileTransferError("çok fazla bekleyen dosya")
        self.offers[frame["fid"]] = frame

    def accept(self, fid: str) -> IncomingFile:
        if len(self.active) >= MAX_ACTIVE_INCOMING:
            raise FileTransferError("aynı anda en fazla 3 dosya alınabilir")
        f = self.offers.pop(fid, None)
        if f is None:
            raise FileTransferError("böyle bir teklif yok")
        inc = IncomingFile(fid, f["name"], f["size"], f["sha256"], self.folder)
        self.active[fid] = inc
        return inc

    def reject(self, fid: str):
        self.offers.pop(fid, None)

    def chunk(self, raw: bytes) -> IncomingFile:
        fid, data = split_chunk(raw)
        inc = self.active.get(fid)
        if inc is None:
            raise FileTransferError("kabul edilmemiş dosyaya ait parça — yok sayıldı")
        inc.feed(data)
        return inc

    def end(self, fid: str) -> Path:
        inc = self.active.pop(fid, None)
        if inc is None:
            raise FileTransferError("bilinmeyen aktarım")
        return inc.finish()

    def cancel(self, fid: str):
        self.offers.pop(fid, None)
        inc = self.active.pop(fid, None)
        if inc:
            inc.abort()

    def abort_all(self):
        for fid in list(self.active):
            self.cancel(fid)
        self.offers.clear()


# ─────────────────────────── giden dosya ───────────────────────────

def make_offer(path: Path) -> dict:
    """Gönderilecek dosya için teklif çerçevesi (SHA-256 burada hesaplanır)."""
    size = path.stat().st_size
    if size == 0:
        raise FileTransferError("boş dosya gönderilemez")
    if size > MAX_FILE_BYTES:
        raise FileTransferError(f"dosya çok büyük (en fazla {fmt_size(MAX_FILE_BYTES)})")
    return {"t": "file_offer", "fid": new_fid(), "name": safe_filename(path.name),
            "size": size, "sha256": file_sha256(path)}


async def send_file(channel, path: Path, fid: str, on_progress=None, is_cancelled=lambda: False):
    """Dosyayı parçalar halinde gönderir (P2P loop'unda çalıştırın). Kanal tamponu
    dolunca bekler: büyük dosya belleği şişirmez."""
    import asyncio
    import json
    sent = 0
    size = path.stat().st_size
    with open(path, "rb") as f:
        while True:
            if is_cancelled():
                return False
            if channel.readyState != "open":
                raise FileTransferError("bağlantı kapandı")
            while channel.bufferedAmount > SEND_BUFFER_HIGH:
                await asyncio.sleep(0.02)
            block = f.read(CHUNK_SIZE)
            if not block:
                break
            channel.send(chunk_frame(fid, block))
            sent += len(block)
            if on_progress:
                on_progress(sent / size)
    channel.send(json.dumps({"t": "file_end", "fid": fid}))
    return True
