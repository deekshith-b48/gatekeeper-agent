"""Offline check of the real voice stack: Piper speaks a sentence -> 16 kHz PCM -> silero-vad -> faster-whisper -> text."""
import numpy as np

from app.core.config import get_settings
from app.voice import VoiceStack

st = get_settings()
v = VoiceStack(st)
assert v.tts, "Piper voice missing"
text = "Hi, my name is Priya from Acme. Could we book thirty minutes tomorrow afternoon?"
pcm, sr, tts_ms = v.tts.synthesize(text)
x = np.frombuffer(pcm, np.int16).astype(np.float32)
n = int(len(x) * 16000 / sr)
x16 = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)
stream = np.concatenate([np.zeros(8000, np.int16), x16, np.zeros(16000, np.int16)]).tobytes()  # 0.5s lead, 1s trailing silence
det, utts = v.new_detector(), []
for i in range(0, len(stream), 1024):  # realistic small frames
    utts += det.feed(stream[i:i + 1024])
print(f"tts_ms={tts_ms} utterances={len(utts)}")
for u in utts:
    t, lang, ms = v.stt.transcribe(u)
    print(f"stt_ms={ms} lang={lang} -> {t!r}")
