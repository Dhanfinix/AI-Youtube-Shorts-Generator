"""Local transcription gateway — prioritizes native YouTube captions and falls back to Gemini AI for raw media."""
import os
import re
import time
import json
import subprocess
from typing import Dict, Optional
from .config import require_local_llm_key, LOCAL_TRANSCRIBER_PRIORITY, LOCAL_TARGET_LANGUAGE, GEMINI_MODEL

def _extract_audio(video_path: str) -> str:
    """Extract mono mp3 at 16khz to dramatically shrink upload payload size (~15MB/hour)."""
    base, _ = os.path.splitext(video_path)
    audio_path = base + "_extract.mp3"
    if os.path.exists(audio_path):
        return audio_path
    
    print(f"[transcribe/audio] Extracting lean audio track via ffmpeg...", flush=True)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ar", "16000", "-ac", "1", 
        "-c:a", "libmp3lame", "-q:a", "8", 
        "-loglevel", "error",
        audio_path
    ]
    subprocess.run(cmd, check=True)
    return audio_path

def _synthesize_word_timings(segments: list):
    """Backfills pseudo-word-level timestamps interpolated evenly from segment intervals."""
    for seg in segments:
        text = (seg.get("text", "") or "").strip()
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + 1.0))
        duration = max(0.1, end - start)
        
        words = text.split()
        word_list = []
        if words:
            word_dur = duration / len(words)
            for idx, w in enumerate(words):
                word_list.append({
                    "word": w,
                    "start": start + idx * word_dur,
                    "end": start + (idx + 1) * word_dur
                })
        seg["words"] = word_list
    return segments

