"""desktop/voip_tracks.py — WebRTC medya track yardımcı sınıfları.

client.py'den taşındı (modülerleştirme): MicrophoneTrack, AudioPlayer, CameraTrack.
Bu sınıflar main()'in closure state'ine bağımlı değildir; module-level olarak
tanımlanmıştı ve davranışları hiç değiştirilmeden buraya taşındı.
"""

import asyncio
from fractions import Fraction
import threading
import time

import av
import sounddevice as sd
import cv2
import numpy as np
from aiortc import MediaStreamTrack, VideoStreamTrack
from av import VideoFrame


class MicrophoneTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.sample_rate = 48000
        self.channels = 1
        self.frame_size = 960
        self.enabled = True

        def callback(indata, frames, time_info, status):
            if status:
                print(f"[MicTrack] Status: {status}")
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, indata.copy())
            except Exception as e:
                pass

        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            blocksize=self.frame_size,
            callback=callback
        )
        self.stream.start()

    async def recv(self):
        if self.stream is None:
            raise Exception("Track stopped")

        data = await self.queue.get()
        if not self.enabled:
            data = np.zeros_like(data)

        data_transposed = data.T
        frame = av.AudioFrame.from_ndarray(data_transposed, format='s16', layout='mono')
        frame.sample_rate = self.sample_rate
        if not hasattr(self, "_pts"):
            self._pts = 0
        frame.pts = self._pts
        frame.time_base = Fraction(1, self.sample_rate)
        self._pts += self.frame_size
        return frame

    def stop(self):
        if hasattr(self, "stream") and self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print("Error stopping sd.InputStream:", e)
            self.stream = None


def to_mono_s16(frame) -> np.ndarray:
    """Gelen ses karesini tek kanallı int16 diziye çevirir. aiortc Opus'u her zaman
    STEREO çözer (960 örnek → 1920 değer); mono çıkışa doğrudan yazmak boyut hatası
    verir ve sessizlik çalınırdı."""
    arr = frame.to_ndarray()
    ch = len(frame.layout.channels)
    if ch > 1:
        arr = arr.mean(axis=0) if frame.format.is_planar else arr.reshape(-1, ch).mean(axis=1)
    return np.asarray(arr, dtype=np.float64).reshape(-1).clip(-32768, 32767).astype(np.int16)


class AudioPlayer:
    MAX_BUFFER = 24000          # en fazla 0,5 sn birikir; fazlası (eski ses) atılır → gecikme büyümez

    def __init__(self, track):
        self.track = track
        self.loop = asyncio.get_running_loop()
        self.sample_rate = 48000
        self.channels = 1
        self.frame_size = 960
        self.running = True
        self.play_task = None
        # Ses kartının iş parçacığıyla paylaşılır (asyncio.Queue iş parçacığı güvenli değil)
        self._buf = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()

        def callback(outdata, frames, time_info, status):
            if status:
                print(f"[AudioPlayer] Status: {status}")
            with self._lock:
                n = min(frames, len(self._buf))
                outdata[:n, 0] = self._buf[:n]
                self._buf = self._buf[n:]
            outdata[n:] = 0

        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            blocksize=self.frame_size,
            callback=callback
        )
        self.stream.start()

    def start(self):
        self.play_task = asyncio.create_task(self._run())

    async def _run(self):
        while self.running:
            try:
                frame = await self.track.recv()
                self.feed(to_mono_s16(frame))
            except Exception as e:
                print("[AudioPlayer] Error receiving frame:", e)
                break

    def feed(self, samples: np.ndarray):
        with self._lock:
            self._buf = np.concatenate([self._buf, samples])[-self.MAX_BUFFER:]

    def stop(self):
        self.running = False
        if self.play_task:
            self.play_task.cancel()
        if hasattr(self, "stream") and self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print("Error stopping sd.OutputStream:", e)
            self.stream = None


class CameraTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        self.running = True
        self.enabled = True
        self.last_frame_time = 0
        # Gönderilen boyut/kare hızı: zayıf bağlantıda desktop/call_quality düşürür
        self.out_size = (640, 480)
        self.fps_interval = 1.0 / 15.0
        # Önizleme bu kareyi okur; track.recv()'i yalnızca gönderici çağırmalı (iki
        # okuyucu kareleri paylaşır, gönderilen akışın kare hızı yarıya düşerdi)
        self.last_image = None
        self._t0 = None

    def set_quality(self, width: int, height: int, fps: int):
        self.out_size = (int(width), int(height))
        self.fps_interval = 1.0 / max(1, int(fps))

    async def recv(self):
        now = time.time()
        elapsed = now - self.last_frame_time
        if elapsed < self.fps_interval:
            await asyncio.sleep(self.fps_interval - elapsed)

        if not self.running or not self.cap:
            raise Exception("Track stopped")

        ret, frame = self.cap.read()
        self.last_frame_time = time.time()

        w, h = self.out_size
        if not self.enabled or not ret:
            img = np.zeros((h, w, 3), dtype=np.uint8)
        else:
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if (img.shape[1], img.shape[0]) != (w, h):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        self.last_image = img

        v_frame = VideoFrame.from_ndarray(img, format='rgb24')
        # Zaman damgası gerçek zamandan: kare hızı görüşme sırasında değişebilir
        if self._t0 is None:
            self._t0 = self.last_frame_time
        v_frame.pts = int((self.last_frame_time - self._t0) * 90000)
        v_frame.time_base = Fraction(1, 90000)
        return v_frame

    def stop(self):
        self.running = False
        if hasattr(self, "cap") and self.cap:
            try:
                self.cap.release()
            except Exception as e:
                print("Error releasing cv2.VideoCapture:", e)
            self.cap = None
