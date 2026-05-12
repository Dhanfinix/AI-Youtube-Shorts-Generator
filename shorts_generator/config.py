import os

from dotenv import load_dotenv

load_dotenv()

# Core Gemini AI settings
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
LOCAL_LLM_PROVIDER = "gemini"

LOCAL_TRANSCRIBER_PRIORITY = os.getenv("LOCAL_TRANSCRIBER_PRIORITY", "youtube").strip().lower()  # youtube / gemini
LOCAL_TARGET_LANGUAGE = os.getenv("LOCAL_TARGET_LANGUAGE", "auto").strip().lower()  # auto / id / en / etc.
LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "output")
LOCAL_FACE_DETECTOR = os.getenv("LOCAL_FACE_DETECTOR", "opencv-haar").strip().lower()  # opencv-haar / mediapipe / mediapipe-mesh
USE_GPU = os.getenv("USE_GPU", "true").lower() == "true"
USE_SHOT_LOCK = os.getenv("USE_SHOT_LOCK", "false").lower() == "true"
USE_ASS_SUBTITLES = os.getenv("USE_ASS_SUBTITLES", "true").lower() == "true"
MIN_SHOT_DURATION = int(os.getenv("MIN_SHOT_DURATION", "90"))  # Default 3 seconds at 30 fps
ENABLE_DYNAMIC_ZOOM = os.getenv("ENABLE_DYNAMIC_ZOOM", "true").lower() == "true"







def require_local_llm_key() -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. The pipeline requires a Gemini key. "
            "Add GEMINI_API_KEY to your .env file or export it as an env var."
        )
    return GEMINI_API_KEY