def try_gemini(media_path: str, target_lang: Optional[str] = None) -> Optional[Dict]:
    """Upload compressed audio to Gemini File API and perform strict JSON transcription."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("[transcribe/gemini] Error: google-genai SDK not installed.", flush=True)
        return None

    # 1. Condense to lightweight audio
    is_video = media_path.lower().endswith((".mp4", ".mkv", ".webm", ".mov"))
    file_to_upload = media_path
    is_temp_audio = False
    
    try:
        if is_video:
            file_to_upload = _extract_audio(media_path)
            is_temp_audio = True
            
        print(f"[transcribe/gemini] Initializing Gemini backend...", flush=True)
        client = genai.Client(api_key=require_local_llm_key())
        
        # 2. Upload File
        print(f"[transcribe/gemini] Uploading audio payload ({os.path.basename(file_to_upload)})...", flush=True)
        gfile = client.files.upload(file=file_to_upload)
        
        # 3. Poll until ready
        attempts = 0
        while gfile.state.name == "PROCESSING":
            if attempts > 30:
                raise TimeoutError("Gemini file processing timed out.")
            print(f"[transcribe/gemini] Waiting for server audio alignment...", flush=True)
            time.sleep(5)
            gfile = client.files.get(name=gfile.name)
            attempts += 1
            
        if gfile.state.name != "ACTIVE":
            raise RuntimeError(f"Gemini File processing failed with state: {gfile.state.name}")
            
        # 4. Formulate rigid schema and prompt
        prompt = (
            "Generate a precise, verbatim transcription of this entire audio clip. "
            "You MUST include floating-point timestamps (start and end times in seconds) for every natural phrase. "
            "Ensure complete coverage. Do not omit text."
        )
        if target_lang and target_lang != "auto":
            prompt += f" Output should be in {target_lang} language."

        # Define specific output structure so model natively follows instructions
        schema = {
            "type": "OBJECT",
            "properties": {
                "segments": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "start": {"type": "NUMBER"},
                            "end": {"type": "NUMBER"},
                            "text": {"type": "STRING"}
                        },
                        "required": ["start", "end", "text"]
                    }
                }
            },
            "required": ["segments"]
        }

        model_to_use = GEMINI_MODEL or "gemini-2.5-flash"
        # Force fast model fallback for utility operations if specialized user-provided model fails schema injection
        if "gemma" in model_to_use.lower():
            model_to_use = "gemini-2.5-flash"

        print(f"[transcribe/gemini] Triggering AI audio synthesis via {model_to_use}...", flush=True)
        response = client.models.generate_content(
            model=model_to_use,
            contents=[gfile, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.0 # Deterministic for transcription
            )
        )
        
        # 5. Cleanup cloud asset
        try:
            client.files.delete(name=gfile.name)
        except Exception:
            pass
            
        # 6. Parse and Map Output
        raw_json = response.text or "{}"
        payload = json.loads(raw_json)
        segments = payload.get("segments", [])
        
        if not segments:
            print("[transcribe/gemini] Received empty segment map from model.", flush=True)
            return None
            
        # Interpolate word level lists to maintain compatibility with face-tracker
        final_segments = _synthesize_word_timings(segments)
        duration = final_segments[-1]["end"]
        
        print(f"[transcribe/gemini] Success: {len(final_segments)} segments recovered.", flush=True)
        return {"duration": duration, "segments": final_segments}

    except Exception as e:
        print(f"[transcribe/gemini] Error during AI workflow: {e}", flush=True)
        return None
    finally:
        # Delete local extracted mp3 temporary asset
        if is_temp_audio and os.path.exists(file_to_upload):
            try:
                os.remove(file_to_upload)
            except OSError:
                pass

def try_youtube(media_path: str, youtube_url: Optional[str] = None, target_lang: Optional[str] = None) -> Optional[Dict]:
    """Fetch existing manual/auto captions direct from YouTube."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("[transcribe/youtube] YouTube API unavailable.", flush=True)
        return None

    video_id = None
    if youtube_url:
        m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", youtube_url)
        if m: video_id = m.group(1)
    if not video_id:
        m = re.search(r"source_([a-zA-Z0-9_-]{11})", os.path.basename(media_path))
        if m: video_id = m.group(1)

    if not video_id:
        print("[transcribe/youtube] No valid video_id detected.", flush=True)
        return None

    print(f"[transcribe/youtube] Scanning captions for {video_id}...", flush=True)
    try:
        t_list = YouTubeTranscriptApi().list(video_id)
        t_obj = None
        
        if target_lang and target_lang != "auto":
            try: t_obj = t_list.find_generated_transcript([target_lang])
            except:
                try: t_obj = t_list.find_manually_created_transcript([target_lang])
                except: pass
        
        if not t_obj:
            try: t_obj = t_list.find_generated_transcript()
            except:
                try: t_obj = t_list.find_manually_created_transcript()
                except: t_obj = list(t_list)[0]

        if target_lang and target_lang != "auto" and t_obj.language_code != target_lang:
            if t_obj.is_translatable:
                print(f"[transcribe/youtube] Translating '{t_obj.language_code}' → '{target_lang}'.", flush=True)
                t_obj = t_obj.translate(target_lang)

        rows = t_obj.fetch()
        
        segments = []
        for r in rows:
            if isinstance(r, dict):
                txt = (r.get("text", "") or "").strip()
                s = float(r.get("start", 0.0))
                d = float(r.get("duration", 0.0))
            else:
                txt = (getattr(r, "text", "") or "").strip()
                s = float(getattr(r, "start", 0.0))
                d = float(getattr(r, "duration", 0.0))

            if not txt: continue
            
            # Prevent collision overlaps
            if segments and s < segments[-1]["end"]:
                segments[-1]["end"] = s
            
            segments.append({"start": s, "end": s + d, "text": txt})
            
        if not segments: return None
        
        # Standardize duration and embed synth word tokens for face matching
        processed = _synthesize_word_timings(segments)
        dur = processed[-1]["end"]
        print(f"[transcribe/youtube] Native success ({len(processed)} segments).", flush=True)
        return {"duration": dur, "segments": processed}
        
    except Exception as e:
        c_err = str(e).split('\n')[0]
        print(f"[transcribe/youtube] Captions retrieval error: {c_err}", flush=True)
        return None

def transcribe_local(
    media_path: str,
    language: Optional[str] = None,
    youtube_url: Optional[str] = None,
) -> Dict:
    """Unified local dispatch prioritizing speed (YouTube) then flexibility (Gemini AI)."""
    
    target_lang = language or LOCAL_TARGET_LANGUAGE
    # Ensure auto is coerced correctly
    if target_lang == "auto": target_lang = None

    priority = LOCAL_TRANSCRIBER_PRIORITY
    
    # Strategy router
    if priority == "youtube":
        res = try_youtube(media_path, youtube_url, target_lang)
        if res: return res
        print("[transcribe/local] YouTube path exhausted. Falling back to Gemini AI processing...", flush=True)
        res = try_gemini(media_path, target_lang)
        if res: return res
    else:
        res = try_gemini(media_path, target_lang)
        if res: return res
        print("[transcribe/local] Gemini route exhausted. Checking YouTube archive data...", flush=True)
        res = try_youtube(media_path, youtube_url, target_lang)
        if res: return res
        
    raise RuntimeError("Failed to resolve any transcription routes. Both YouTube and Gemini fallbacks returned negative.")
