"""Local YouTube download via yt-dlp.

Returns a local mp4 path so the rest of the local pipeline can read it
directly off disk.
"""
import os
from typing import Optional

from ..config import LOCAL_OUTPUT_DIR


def _import_ytdlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e
    return yt_dlp


def _format_for(fmt: str) -> str:
    """Map our '720' / '1080' shorthand to a yt-dlp format selector."""
    try:
        height = int(fmt)
    except ValueError:
        height = 720
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={height}][ext=mp4]/best"
    )


def download_youtube_local(video_url: str, fmt: str = "720", out_dir: Optional[str] = None) -> str:
    """Download to disk and return the local mp4 path."""
    yt_dlp = _import_ytdlp()
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    print(f"[download/local] {video_url} @ {fmt}p → {out_dir}/", flush=True)
    ydl_opts = {
        "format": _format_for(fmt),
        "outtmpl": os.path.join(out_dir, "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }

    # Automatically load cookies if present in environment or local files
    cookies_env = os.getenv("YOUTUBE_COOKIES")
    cookies_b64_env = os.getenv("YOUTUBE_COOKIES_BASE64")
    
    if cookies_env:
        cookie_path = os.path.join(out_dir, "youtube_cookies_temp.txt")
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(cookies_env)
        ydl_opts["cookiefile"] = cookie_path
        print("[download/local] Using cookies loaded from YOUTUBE_COOKIES environment variable.", flush=True)
    elif cookies_b64_env:
        import base64
        cookie_path = os.path.join(out_dir, "youtube_cookies_temp.txt")
        try:
            decoded = base64.b64decode(cookies_b64_env).decode("utf-8")
            with open(cookie_path, "w", encoding="utf-8") as f:
                f.write(decoded)
            ydl_opts["cookiefile"] = cookie_path
            print("[download/local] Using cookies decoded from YOUTUBE_COOKIES_BASE64 environment variable.", flush=True)
        except Exception as e:
            print(f"[download/local] Error decoding YOUTUBE_COOKIES_BASE64: {e}", flush=True)
    else:
        for name in ("cookies.txt", "youtube_cookies.txt"):
            possible_paths = [
                name,
                os.path.join(os.getcwd(), name),
                os.path.abspath(name),
            ]
            found_path = None
            for p in possible_paths:
                if os.path.exists(p):
                    found_path = p
                    break
            if found_path:
                ydl_opts["cookiefile"] = found_path
                print(f"[download/local] Using local cookies file: {found_path}", flush=True)
                break

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            path = ydl.prepare_filename(info)
            # merge_output_format may rename the extension after merge
            if not os.path.exists(path):
                stem, _ = os.path.splitext(path)
                for ext in (".mp4", ".mkv", ".webm"):
                    if os.path.exists(stem + ext):
                        path = stem + ext
                        break
    except Exception as ytdl_err:
        print(f"[download/local] yt-dlp failed to download ({ytdl_err}). Attempting fallback to pytubefix...", flush=True)
        try:
            from pytubefix import YouTube  # type: ignore
            
            video_id = "video"
            if "v=" in video_url:
                video_id = video_url.split("v=")[1].split("&")[0]
            elif "youtu.be/" in video_url:
                video_id = video_url.split("youtu.be/")[1].split("?")[0]
            
            yt = YouTube(video_url)
            # Find progressive mp4 stream or highest resolution stream
            stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
            if not stream:
                stream = yt.streams.filter(file_extension='mp4').order_by('resolution').desc().first()
            if not stream:
                stream = yt.streams.order_by('resolution').desc().first()
                
            if not stream:
                raise RuntimeError("No downloadable streams found via pytubefix.")
                
            out_filename = f"source_{video_id}.mp4"
            path = os.path.join(out_dir, out_filename)
            
            print(f"[download/local] Downloading stream: {stream.resolution} via pytubefix to {path}...", flush=True)
            stream.download(output_path=out_dir, filename=out_filename)
        except Exception as pytube_err:
            raise RuntimeError(
                f"Both downloaders failed.\n"
                f"  - yt-dlp error: {ytdl_err}\n"
                f"  - pytubefix error: {pytube_err}"
            ) from pytube_err

    print(f"[download/local] ready: {path}", flush=True)
    return path
