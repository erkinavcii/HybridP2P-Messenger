import av
import numpy as np
import sounddevice as sd
import asyncio

print("PyAV version:", av.__version__)
print("Sounddevice version:", sd.__version__)

async def test_audio():
    # Test creating an AudioFrame from a numpy array
    arr = np.zeros((1, 960), dtype=np.int16)
    frame = av.AudioFrame.from_ndarray(arr, format='s16', layout='mono')
    frame.sample_rate = 48000
    print("AudioFrame created successfully! Format:", frame.format, "Layout:", frame.layout)

asyncio.run(test_audio())
