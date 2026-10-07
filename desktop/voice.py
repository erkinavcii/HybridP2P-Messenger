"""desktop/voice.py — Sesli mesaj: kayıt, Opus kodlama/çözme, oynatma, yerel saklama.

Tasarım:
  • Kayıt: sounddevice.InputStream, 48 kHz mono int16 (VoIP MicrophoneTrack deseni).
  • Kodlama: PyAV ile Opus/Ogg (~4 KB/sn; aynı ses WAV olarak ~96 KB/sn).
  • İletim: mevcut E2EE dosya yolu (encrypt_bytes → /api/upload_file → file_message,
    file_type="audio"). Dosya adı "voice-…ogg" — eski istemciler bunu sıradan bir
    ses dosyası olarak görüp indirebilir (geriye dönük uyumlu). Web istemcisi
    Chrome'da "voice-…webm" gönderir (MediaRecorder Ogg kaydedemez); içerik yine
    Opus'tur ve PyAV kabı içerikten tanır, bu yüzden iki uzantı da kabul edilir.
  • Saklama: sunucu dosyayı ilk indirmede siler, bu yüzden alıcı sesi hemen
    indirip ~/.hybridp2p_messenger/{user}/media/ altına yazar. Ephemeral
    sohbetlerde diske hiçbir şey yazılmaz (çağıran taraf kontrol eder).
"""

import io
import threading
import time

import av
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 48000
FRAME = 960                  # 20 ms @ 48 kHz (Opus'un doğal çerçeve boyu)
MAX_SECONDS = 120
MIN_SECONDS = 0.5
VOICE_PREFIX = "voice-"
VOICE_EXT = ".ogg"                      # masaüstünün gönderdiği
VOICE_EXTS = (".ogg", ".webm")          # kabul edilenler (.webm: web istemcisi / Chrome)


def is_voice_file(original_name: str, file_type: str) -> bool:
    return (file_type == "audio" and original_name.startswith(VOICE_PREFIX)
            and original_name.endswith(VOICE_EXTS))


def voice_filename() -> str:
    return f"{VOICE_PREFIX}{int(time.time() * 1000)}{VOICE_EXT}"


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


# ── Kodlama / çözme ─────────────────────────────────────────────────

def encode_opus(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """int16 mono PCM → Ogg/Opus bayt dizisi."""
    buf = io.BytesIO()
    container = av.open(buf, "w", format="ogg")
    stream = container.add_stream("libopus", rate=sample_rate)
    stream.layout = "mono"
    stream.bit_rate = 24000
    for i in range(0, len(pcm), FRAME):
        chunk = pcm[i:i + FRAME]
        if len(chunk) < FRAME:
            chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return buf.getvalue()


def decode_audio(data: bytes) -> tuple[np.ndarray, int]:
    """Ogg/Opus (veya PyAV'ın okuyabildiği herhangi bir ses) → (int16 mono PCM, örnekleme hızı)."""
    container = av.open(io.BytesIO(data))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    parts = []
    for frame in container.decode(audio=0):
        for out in resampler.resample(frame):
            parts.append(out.to_ndarray().reshape(-1))
    container.close()
    pcm = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
    return pcm.astype(np.int16), SAMPLE_RATE


def duration_of(data: bytes) -> float:
    pcm, sr = decode_audio(data)
    return len(pcm) / sr


# ── Kayıt ───────────────────────────────────────────────────────────

class VoiceRecorder:
    """Mikrofondan bellek içine kayıt. MAX_SECONDS'ta kendiliğinden durur
    (on_limit geri çağrısı tetiklenir)."""

    def __init__(self, on_limit=None):
        self._chunks = []
        self._stream = None
        self._started = None
        self._lock = threading.Lock()
        self._on_limit = on_limit
        self._limit_fired = False

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self._started else 0.0

    def start(self):
        """Mikrofon yoksa/meşgulse sounddevice istisnası fırlatır — çağıran yakalamalı."""
        self._chunks = []
        self._limit_fired = False

        def callback(indata, frames, time_info, status):
            with self._lock:
                self._chunks.append(indata[:, 0].copy())
            if self.elapsed >= MAX_SECONDS and not self._limit_fired and self._on_limit:
                self._limit_fired = True
                threading.Thread(target=self._on_limit, daemon=True).start()

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=FRAME, callback=callback)
        self._stream.start()
        self._started = time.monotonic()

    def _close(self):
        stream, self._stream = self._stream, None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception as ex:
                print(f"[Voice] Mikrofon kapatma hatasi: {ex}")

    def stop(self) -> np.ndarray:
        self._close()
        with self._lock:
            pcm = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.int16)
            self._chunks = []
        self._started = None
        return pcm[: SAMPLE_RATE * MAX_SECONDS]

    def cancel(self):
        self._close()
        with self._lock:
            self._chunks = []
        self._started = None


# ── Oynatma ─────────────────────────────────────────────────────────

class VoicePlayer:
    """Aynı anda tek ses çalar. Kendi OutputStream'ini kullanır; bildirim sesinin
    (sd.play) çalan sesli mesajı kesmemesi için global akıştan bağımsızdır."""

    def __init__(self):
        self._stream = None
        self._on_done = None
        self._token = 0

    def play(self, pcm: np.ndarray, sample_rate: int, on_done=None):
        self.stop()
        self._token += 1
        token = self._token
        self._on_done = on_done
        pos = [0]

        def callback(outdata, frames, time_info, status):
            chunk = pcm[pos[0]:pos[0] + frames]
            outdata[:len(chunk), 0] = chunk
            if len(chunk) < frames:
                outdata[len(chunk):, 0] = 0
                raise sd.CallbackStop
            pos[0] += frames

        def finished():
            # stop() ile kesildiyse ya da yerine yenisi başladıysa eski geri çağrı çalışmaz
            if token == self._token:
                self._finish()

        self._stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16",
                                       callback=callback, finished_callback=finished)
        self._stream.start()

    def _finish(self):
        stream, self._stream = self._stream, None
        cb, self._on_done = self._on_done, None
        if stream:
            threading.Thread(target=stream.close, daemon=True).start()
        if cb:
            cb()

    def stop(self):
        if self._stream is None:
            return
        self._token += 1          # bekleyen finished geri çağrısını geçersiz kıl
        stream = self._stream
        try:
            stream.abort()
        except Exception:
            pass
        self._finish()


# Uygulama genelinde tek oynatıcı: yeni bir sesli mesaj başlayınca öncekini durdurur
player = VoicePlayer()


# ── Yerel saklama ───────────────────────────────────────────────────

def media_dir(username: str):
    from message_store import KEYS_DIR
    d = KEYS_DIR / username / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_media(username: str, file_uuid: str, data: bytes) -> str:
    name = f"{file_uuid}{VOICE_EXT}"
    (media_dir(username) / name).write_bytes(data)
    return name


def load_media(username: str, name: str) -> bytes | None:
    # Yalnızca düz dosya adı kabul edilir (yol geçişi yok)
    if "/" in name or "\\" in name or ".." in name:
        return None
    path = media_dir(username) / name
    return path.read_bytes() if path.exists() else None
