"""CLI entry point.

Usage:
    python main.py "https://www.youtube.com/watch?v=..." \
        --num-clips 3 --aspect-ratio 9:16
"""
import argparse
import json
import sys

from shorts_generator import generate_shorts
from shorts_generator.llm import get_trending_videos


def main() -> int:
    parser = argparse.ArgumentParser(description="AI YouTube Shorts Generator")
    parser.add_argument("url", nargs="?", help="YouTube video URL")
    parser.add_argument("--num-clips", type=int, default=3, help="How many shorts to render (default: 3, use 0 for all found)")
    parser.add_argument("--aspect-ratio", default="9:16", help="Output aspect ratio (default: 9:16)")
    parser.add_argument("--format", default="1080", help="Source download resolution: 360 / 480 / 720 / 1080 (default: 1080)")
    parser.add_argument("--language", default=None, help="Force Whisper language code, e.g. 'en' (default: auto-detect)")
    parser.add_argument("--output-json", default=None, help="Write the full result JSON to this path")
    parser.add_argument("--interactive", action="store_true", help="Pause and choose which candidates to render")
    parser.add_argument("--trends", action="store_true", help="Discover trending podcasts and choose one to process")
    args = parser.parse_args()

    target_url = args.url

    # Dynamic Selection flow via --trends
    if args.trends:
        try:
            videos = get_trending_videos()
            if not videos:
                print("\n[trends] No trending podcasts found or Gemini returned empty. Aborting.", flush=True)
                return 1
            
            print("\n" + "🚀" * 25)
            print("       DISCOVER TRENDING PODCASTS")
            print("🚀" * 25)
            for idx, v in enumerate(videos, 1):
                date_str = v.get('date', 'Unknown Date')
                print(f"[{idx:2}] 📅 {date_str} | 🎙️ {v.get('title')}")
            
            print("\n👉 Enter the number you want to process")
            print("👉 Or press Ctrl+C to exit")
            
            choice = input("\nChoice > ").strip()
            if not choice:
                print("[trends] No selection made. Exiting.", flush=True)
                return 0
            
            sel_idx = int(choice) - 1
            if 0 <= sel_idx < len(videos):
                vid_info = videos[sel_idx]
                print(f"\n[trends] Authenticating exact source URL for '{vid_info.get('title')}'...", flush=True)
                
                from shorts_generator.downloader import lookup_youtube_url_by_title
                verified_url = lookup_youtube_url_by_title(vid_info.get('title'))
                
                if verified_url:
                    target_url = verified_url
                    print(f"[trends] Locked authenticated URL: {target_url}", flush=True)
                else:
                    # Revert to Gemini supplied URL fallback
                    target_url = vid_info.get("url")
                    print(f"[trends] Using direct fallback URL: {target_url}", flush=True)
            else:
                print("[trends] Invalid choice number. Exiting.", flush=True)
                return 1
        except KeyboardInterrupt:
            print("\n[trends] Cancelled. Exiting.", flush=True)
            return 0
        except ValueError:
            print("[trends] Invalid input format. Exiting.", flush=True)
            return 1

    if not target_url:
        parser.error("A valid YouTube URL is required, or use --trends to pick one.")

    try:
        result = generate_shorts(
            youtube_url=target_url,
            num_clips=args.num_clips,
            aspect_ratio=args.aspect_ratio,
            download_format=args.format,
            language=args.language,
            interactive=args.interactive,
        )
    except KeyboardInterrupt:
        print("\n\n[pipeline] Aborted by user (Ctrl+C). Exiting.", file=sys.stderr, flush=True)
        return 0
    except Exception as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 72)
    print(f"Mode:          local")
    print(f"Source video:  {result['source_video_url']}")
    print(f"Highlights:    {len(result['highlights'])} candidates → kept top {len(result['shorts'])}")
    print("=" * 72)
    for i, s in enumerate(result["shorts"], 1):
        print(f"\n#{i}  score={s.get('score')}  {s.get('start_time'):.1f}s → {s.get('end_time'):.1f}s")
        print(f"     title:  {s.get('title')}")
        print(f"     hook:   {s.get('hook_sentence')}")
        if s.get("clip_url"):
            print(f"     clip:   {s['clip_url']}")
        else:
            print(f"     clip:   FAILED ({s.get('error')})")

    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nFull JSON written to {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
