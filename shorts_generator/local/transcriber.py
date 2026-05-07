"""Local transcription via faster-whisper.

Reads a local media file and returns the same shape the highlight generator
expects: {duration, segments[start, end, text]}.
"""
from typing import Dict, Optional

from ..config import LOCAL_WHISPER_DEVICE, LOCAL_WHISPER_MODEL


def _resolve_device() -> str:
    if LOCAL_WHISPER_DEVICE != "auto":
        return LOCAL_WHISPER_DEVICE
    try:
        import torch  # type: ignore
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def transcribe_local(
    media_path: str,
    language: Optional[str] = None,
    youtube_url: Optional[str] = None,
) -> Dict:
    """Transcribe local media with accurate word timing, then fall back to captions."""
    import os
    import re

    # Prefer faster-whisper because YouTube transcript entries rarely include
    # real word timestamps; evenly distributed words look polished but drift
    # noticeably against speech in fast Indonesian/English clips.
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        print(f"[transcribe/local] faster-whisper unavailable: {e}. Falling back to YouTube transcript...", flush=True)
    else:
        device = _resolve_device()
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"[transcribe/local] faster-whisper model={LOCAL_WHISPER_MODEL} device={device}", flush=True)

        model = WhisperModel(LOCAL_WHISPER_MODEL, device=device, compute_type=compute_type)
        segments_iter, info = model.transcribe(
            media_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            word_timestamps=True,
        )

        segments = []
        for s in segments_iter:
            word_list = []
            if getattr(s, "words", None) is not None:
                for w in s.words:
                    word_start = float(w.start)
                    word_end = float(w.end)
                    if word_end <= word_start:
                        continue
                    word_list.append({
                        "word": w.word.strip(),
                        "start": word_start,
                        "end": word_end,
                    })
            segments.append({
                "start": float(s.start),
                "end": float(s.end),
                "text": (s.text or "").strip(),
                "words": word_list,
            })

        duration = float(getattr(info, "duration", 0.0)) or (segments[-1]["end"] if segments else 0.0)
        if segments:
            print(f"[transcribe/local] {len(segments)} segments, {duration:.0f}s of audio", flush=True)
            return {"duration": duration, "segments": segments}

    # Last resort: captions are useful for highlight selection, but word times
    # are approximate. The renderer receives them only if Whisper cannot run.
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

        video_id = None
        if youtube_url:
            v_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", youtube_url)
            if v_match:
                video_id = v_match.group(1)
        if not video_id:
            basename = os.path.basename(media_path)
            f_match = re.search(r"source_([a-zA-Z0-9_-]{11})", basename)
            if f_match:
                video_id = f_match.group(1)
        if not video_id:
            raise RuntimeError("could not resolve YouTube video id")

        print(f"[transcribe/youtube] Fetching fallback transcript for {video_id}...", flush=True)
        api_instance = YouTubeTranscriptApi()
        transcript_list = api_instance.list(video_id)
        try:
            raw_transcript = transcript_list.find_transcript([language or "id", "en"]).fetch()
        except Exception:
            try:
                raw_transcript = transcript_list.find_generated_transcript([language or "id", "en"]).fetch()
            except Exception:
                raw_transcript = list(transcript_list)[0].fetch()
    except Exception as api_err:
        raise RuntimeError(
            "Could not transcribe locally or fetch YouTube captions. Install local deps with:\n"
            "    pip install -r requirements-local.txt"
        ) from api_err

    segments = []
    for item in raw_transcript:
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(item, "start", 0.0))
        duration = float(getattr(item, "duration", 0.0))
        end = start + duration

        words = text.split()
        word_list = []
        if words and duration > 0:
            word_duration = duration / len(words)
            for idx, w in enumerate(words):
                word_list.append({
                    "word": w.strip(),
                    "start": start + idx * word_duration,
                    "end": start + (idx + 1) * word_duration,
                })
        segments.append({
            "start": start,
            "end": end,
            "text": text,
            "words": word_list,
        })

    duration = segments[-1]["end"] if segments else 0.0
    print(f"[transcribe/youtube] fallback {len(segments)} segments, {duration:.0f}s of captions", flush=True)
    return {"duration": duration, "segments": segments}
