"""Local YouTube download via yt-dlp.

Returns a local mp4 path so the rest of the local pipeline can read it
directly off disk.
"""
import os
# ARMAGEDDON FIX: Force exclude local requests from any environmental proxies
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + ",127.0.0.1,localhost"
from typing import Optional

from .config import LOCAL_OUTPUT_DIR


YOUTUBE_AUTH_COOKIE_NAMES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-1PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSID",
    "__Secure-3PAPISID",
    "__Secure-3PSIDTS",
}


def _import_ytdlp():
    try:
        import yt_dlp  # type: ignore
        
        # 🚀 CRITICAL FIX: Patch buggy yt-dlp-getpot plugin logger keyword overflow
        try:
            from yt_dlp.extractor.youtube.pot._director import YoutubeIEContentProviderLogger
            
            _orig_ytdlp_debug = YoutubeIEContentProviderLogger.debug
            def _patched_ytdlp_debug(self, message, *a, **k):
                return _orig_ytdlp_debug(self, message)
            YoutubeIEContentProviderLogger.debug = _patched_ytdlp_debug

            _orig_ytdlp_warning = YoutubeIEContentProviderLogger.warning
            def _patched_ytdlp_warning(self, message, *a, **k):
                return _orig_ytdlp_warning(self, message)
            YoutubeIEContentProviderLogger.warning = _patched_ytdlp_warning
        except Exception:
            pass 
            
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


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_cookie_text(raw: str) -> str:
    """Accept raw Netscape cookies or secrets pasted with literal "\\n" escapes."""
    text = raw.strip()
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    if "\t" not in text and "\\t" in text:
        text = text.replace("\\t", "\t")
    return text.rstrip() + "\n"


