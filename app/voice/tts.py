import io
import logging
import os
import time
import wave

log = logging.getLogger("tts")


class PiperTTS:
    def __init__(self, voice: str, voice_dir: str):
        from piper import PiperVoice
        path = os.path.join(voice_dir, f"{voice}.onnx")
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} missing. Download: python -m piper.download_voices {voice} --download-dir {voice_dir}")
        self.voice = PiperVoice.load(path)
        self.sample_rate = self.voice.config.sample_rate

    def synthesize(self, text: str) -> tuple[bytes, int, int]:
        """Returns (pcm_s16le bytes, sample_rate, ms)."""
        t0 = time.monotonic()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self.voice.synthesize_wav(text, w)
        buf.seek(0)
        with wave.open(buf, "rb") as r:
            pcm = r.readframes(r.getnframes())
        return pcm, self.sample_rate, int((time.monotonic() - t0) * 1000)
