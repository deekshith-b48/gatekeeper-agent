from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/gatekeeper.db"
    admin_token: str = ""  # empty = admin REST disabled
    call_token: str = ""  # empty = calls open (set it when exposed beyond localhost)

    # LLM / guard. provider "ollama" (local, ~2-5 GB of models) or "openai" (any OpenAI-compatible API:
    # Groq, Google Gemini, OpenRouter... free tiers, nothing stored locally)
    llm_provider: Literal["ollama", "openai"] = "ollama"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_fallback_model: str = "qwen2.5:3b"
    llm_timeout_s: float = 60.0
    guard_enabled: bool = True
    guard_model: str = "llama-guard3:1b"  # for provider=openai, set to a hosted guard model or leave GUARD_ENABLED=false (rules still run)
    max_input_chars: int = 1500

    # Voice
    whisper_model: str = "base"
    whisper_compute: str = "int8"
    stt_partials: bool = False
    piper_voice: str = "en_US-lessac-medium"
    piper_voice_dir: str = "voices"
    vad_threshold: float = 0.5
    vad_silence_ms: int = 700
    max_utterance_s: int = 30

    # Calendar
    calendar_provider: Literal["google", "caldav"] = "google"
    google_credentials_path: str = "credentials.json"
    google_token_path: str = "token.json"
    google_calendar_id: str = "primary"
    caldav_url: str = ""
    caldav_user: str = ""
    caldav_password: str = ""
    caldav_calendar: str = ""

    # Notifications (ntfy)
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""

    # Owner seed (used only when no owner with this handle exists yet)
    owner_handle: str = "owner"
    owner_name: str = "the owner"
    owner_email: str = ""
    owner_timezone: str = "UTC"
    owner_work_start: str = "09:00"
    owner_work_end: str = "18:00"
    owner_min_start: str = "11:00"
    owner_max_meeting_min: int = 30
    owner_buffer_min: int = 10
    owner_work_days: str = "0,1,2,3,4"  # Mon=0
    owner_vip_contacts: str = ""  # comma-separated verified contact ids

    # Rate limits
    rl_connect_per_min: int = 10
    rl_turns_per_min: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
