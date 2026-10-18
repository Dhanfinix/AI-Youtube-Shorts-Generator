"""Helper script for Job 1 on device (Self-Hosted Runner).

Resolves the target YouTube video (from user URL input or trends)
and downloads it using local yt-dlp.
"""
import os
import sys
import glob
import json
import argparse
import subprocess

def check_video_available(url: str) -> bool:
    """Verifies if a YouTube video is currently public, active, and accessible."""
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
            for idx, vid in enumerate(videos, 1):
                title = vid.get("title", "")
                print(f"[resolve] Evaluating candidate #{idx}: '{title}'...", flush=True)
                cand_url = lookup_youtube_url_by_title(title) if title else None
                if not cand_url:
                    cand_url = vid.get("url")

                if cand_url and check_video_available(cand_url):
                    print(f"[resolve] ✅ Confirmed accessible video #{idx}: {cand_url}", flush=True)
                    return cand_url
                else:
                    print(f"[resolve] ⚠️ Candidate #{idx} is unavailable or private. Checking next...", flush=True)
    except Exception as e:
        print(f"[resolve] Trending lookup encountered error: {e}", flush=True)

    # Secondary fallback search
    print("[resolve] Running fallback search for trending podcast...", flush=True)
    for fallback_query in ["ytsearch1:podcast indonesia terbaru", "ytsearch1:ted talk latest"]:
        try:
            res = subprocess.run(
                ["yt-dlp", "--no-update", "--get-id", fallback_query],
                capture_output=True,
                text=True,
                timeout=20
            )
            vid_id = res.stdout.strip()
            if vid_id:
                cand_url = f"https://www.youtube.com/watch?v={vid_id}"
                if check_video_available(cand_url):
                    print(f"[resolve] ✅ Selected fallback video: {cand_url}", flush=True)
                    return cand_url
        except Exception:
            continue

    raise RuntimeError("Could not find any available YouTube video to process.")

def main():
    parser = argparse.ArgumentParser(description="Resolve and download YouTube video locally")
    parser.add_argument("--url", default="", help="Target YouTube URL (optional)")
    parser.add_argument("--format", default="720", help="Max video resolution height")
    args = parser.parse_args()

    target_url = resolve_target_url(args.url)
    print(f"[download] Target video locked: {target_url}", flush=True)

    out_dir = os.path.abspath("output/source")
    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, "source_video.%(ext)s")

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
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"[download] yt-dlp failed with exit code {res.returncode}", file=sys.stderr, flush=True)
        sys.exit(res.returncode)

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
    print(f"✅ Success! Saved {target_video} ({size_mb:.1f} MB)", flush=True)

if __name__ == "__main__":
    main()