def _cookie_auth_summary(cookie_path: str) -> tuple[int, int]:
    youtube_rows = 0
    auth_rows = 0
    try:
        with open(cookie_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("# Netscape"):
                    continue
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_") :]
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain = parts[0].lower()
                name = parts[5]
                if "youtube.com" in domain or "google.com" in domain:
                    youtube_rows += 1
                    if name in YOUTUBE_AUTH_COOKIE_NAMES:
                        auth_rows += 1
    except OSError:
        pass
    return youtube_rows, auth_rows


def _write_cookie_secret(out_dir: str, value: str, source: str) -> Optional[str]:
    os.makedirs(out_dir, exist_ok=True)
    cookie_path = os.path.join(out_dir, "youtube_cookies_temp.txt")
    with open(cookie_path, "w", encoding="utf-8") as f:
        f.write(_normalize_cookie_text(value))

    youtube_rows, auth_rows = _cookie_auth_summary(cookie_path)
    if youtube_rows == 0:
        print(
            f"[download/local] {source} was written, but it does not look like a Netscape YouTube cookies file.",
            flush=True,
        )
    elif auth_rows == 0:
        print(
            f"[download/local] {source} has {youtube_rows} YouTube/Google cookie rows but no obvious login cookies.",
            flush=True,
        )
    else:
        print(
            f"[download/local] {source} loaded ({youtube_rows} YouTube/Google rows, {auth_rows} auth rows).",
            flush=True,
        )
    return cookie_path


def _load_cookiefile(out_dir: str) -> Optional[str]:
    cookies_env = os.getenv("YOUTUBE_COOKIES")
    cookies_file_env = os.getenv("YOUTUBE_COOKIES_FILE")

    if cookies_env:
        return _write_cookie_secret(out_dir, cookies_env, "YOUTUBE_COOKIES")

    if cookies_file_env:
        if os.path.exists(cookies_file_env):
            print(f"[download/local] Using cookies file from YOUTUBE_COOKIES_FILE: {cookies_file_env}", flush=True)
            return cookies_file_env
        print(f"[download/local] YOUTUBE_COOKIES_FILE does not exist: {cookies_file_env}", flush=True)

    for name in ("cookies.txt", "youtube_cookies.txt"):
        possible_paths = [
            name,
            os.path.join(os.getcwd(), name),
            os.path.abspath(name),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                print(f"[download/local] Using local cookies file: {p}", flush=True)
                return p
    return None


def validate_youtube_cookies(out_dir: Optional[str] = None) -> tuple[bool, str]:
    """Validate cookies file for physical expiration and structure.
    
    Returns:
        (is_valid, status_message)
    """
    import time
    from datetime import datetime

    out_dir = out_dir or LOCAL_OUTPUT_DIR
    cookie_path = _load_cookiefile(out_dir)
    
    if not cookie_path:
        return False, "No YouTube cookies configured. (Missing cookies.txt, YOUTUBE_COOKIES, or YOUTUBE_COOKIES_FILE env)"
        
    auth_found = 0
    expired_list = []
    now = time.time()
    
    try:
        with open(cookie_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Remove potential prefix #HttpOnly_
                if line.startswith("#HttpOnly_"):
                     line = line[len("#HttpOnly_"):]
                     
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                
                name = parts[5]
                try:
                    expiration = float(parts[4])
                except ValueError:
                    expiration = 0
                
                if name in YOUTUBE_AUTH_COOKIE_NAMES:
                    auth_found += 1
                    # Non-zero timestamps indicate real expiry dates (0 often signifies session cookies)
                    if expiration > 0 and expiration < now:
                        readable_exp = datetime.fromtimestamp(expiration).strftime("%Y-%m-%d %H:%M:%S")
                        expired_list.append(f"{name} (expired {readable_exp})")

    except Exception as e:
        return False, f"Error reading cookies file: {str(e)}"

    if auth_found == 0:
        return False, f"Cookies loaded from {cookie_path}, but no valid YouTube auth cookies (SID, HSID, etc.) found in it."

    if expired_list:
        return False, f"Expired cookies detected: {', '.join(expired_list)}. Please refresh your YouTube cookies."

    return True, f"✅ YouTube cookies are VALID ({auth_found} auth components detected, none expired)."


def _save_source_metadata(out_dir: str, info: dict, video_id: str) -> str:
    """Save relevant fields (title, uploader, thumbnail) to a sidecar JSON file for attribution."""
    import json
    import glob
    
    metadata = {
        "video_id": video_id or info.get("id"),
        "title": info.get("title", "Unknown Video"),
        "channel": info.get("uploader") or info.get("channel") or "Unknown Creator",
        "duration": info.get("duration"),
        "thumbnail_path": None
    }
    
    # Search for the written thumbnail file in the directory
    target_prefix = os.path.join(out_dir, f"source_{metadata['video_id']}.")
    matches = glob.glob(f"{target_prefix}*")
    for match in matches:
        ext = os.path.splitext(match)[1].lower()
        if ext in [".webp", ".jpg", ".jpeg", ".png"]:
            metadata["thumbnail_path"] = os.path.abspath(match)
            break
            
    meta_path = os.path.join(out_dir, f"source_{metadata['video_id']}_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    return meta_path


def _resolve_output_path(ydl, info) -> str:
    """Resolve the final output file path after yt-dlp download + merge."""
    path = ydl.prepare_filename(info)
    if os.path.exists(path):
        return path
    stem, _ = os.path.splitext(path)
    for ext in (".mp4", ".mkv", ".webm"):
        if os.path.exists(stem + ext):
            return stem + ext
    return path


def download_youtube_local(video_url: str, fmt: str = "720", out_dir: Optional[str] = None) -> str:
    """Download to disk and return the local mp4 path using standard, stable extraction."""
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    # 1. Extract Video ID for caching checks
    video_id = None
    if "v=" in video_url:
        video_id = video_url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in video_url:
        video_id = video_url.split("youtu.be/")[1].split("?")[0]
    elif "shorts/" in video_url:
        video_id = video_url.split("shorts/")[1].split("?")[0]
        
    # 2. Performance optimization: Re-use existing high-quality file on disk if found
    if video_id:
        import glob
        found_files = []
        found_files.extend(glob.glob(os.path.join(out_dir, "**", "source", f"source_{video_id}.mp4"), recursive=True))
        found_files.extend(glob.glob(os.path.join(out_dir, f"source_{video_id}.mp4")))
        
        path = found_files[0] if found_files else None
        if path and os.path.exists(path) and os.path.getsize(path) > 10000000:
            source_dir = os.path.dirname(path)
            print(f"[download/local] Found existing video cache: {path}. Reusing assets.", flush=True)
            
            # Lazy-hydrate metadata if missing in cache folder
            meta_path = os.path.join(source_dir, f"source_{video_id}_metadata.json")
            if not os.path.exists(meta_path):
                try:
                    yt_dlp = _import_ytdlp()
                    meta_opts = {"no_warnings": True, "skip_download": True, "writethumbnail": True, "outtmpl": os.path.join(source_dir, "source_%(id)s.%(ext)s")}
                    with yt_dlp.YoutubeDL(meta_opts) as ydl:
                        info = ydl.extract_info(video_url, download=True)
                        _save_source_metadata(source_dir, info, video_id)
                except Exception:
                    pass
            return path

    # 3. Detect direct download URLs (G-Drive/MP4)
    if "drive.google.com" in video_url or video_url.split("?")[0].endswith(".mp4"):
        file_id = "direct_video"
        download_url = video_url
        if "drive.google.com" in video_url:
            if "/file/d/" in video_url:
                file_id = video_url.split("/file/d/")[1].split("/")[0]
            elif "id=" in video_url:
                file_id = video_url.split("id=")[1].split("&")[0]
            download_url = f"https://docs.google.com/uc?export=download&id={file_id}"
        
        path = os.path.join(out_dir, f"source_{file_id}.mp4")
        import requests
        print(f"[download/local] Pulling direct file → {path}...", flush=True)
        with requests.get(download_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        print(f"[download/local] Success: {path}", flush=True)
        return path

    # 4. Execute Clean yt-dlp download routine (Vanilla Extraction)
    yt_dlp = _import_ytdlp()
    print(f"[download/local] {video_url} @ {fmt}p → {out_dir}/", flush=True)

    cookie_path = _load_cookiefile(out_dir)
    proxy = os.getenv("YOUTUBE_PROXY") or os.getenv("DOWNLOAD_PROXY")

    base_opts = {
        "format": _format_for(fmt),
        "outtmpl": os.path.join(out_dir, "%(title)s", "source", "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "writethumbnail": True,
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "sleep_interval_requests": 3,
    }

    # Enable browser-like TLS impersonation fingerprints
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        base_opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        print("[download/local] Impersonation Active: TLS target 'chrome'.", flush=True)
    except ImportError:
        base_opts["http_headers"] = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"}

    if proxy:
        print(f"[download/local] Using proxy: {proxy}", flush=True)
        base_opts["proxy"] = proxy
    if cookie_path:
        base_opts["cookiefile"] = cookie_path

    # The standard vanilla execution requested by user
    try:
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            print(f"[download/local] Executing standard extraction...", flush=True)
            info = ydl.extract_info(video_url, download=True)
            path = _resolve_output_path(ydl, info)
            _save_source_metadata(os.path.dirname(path), info, info.get("id", ""))
            
        print(f"[download/local] Download successful! Written to {path}", flush=True)
        return path
        
    except Exception as e:
        clean_msg = str(e).split("\n")[0]
        raise RuntimeError(f"Download failed: {clean_msg}. Please verify cookies/proxy settings.")
