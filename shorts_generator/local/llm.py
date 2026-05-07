"""Local LLM backend — calls OpenAI or Gemini directly so no MuAPI account is needed."""
from ..config import OPENAI_MODEL, GEMINI_MODEL, LOCAL_LLM_PROVIDER, require_local_llm_key, GEMINI_FALLBACK_MODELS


def call_openai_llm(prompt: str) -> str:
    """Local LLM backend used by --mode local (supports OpenAI and Gemini)."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openai is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    provider = LOCAL_LLM_PROVIDER
    api_key = require_local_llm_key()

    if provider == "gemini":
        primary_model = GEMINI_MODEL or "gemma-4-31b-it"
        client = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        
        # Build the exact fallback try sequence
        models_to_try = [primary_model] + [m for m in GEMINI_FALLBACK_MODELS if m != primary_model]
        
        last_err = None
        for model in models_to_try:
            try:
                print(f"[llm] Trying Gemini model: {model}...", flush=True)
                response = client.chat.completions.create(
                    model=model,
                    temperature=0.7,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                print(f"[llm] Model {model} failed: {e}", flush=True)
                last_err = e
        raise RuntimeError(f"All Gemini models failed. Last error: {last_err}")
    else:
        model = OPENAI_MODEL or "gpt-4o-mini"
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
