"""Local LLM backend — centralized on Gemini AI using the official SDK."""
from .config import GEMINI_MODEL, require_local_llm_key, GEMINI_FALLBACK_MODELS

def call_gemini_llm(prompt: str) -> str:
    """Core dispatcher sending requests directly via the official google-genai client."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "google-genai is required. Install it with:\n"
            "    pip install google-genai"
        ) from e

    api_key = require_local_llm_key()
    client = genai.Client(api_key=api_key)
    
    primary_model = GEMINI_MODEL or "gemini-2.5-flash"
    # Assemble absolute sequence of fallback models
    models_to_try = [primary_model] + [m for m in GEMINI_FALLBACK_MODELS if m != primary_model]
    
    last_err = None
    for model_id in models_to_try:
        try:
            print(f"[llm] Querying Gemini model: {model_id}...", flush=True)
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                ),
            )
            return response.text or ""
        except Exception as e:
            clean_err = str(e).split('\n')[0]
            print(f"[llm] Warning: {model_id} failed ({clean_err}).", flush=True)
            last_err = e

    raise RuntimeError(f"All configured Gemini endpoints failed. Last trace: {last_err}")

# Backward-compatibility alias for orchestrators relying on original naming convention
call_openai_llm = call_gemini_llm


def get_trending_videos() -> list:
    """Uses Gemini with Search Grounding to discover viral long-form podcasts."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return []

    api_key = require_local_llm_key()
    client = genai.Client(api_key=api_key)
    
    prompt = (
        "Please use Google Search to find active, viral, and currently popular long-form podcasts or talk shows in Indonesia. "
        "Focus especially on Indonesian language content from popular local channels like Vindes, Deddy, Denny Sumargo, and similar creators. "
        "Return raw JSON with an array named 'videos' containing 'title', 'url', and 'date' (YYYY-MM-DD). "
        "Example: {\"videos\": [{\"title\": \"Example Podcast\", \"url\": \"https://www.youtube.com/watch?v=...\", \"date\": \"2025-01-01\"}]}"
    )
    
    try:
        print("[trends] Querying Google Search via Gemini for trending podcasts...", flush=True)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        import json
        raw_text = response.text or "{}"
        # Strip Markdown wrapper if present
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0]
        data = json.loads(raw_text)
        return data.get("videos", [])
    except Exception as e:
        print(f"[trends] Failed to parse or retrieve trending lists: {e}", flush=True)
        return []
