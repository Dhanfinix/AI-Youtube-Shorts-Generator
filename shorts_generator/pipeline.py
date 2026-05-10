"""End-to-end orchestrator.

Two modes:
  * mode="api"   (default) — MuAPI does download / transcribe / LLM / autocrop.
                              Fast, no local deps, pay-per-call.
  * mode="local"            — yt-dlp + faster-whisper + OpenAI + ffmpeg/opencv.
                              Self-hosted, OPENAI_API_KEY required for the LLM.
"""
from typing import Dict, List, Optional

from .clipper import crop_highlights
from .downloader import download_youtube
from .highlights import call_muapi_llm, get_highlights
from .transcriber import transcribe


def _run_local(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
) -> Dict:
    import os
    import re
    import json
    import hashlib
    from .local.clipper import crop_highlights_local
    from .local.downloader import download_youtube_local
    from .local.llm import call_openai_llm
    from .local.transcriber import transcribe_local

    source_path = download_youtube_local(youtube_url, fmt=download_format)

    # Extract Video ID for persistent checkpoint/caching
    video_id = "unknown"
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", youtube_url)
    if match:
        video_id = match.group(1)
    else:
        video_id = hashlib.md5(youtube_url.encode()).hexdigest()

    os.makedirs("cache", exist_ok=True)
    transcript_cache_path = os.path.join("cache", f"{video_id}_transcript.json")
    highlights_cache_path = os.path.join("cache", f"{video_id}_highlights.json")

    # 1. Transcript Checkpoint
    if os.path.exists(transcript_cache_path):
        print(f"[cache] Loading transcript from checkpoint: {transcript_cache_path}", flush=True)
        with open(transcript_cache_path, "r") as f:
            transcript = json.load(f)
    else:
        transcript = transcribe_local(source_path, language=language, youtube_url=youtube_url)
        if transcript.get("segments"):
            with open(transcript_cache_path, "w") as f:
                json.dump(transcript, f, indent=2)

    if not transcript.get("segments"):
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    # 2. Highlights Checkpoint
    if os.path.exists(highlights_cache_path):
        print(f"[cache] Loading highlights from checkpoint: {highlights_cache_path}", flush=True)
        with open(highlights_cache_path, "r") as f:
            highlights_result = json.load(f)
    else:
        highlights_result = get_highlights(transcript, num_clips=num_clips, llm_fn=call_openai_llm)
        all_highlights = highlights_result.get("highlights", [])
        if all_highlights:
            with open(highlights_cache_path, "w") as f:
                json.dump(highlights_result, f, indent=2)

    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")

    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)
    if num_clips > 0:
        top = top[:num_clips]
    print(f"[pipeline/local] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    # Load Metadata Sidecar for visual attribution
    source_metadata = {}
    try:
        src_dir = os.path.dirname(source_path)
        src_stem = os.path.splitext(os.path.basename(source_path))[0]
        meta_path = os.path.join(src_dir, f"{src_stem}_metadata.json")
        if os.path.exists(meta_path):
            print(f"[pipeline/local] Attaching source attribution metadata for rendering...", flush=True)
            with open(meta_path, "r", encoding="utf-8") as mf:
                source_metadata = json.load(mf)
    except Exception as ex:
        print(f"[pipeline/local] Info: Metadata load failed ({ex})", flush=True)

    shorts = crop_highlights_local(
        source_path,
        top,
        aspect_ratio=aspect_ratio,
        transcript_segments=transcript["segments"],
        source_metadata=source_metadata,
    )

    return {
        "mode": "local",
        "source_video_url": source_path,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
    }


def _run_api(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
) -> Dict:
    source_url = download_youtube(youtube_url, fmt=download_format)

    transcript = transcribe(source_url, language=language)
    if not transcript["segments"]:
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    highlights_result = get_highlights(transcript, num_clips=num_clips, llm_fn=call_muapi_llm)
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")

    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)
    if num_clips > 0:
        top = top[:num_clips]
    print(f"[pipeline] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    shorts = crop_highlights(source_url, top, aspect_ratio=aspect_ratio)

    return {
        "mode": "api",
        "source_video_url": source_url,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
    }


def generate_shorts(
    youtube_url: str,
    num_clips: int = 3,
    aspect_ratio: str = "9:16",
    download_format: str = "720",
    language: Optional[str] = None,
    mode: str = "api",
) -> Dict:
    """Run the full pipeline and return a structured result.

    Args:
        youtube_url: source URL.
        num_clips: how many shorts to render.
        aspect_ratio: e.g. "9:16", "1:1".
        download_format: source resolution ("360" / "480" / "720" / "1080").
        language: ISO-639-1 to force Whisper language detection.
        mode: "api" (default, MuAPI) or "local" (yt-dlp + faster-whisper +
            OpenAI + ffmpeg).

    Returns:
        {
          "mode": "api" | "local",
          "source_video_url": str,   # hosted URL (api) or local path (local)
          "transcript": {...},
          "highlights": [...],       # all candidates ranked
          "shorts": [...],           # top `num_clips` with clip_url / local path
        }
    """
    mode = (mode or "api").lower()
    if mode == "local":
        return _run_local(youtube_url, num_clips, aspect_ratio, download_format, language)
    if mode == "api":
        return _run_api(youtube_url, num_clips, aspect_ratio, download_format, language)
    raise ValueError(f"Unknown mode: {mode!r}. Use 'api' or 'local'.")
