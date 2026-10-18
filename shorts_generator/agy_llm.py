"""Local AI Highlight Generator using the on-device Antigravity CLI (`agy`).

Does not consume Gemini API quotas or external API keys.
"""
import os
import re
import json
import subprocess
from typing import Dict, List, Optional

def parse_json3_subtitles(json3_path: str) -> Dict:
    """Parse YouTube json3 subtitle format into segment timestamps and words."""
    with open(json3_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = []
    events = data.get("events", [])
    for ev in events:
        segs = ev.get("segs", [])
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue

        start = round(float(ev.get("tStartMs", 0)) / 1000.0, 2)
        dur = round(float(ev.get("dDurationMs", 0)) / 1000.0, 2)
        end = round(start + dur, 2)

        # Synthesize word timestamps for MoviePy/OpenCV compatibility
        words = text.split()
        word_list = []
        if words:
            step = max(0.05, (end - start) / len(words))
            for i, w in enumerate(words):
                w_start = round(start + i * step, 2)
                w_end = round(min(end, w_start + step), 2)
                word_list.append({"word": w, "start": w_start, "end": w_end})

        segments.append({
            "start": start,
            "end": end,
            "text": text,
            "words": word_list
        })

    duration = segments[-1]["end"] if segments else 0.0
    return {"duration": duration, "segments": segments}


def generate_highlights_with_agy(transcript_segments: List[Dict], num_clips: int = 3) -> Optional[Dict]:
    """Call local `agy` CLI to detect the most viral highlights."""
    if not transcript_segments:
        return None

    # Format transcript with timestamps
    lines = []
    for s in transcript_segments:
        lines.append(f"[{s['start']:.1f}s -> {s['end']:.1f}s] {s['text']}")
    formatted_transcript = "\n".join(lines)

    # Truncate if extremely long (limit to ~40k chars for fast turnaround)
    if len(formatted_transcript) > 40000:
        formatted_transcript = formatted_transcript[:40000]

    prompt = f"""You are an elite short-form video editor who knows what makes viewers stop scrolling on YouTube Shorts, TikTok, and Instagram Reels.

Analyze this video transcript and identify the top {num_clips} most viral-worthy highlight clips.

Rules:
- Duration sweet spot: 25 to 60 seconds per clip.
- Never cut mid-sentence or mid-thought; each clip must feel complete and punchy.
- Every highlight must start with a compelling hook sentence.
- Write "title", "hook_sentence", "virality_reason", and "social_caption" in the SAME language as the video transcript.
- Score 0 to 100 based on viral hook potential.

Respond ONLY with valid JSON in this exact structure:
{{
  "highlights": [
    {{
      "title": "Short Punchy Title",
      "start_time": 10.5,
      "end_time": 45.0,
      "score": 95,
      "hook_sentence": "The opening line",
      "virality_reason": "Why this clip will perform well",
      "social_caption": "Engaging description with relevant hashtags"
    }}
  ]
}}

Transcript:
{formatted_transcript}
"""

    print(f"[agy/ai] Analyzing transcript with local Antigravity CLI (agy)...", flush=True)
    try:
        res = subprocess.run(
            ["agy", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=300
        )
        if res.returncode != 0:
            print(f"[agy/ai] agy exited with code {res.returncode}: {res.stderr}", flush=True)
            return None

        raw = res.stdout.strip()
        # Clean markdown codeblocks if any
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        start_idx = raw.find("{")
        end_idx = raw.rfind("}")
        if start_idx != -1 and end_idx != -1:
            parsed = json.loads(raw[start_idx:end_idx + 1])
            highlights = parsed.get("highlights", [])
            if highlights:
                print(f"[agy/ai] ✅ Successfully discovered {len(highlights)} viral candidate(s) via agy!", flush=True)
                return parsed

        print(f"[agy/ai] Could not parse JSON from agy response: {raw[:200]}", flush=True)
        return None

    except Exception as e:
        print(f"[agy/ai] Error running agy: {e}", flush=True)
        return None
