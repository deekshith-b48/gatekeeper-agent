import logging
import time

import numpy as np

log = logging.getLogger("stt")


class WhisperSTT:
    def __init__(self, model: str, compute_type: str):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model, device="cpu", compute_type=compute_type)

    def transcribe(self, audio: np.ndarray, beam_size: int = 1) -> tuple[str, str, int]:
        """audio: float32 mono 16 kHz. Returns (text, language, ms)."""
        t0 = time.monotonic()
        if len(audio) < 1600:  # < 100 ms
            return "", "", 0
        segs, info = self.model.transcribe(audio, beam_size=beam_size, vad_filter=False,
                                           condition_on_previous_text=False, no_speech_threshold=0.6)
        text = " ".join(s.text.strip() for s in segs).strip()
        return text, info.language, int((time.monotonic() - t0) * 1000)
