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

def main():
    parser = argparse.ArgumentParser(description="Resolve and download YouTube video locally")
    parser.add_argument("--url", default="", help="Target YouTube URL (optional)")
    parser.add_argument("--format", default="720", help="Max video resolution height")
    args = parser.parse_args()

    target_url = args.url.strip()

    if not target_url:
        print("[resolve] No explicit URL provided. Fetching trending podcast...", flush=True)
        try:
            from shorts_generator.llm import get_trending_videos
            from shorts_generator.downloader import lookup_youtube_url_by_title

            videos = get_trending_videos()
            if not videos:
                raise RuntimeError("No trending videos returned by Gemini.")

            top_video = videos[0]
            print(f"[resolve] Top trending pick: {top_video.get('title')}", flush=True)
            verified = lookup_youtube_url_by_title(top_video.get("title"))
            target_url = verified or top_video.get("url")
            print(f"[resolve] Locked authenticated URL: {target_url}", flush=True)
        except Exception as e:
            print(f"[resolve] Error finding trending video: {e}", file=sys.stderr, flush=True)
            sys.exit(1)

    if not target_url:
        print("[resolve] Error: Could not resolve a valid target URL.", file=sys.stderr, flush=True)
        sys.exit(1)

    out_dir = os.path.abspath("output/source")
    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, "source_video.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", f"bestvideo[height<={args.format}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-thumbnail",
        "-o", out_template,
        target_url,
    ]

    print(f"[download] Downloading via yt-dlp: {target_url}...", flush=True)
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
