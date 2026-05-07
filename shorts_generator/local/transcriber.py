"""Local transcription via faster-whisper.

Reads a local media file and returns the same shape the highlight generator
expects: {duration, segments[start, end, text]}.
"""
from typing import Dict, Optional

from ..config import LOCAL_WHISPER_DEVICE, LOCAL_WHISPER_MODEL, LOCAL_TRANSCRIBER_PRIORITY, LOCAL_TARGET_LANGUAGE


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
    """Transcribe local media following user configured priorities and translation targets."""
    import os
    import re
    
    # Resolve target language priorities
    target_lang = language or LOCAL_TARGET_LANGUAGE
    if target_lang == "auto":
        target_lang = None
        
    def try_whisper():
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            print(f"[transcribe/local] faster-whisper unavailable: {e}.", flush=True)
            return None
        
        device = _resolve_device()
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"[transcribe/local] faster-whisper model={LOCAL_WHISPER_MODEL} device={device}", flush=True)

        try:
            model = WhisperModel(LOCAL_WHISPER_MODEL, device=device, compute_type=compute_type)
            segments_iter, info = model.transcribe(
                media_path,
                language=target_lang,
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
                print(f"[transcribe/local] whisper success: {len(segments)} segments, {duration:.0f}s of audio", flush=True)
                return {"duration": duration, "segments": segments}
        except Exception as e:
            print(f"[transcribe/local] Whisper failed: {e}", flush=True)
        return None

    def try_youtube():
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
        except ImportError as e:
            print(f"[transcribe/youtube] youtube-transcript-api unavailable: {e}", flush=True)
            return None

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
            print("[transcribe/youtube] Could not resolve YouTube video id.", flush=True)
            return None

        print(f"[transcribe/youtube] Fetching transcript for {video_id}...", flush=True)
        try:
            transcript_list = YouTubeTranscriptApi().list(video_id)
            
            # Select the appropriate transcript
            transcript_obj = None
            if target_lang:
                try:
                    # Try manual transcript in target lang
                    transcript_obj = transcript_list.find_manually_created_transcript([target_lang])
                except Exception:
                    try:
                        # Try generated transcript in target lang
                        transcript_obj = transcript_list.find_generated_transcript([target_lang])
                    except Exception:
                        pass

            if not transcript_obj:
                # Fallback to any manual transcript, then any generated transcript
                try:
                    transcript_obj = transcript_list.find_manually_created_transcript()
                except Exception:
                    try:
                        transcript_obj = transcript_list.find_generated_transcript()
                    except Exception:
                        # Grab the first available transcript
                        transcript_obj = list(transcript_list)[0]

            if target_lang and transcript_obj.language_code != target_lang:
                if transcript_obj.is_translatable:
                    print(f"[transcribe/youtube] Translating transcript from '{transcript_obj.language_code}' to '{target_lang}'...", flush=True)
                    transcript_obj = transcript_obj.translate(target_lang)

            raw_transcript = transcript_obj.fetch()
            
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
            print(f"[transcribe/youtube] success: {len(segments)} segments, {duration:.0f}s of captions (lang={transcript_obj.language_code})", flush=True)
            return {"duration": duration, "segments": segments}
        except Exception as e:
            print(f"[transcribe/youtube] Failed to fetch YouTube captions: {e}", flush=True)
        return None

    # Run following user-configured priority
    if LOCAL_TRANSCRIBER_PRIORITY == "youtube":
        res = try_youtube()
        if res is not None:
            return res
        print("[transcribe/local] YouTube transcript failed/unavailable. Falling back to Whisper...", flush=True)
        res = try_whisper()
        if res is not None:
            return res
    else:
        res = try_whisper()
        if res is not None:
            return res
        print("[transcribe/local] Whisper failed/unavailable. Falling back to YouTube transcript...", flush=True)
        res = try_youtube()
        if res is not None:
            return res

    raise RuntimeError(
        "Both Whisper and YouTube transcript options failed or were unavailable. Please ensure you have internet access or your local packages are installed correctly."
    )
