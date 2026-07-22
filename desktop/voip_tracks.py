"""desktop/voip_tracks.py — WebRTC medya track yardımcı sınıfları.

client.py'den taşındı (modülerleştirme): MicrophoneTrack, AudioPlayer, CameraTrack.
Bu sınıflar main()'in closure state'ine bağımlı değildir; module-level olarak
tanımlanmıştı ve davranışları hiç değiştirilmeden buraya taşındı.
"""

import asyncio
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
        frame.time_base = av.Fraction(1, self.sample_rate)
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


class AudioPlayer:
    def __init__(self, track):
        self.track = track
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.sample_rate = 48000
        self.channels = 1
        self.frame_size = 960
        self.running = True
        self.play_task = None

        def callback(outdata, frames, time_info, status):
            if status:
                print(f"[AudioPlayer] Status: {status}")
            try:
                if not self.queue.empty():
                    data = self.queue.get_nowait()
                    outdata[:] = data
                else:
                    outdata.fill(0)
            except Exception as e:
                outdata.fill(0)

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
                data = frame.to_ndarray().T
                await self.queue.put(data)
            except Exception as e:
                print("[AudioPlayer] Error receiving frame:", e)
                break

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
        self.fps_interval = 1.0 / 15.0

    async def recv(self):
        now = time.time()
        elapsed = now - self.last_frame_time
        if elapsed < self.fps_interval:
            await asyncio.sleep(self.fps_interval - elapsed)

        if not self.running or not self.cap:
            raise Exception("Track stopped")

        ret, frame = self.cap.read()
        self.last_frame_time = time.time()

        if not self.enabled:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
        else:
            if not ret:
                img = np.zeros((480, 640, 3), dtype=np.uint8)
            else:
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        v_frame = VideoFrame.from_ndarray(img, format='rgb24')
        if not hasattr(self, "_pts"):
            self._pts = 0
        v_frame.pts = self._pts
        v_frame.time_base = av.Fraction(1, 90000)
        self._pts += 6000
        return v_frame

    def stop(self):
        self.running = False
        if hasattr(self, "cap") and self.cap:
            try:
                self.cap.release()
            except Exception as e:
                print("Error releasing cv2.VideoCapture:", e)
            self.cap = None
