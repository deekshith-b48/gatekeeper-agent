"""Silero VAD: turns a stream of 16 kHz int16 PCM into complete utterances."""
import numpy as np

SR, WIN = 16000, 512


class UtteranceDetector:
    def __init__(self, model, threshold: float, silence_ms: int, max_s: int):
        from silero_vad import VADIterator
        self.it = VADIterator(model, threshold=threshold, sampling_rate=SR, min_silence_duration_ms=silence_ms, speech_pad_ms=120)
        self.max_samples = max_s * SR
        self._tail = np.zeros(0, np.float32)
        self._preroll: list[np.ndarray] = []
        self._speech: list[np.ndarray] = []
        self.in_speech = False

    @property
    def speech_samples(self) -> int:
        return sum(len(x) for x in self._speech)

    def snapshot(self) -> np.ndarray:
        return np.concatenate(self._speech) if self._speech else np.zeros(0, np.float32)

    def feed(self, pcm: bytes) -> list[np.ndarray]:
        import torch
        data = np.concatenate([self._tail, np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0])
        out = []
        n = len(data) // WIN * WIN
        for i in range(0, n, WIN):
            w = data[i:i + WIN]
            ev = self.it(torch.from_numpy(w))
            if ev and "start" in ev and not self.in_speech:
                self.in_speech, self._speech = True, list(self._preroll)
            if self.in_speech:
                self._speech.append(w)
            else:
                self._preroll = (self._preroll + [w])[-6:]  # ~190 ms pre-roll
            if (ev and "end" in ev and self.in_speech) or (self.in_speech and self.speech_samples >= self.max_samples):
                out.append(self.snapshot())
                self.in_speech, self._speech = False, []
                self.it.reset_states()
        self._tail = data[n:]
        return out
