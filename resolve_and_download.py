"""Helper script for Job 1 on device (Self-Hosted Runner).

Resolves the target YouTube video (from user URL input or trends),
downloads it using local yt-dlp, and uses the on-device Antigravity CLI (`agy`)
to analyze the transcript and generate viral draft highlights without API keys.
"""
import os
import sys
import glob
import json
import argparse
import subprocess

from shorts_generator.agy_llm import parse_json3_subtitles, generate_highlights_with_agy

def check_video_available(url: str) -> bool:
    """Verifies if a YouTube video is currently public, active, and accessible."""
    if not url or "dQw4w9WgXcQ" in url or "example.com" in url:
        return False
    try:
        res = subprocess.run(
            ["yt-dlp", "--no-update", "--simulate", "--quiet", "--no-warnings", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
        )
        return res.returncode == 0
    except Exception:
        return False

def check_video_has_subtitles(url: str) -> bool:
    """Checks if a YouTube video has native or automatic subtitles available."""
    try:
        res = subprocess.run(
            ["yt-dlp", "--no-update", "--js-runtimes", "node", "--list-subs", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return "Available subtitles" in res.stdout or "Available automatic captions" in res.stdout
    except Exception:
        return False

def resolve_target_url(input_url: str) -> str:
    """Resolves a valid and accessible YouTube video URL."""
    if input_url.strip():
        url = input_url.strip()
        print(f"[resolve] Verifying input URL: {url}...", flush=True)
        if check_video_available(url):
            return url
        print(f"[resolve] ⚠️ Specified URL is unavailable. Falling back to trending...", flush=True)

    print("[resolve] Fetching trending podcast candidates via Gemini...", flush=True)
    try:
        from shorts_generator.llm import get_trending_videos
        from shorts_generator.downloader import lookup_youtube_url_by_title

        videos = get_trending_videos()
        if videos:
            # First pass: look for a candidate that is accessible AND has subtitles
            for idx, vid in enumerate(videos, 1):
                title = vid.get("title", "")
                print(f"[resolve] Evaluating candidate #{idx}: '{title}'...", flush=True)
                cand_url = lookup_youtube_url_by_title(title) if title else None
                raw_url = vid.get("url") or ""
                if not cand_url and raw_url and check_video_available(raw_url):
                    cand_url = raw_url

                if cand_url and check_video_available(cand_url):
                    if check_video_has_subtitles(cand_url):
                        print(f"[resolve] ✅ Confirmed accessible video with subtitles #{idx}: {cand_url}", flush=True)
                        return cand_url
                    else:
                        print(f"[resolve] ℹ️ Candidate #{idx} has no subtitles yet. Trying next candidate...", flush=True)
    except Exception as e:
        print(f"[resolve] Trending lookup encountered error: {e}", flush=True)

    # Bulletproof fallback search directly querying YouTube trending Indonesian podcasts
    print("[resolve] Running direct YouTube search for active trending podcasts with subtitles...", flush=True)
    trending_queries = [
        "ytsearch5:podcast close the door terbaru",
        "ytsearch5:podcast indonesia terbaru",
        "ytsearch5:curhat bang denny sumargo terbaru",
        "ytsearch5:podcast agak laen terbaru",
    ]
    for fallback_query in trending_queries:
        try:
            res = subprocess.run(
                ["yt-dlp", "--no-update", "--js-runtimes", "node", "--get-id", fallback_query],
                capture_output=True,
                text=True,
                timeout=25
            )
            vid_ids = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            for vid_id in vid_ids:
                if "dQw4w9WgXcQ" in vid_id:
                    continue
                cand_url = f"https://www.youtube.com/watch?v={vid_id}"
                if check_video_available(cand_url):
                    if check_video_has_subtitles(cand_url):
                        print(f"[resolve] ✅ Selected trending podcast with subtitles: {cand_url}", flush=True)
                        return cand_url
        except Exception:
            continue

    # If none had subtitles, pick any available candidate from the first query
    try:
        res = subprocess.run(
            ["yt-dlp", "--no-update", "--js-runtimes", "node", "--get-id", "ytsearch3:podcast indonesia terbaru"],
            capture_output=True,
            text=True,
            timeout=20
        )
        for vid_id in res.stdout.splitlines():
            vid_id = vid_id.strip()
            if vid_id and "dQw4w9WgXcQ" not in vid_id:
                cand_url = f"https://www.youtube.com/watch?v={vid_id}"
                if check_video_available(cand_url):
                    print(f"[resolve] ✅ Selected fallback video: {cand_url}", flush=True)
                    return cand_url
    except Exception:
        pass

    raise RuntimeError("Could not find any available YouTube video to process.")

def main():
    parser = argparse.ArgumentParser(description="Resolve, download, and detect viral highlights locally")
    parser.add_argument("--url", default="", help="Target YouTube URL (optional)")
    parser.add_argument("--format", default="720", help="Max video resolution height")
    parser.add_argument("--num-clips", type=int, default=3, help="Number of viral highlights to detect")
    args = parser.parse_args()

    target_url = resolve_target_url(args.url)
    print(f"[download] Target video locked: {target_url}", flush=True)

    out_dir = os.path.abspath("output/source")
    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, "source_video.%(ext)s")

    # Step 1: Download video, audio, info json, thumbnail without subtitles to ensure download never aborts
    cmd = [
        "yt-dlp",
        "--no-update",
        "--js-runtimes", "node",
        "-f", f"bestvideo[height<={args.format}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-thumbnail",
        "-o", out_template,
        target_url,
    ]

    print(f"[download] Starting download via yt-dlp: {target_url}...", flush=True)
    subprocess.run(cmd)

    target_video = os.path.join(out_dir, "source_video.mp4")
    if not os.path.exists(target_video):
        matches = glob.glob(os.path.join(out_dir, "source_video.*"))
        for m in matches:
            if m.endswith((".mp4", ".mkv", ".webm")):
                os.rename(m, target_video)
                break

    if not os.path.exists(target_video):
        print(f"[download] Error: Target video file was not created.", file=sys.stderr, flush=True)
        sys.exit(1)

    # Save metadata
    info_matches = glob.glob(os.path.join(out_dir, "source_video*.info.json"))
    info_data = {"url": target_url}
    if info_matches:
        try:
            with open(info_matches[0], "r", encoding="utf-8") as f:
                raw = json.load(f)
                info_data.update({
                    "video_id": raw.get("id"),
                    "title": raw.get("title"),
                    "channel": raw.get("uploader") or raw.get("channel"),
                    "duration": raw.get("duration"),
                })
        except Exception:
            pass

    meta_file = os.path.join(out_dir, "source_video_metadata.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(info_data, f, indent=2)

    size_mb = os.path.getsize(target_video) / (1024 * 1024)
    print(f"✅ Video Downloaded: {target_video} ({size_mb:.1f} MB)", flush=True)

    # Step 2: Download Subtitles Independently (non-fatal if unavailable)
    print(f"[download] Fetching Indonesian subtitles for: {target_url}...", flush=True)
    sub_cmd = [
        "yt-dlp",
        "--no-update",
        "--js-runtimes", "node",
        "--skip-download",
        "--write-auto-sub",
        "--sub-lang", "id,id-orig",
        "--sub-format", "json3",
        "-o", out_template,
        target_url,
    ]
    subprocess.run(sub_cmd)

    # Process Subtitles & AI Viral Highlight Drafts via `agy`
    sub_matches = glob.glob(os.path.join(out_dir, "*.json3"))
    if not sub_matches:
        print("[download] No Indonesian subtitles found. Checking for English subtitles...", flush=True)
        sub_en_cmd = [
            "yt-dlp",
            "--no-update",
            "--js-runtimes", "node",
            "--skip-download",
            "--write-auto-sub",
            "--sub-lang", "en,en-orig",
            "--sub-format", "json3",
            "-o", out_template,
            target_url,
        ]
        subprocess.run(sub_en_cmd)
        sub_matches = glob.glob(os.path.join(out_dir, "*.json3"))

    if sub_matches:
        json3_file = sub_matches[0]
        # Prioritize Indonesian subtitles if available
        for sm in sub_matches:
            if ".id." in sm or ".id-orig." in sm:
                json3_file = sm
                break

        print(f"[agy/ai] Parsing subtitle track: {os.path.basename(json3_file)}...", flush=True)
        try:
            transcript = parse_json3_subtitles(json3_file)
            transcript_file = os.path.join(out_dir, "transcript.json")
            with open(transcript_file, "w", encoding="utf-8") as tf:
                json.dump(transcript, tf, indent=2)
            print(f"[agy/ai] Saved transcript with {len(transcript['segments'])} segments.", flush=True)

            # Generate viral draft with local agy
            highlights_result = generate_highlights_with_agy(transcript["segments"], num_clips=args.num_clips)
            if highlights_result and highlights_result.get("highlights"):
                highlights_file = os.path.join(out_dir, "highlights.json")
                with open(highlights_file, "w", encoding="utf-8") as hf:
                    json.dump(highlights_result, hf, indent=2)

                print("\n" + "🔥" * 25, flush=True)
                print("   VIRAL HIGHLIGHT DRAFT GENERATED VIA AGY", flush=True)
                print("🔥" * 25, flush=True)
                for idx, h in enumerate(highlights_result["highlights"], 1):
                    start = h.get("start_time", 0)
                    end = h.get("end_time", 0)
                    score = h.get("score", 0)
                    title = h.get("title", "Untitled")
                    hook = h.get("hook_sentence", "")
                    print(f"[{idx}] ⏱ {start:.1f}s -> {end:.1f}s | ⭐ {score} | {title}", flush=True)
                    print(f"    🪝 Hook: {hook}", flush=True)
                print("-" * 50 + "\n", flush=True)
        except Exception as e:
            print(f"[agy/ai] Subtitle / agy processing warning: {e}", flush=True)
    else:
        print("[agy/ai] No subtitles available; cloud runner will perform fallback transcription.", flush=True)

if __name__ == "__main__":
    main()
