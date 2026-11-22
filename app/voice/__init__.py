import logging

from app.core.config import Settings

log = logging.getLogger("voice")


class VoiceStack:
    """Loads STT/VAD/TTS once. Heavy imports are lazy so the text path works without the `voice` extra."""

    def __init__(self, st: Settings):
        from app.voice.stt import WhisperSTT
        import silero_vad
        self.st = st
        self.stt = WhisperSTT(st.whisper_model, st.whisper_compute)
        self._vad_model = silero_vad.load_silero_vad()
        try:
            from app.voice.tts import PiperTTS
            self.tts = PiperTTS(st.piper_voice, st.piper_voice_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("TTS disabled: %s", e)
            self.tts = None

    def new_detector(self):
        from app.voice.vad import UtteranceDetector
        # silero's model keeps per-stream state; copy so concurrent calls don't share it
        import copy
        return UtteranceDetector(copy.deepcopy(self._vad_model), self.st.vad_threshold, self.st.vad_silence_ms, self.st.max_utterance_s)


def load_voice(st: Settings) -> "VoiceStack | None":
    try:
        return VoiceStack(st)
    except ImportError as e:
        log.warning("voice stack not installed (%s); text-only mode. Install with: pip install '.[voice]'", e)
        return None
