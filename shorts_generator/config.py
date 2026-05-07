import os

from dotenv import load_dotenv

load_dotenv()

MUAPI_API_KEY = os.getenv("MUAPI_API_KEY", "").strip()
MUAPI_BASE_URL = os.getenv("MUAPI_BASE_URL", "https://api.muapi.ai/api/v1").rstrip("/")

POLL_INTERVAL_SECONDS = float(os.getenv("MUAPI_POLL_INTERVAL", "5"))
POLL_TIMEOUT_SECONDS = float(os.getenv("MUAPI_POLL_TIMEOUT", "600"))

# Local-mode (--mode local) settings — only consulted when running offline.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3-flash-preview,gemini-3.1-flash-lite-preview,gemini-2.5-flash,gemma-4-31b-it"
    ).split(",")
    if m.strip()
]
LOCAL_LLM_PROVIDER = os.getenv("LOCAL_LLM_PROVIDER", "").strip().lower()

# Auto-detect provider if not explicitly set
if not LOCAL_LLM_PROVIDER:
    if GEMINI_API_KEY:
        LOCAL_LLM_PROVIDER = "gemini"
    else:
        LOCAL_LLM_PROVIDER = "openai"

LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "base")
LOCAL_WHISPER_DEVICE = os.getenv("LOCAL_WHISPER_DEVICE", "auto")  # auto / cpu / cuda
LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "output")


def require_api_key() -> str:
    if not MUAPI_API_KEY:
        raise RuntimeError(
            "MUAPI_API_KEY is not set. Add it to your .env file or export it as an env var."
        )
    return MUAPI_API_KEY


def require_local_llm_key() -> str:
    if LOCAL_LLM_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Local mode is configured for Gemini but no key is provided. "
                "Add GEMINI_API_KEY to your .env or export it."
            )
        return GEMINI_API_KEY
    else:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Local mode is configured for OpenAI but no key is provided. "
                "Add OPENAI_API_KEY to your .env or export it, or switch to Gemini by setting GEMINI_API_KEY."
            )
        return OPENAI_API_KEY


def require_openai_key() -> str:
    return require_local_llm_key()
