"""Local YouTube download via yt-dlp.

Returns a local mp4 path so the rest of the local pipeline can read it
directly off disk.
"""
import os
from typing import Optional

from ..config import LOCAL_OUTPUT_DIR


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


def _proxy_config() -> tuple[Optional[str], Optional[dict]]:
    explicit_proxy = os.getenv("YOUTUBE_PROXY") or os.getenv("DOWNLOAD_PROXY")
    if explicit_proxy:
        print(f"[download/local] Using explicit proxy from environment: {explicit_proxy}", flush=True)
        return explicit_proxy, {"http": explicit_proxy, "https": explicit_proxy}

    import socket

    tor_available = False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 9050))
        tor_available = True
    except Exception:
        pass
    finally:
        s.close()

    if tor_available and _env_truthy("ENABLE_TOR_PROXY"):
        proxy = "socks5://127.0.0.1:9050"
        print("[download/local] Tor proxy enabled via ENABLE_TOR_PROXY=true.", flush=True)
        return proxy, {"http": proxy, "https": proxy}
    if tor_available:
        print("[download/local] Tor is available on 127.0.0.1:9050, but it is disabled. Set ENABLE_TOR_PROXY=true to use it.", flush=True)
    return None, None


def _youtube_extractor_args(player_clients_override: Optional[list] = None) -> dict:
    if player_clients_override:
        player_clients = player_clients_override
    else:
        player_clients = [
            item.strip()
            for item in (os.getenv("YT_DLP_PLAYER_CLIENTS") or "web_creator,default").split(",")
            if item.strip()
        ]
    args = {"player_client": player_clients}

    po_token = os.getenv("YOUTUBE_PO_TOKEN") or os.getenv("YT_DLP_PO_TOKEN")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA") or os.getenv("YT_DLP_VISITOR_DATA")
    if po_token:
        args["po_token"] = [po_token]
    if visitor_data:
        args["visitor_data"] = [visitor_data]
    return {"youtube": args}


# ── Retry strategies: each entry is a list of player_client values to try ──
_FALLBACK_STRATEGIES = [
    # Strategy 0: user-configured (env) or default web_creator,default
    None,
    # Strategy 1: web_creator alone (works best with cookies on datacenter IPs)
    ["web_creator"],
    # Strategy 2: default client (mweb / tv)
    ["default"],
    # Strategy 3: ios client (reliable mobile API, doesn't trigger browser UI)
    ["ios"],
    # Strategy 4: android_vr (highly stable legacy API)
    ["android_vr"],
    # Strategy 5: web + web_creator (LAST RESORT, can trigger slow browser engines)
    ["web", "web_creator"],
]


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
    """Download to disk and return the local mp4 path."""
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    # Check if a file for this video already exists on disk to preserve high resolution and save bandwidth
    video_id = None
    if "v=" in video_url:
        video_id = video_url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in video_url:
        video_id = video_url.split("youtu.be/")[1].split("?")[0]
    elif "shorts/" in video_url:
        video_id = video_url.split("shorts/")[1].split("?")[0]
        
    if video_id:
        out_filename = f"source_{video_id}.mp4"
        path = os.path.join(out_dir, out_filename)
        if os.path.exists(path) and os.path.getsize(path) > 10000000:  # larger than 10MB
            print(f"[download/local] High-quality source video already exists on disk: {path}. Reusing existing file!", flush=True)
            return path

    ytdlp_proxy, req_proxies = _proxy_config()

    # 1. Check if the URL is a Google Drive link or direct MP4 link
    if "drive.google.com" in video_url or video_url.split("?")[0].endswith(".mp4"):
        file_id = "direct_video"
        download_url = video_url
        if "drive.google.com" in video_url:
            if "/file/d/" in video_url:
                file_id = video_url.split("/file/d/")[1].split("/")[0]
            elif "id=" in video_url:
                file_id = video_url.split("id=")[1].split("&")[0]
            download_url = f"https://docs.google.com/uc?export=download&id={file_id}"
        
        out_filename = f"source_{file_id}.mp4"
        path = os.path.join(out_dir, out_filename)
        
        import requests
        print(f"[download/local] Direct file/Drive link detected, downloading to {path}...", flush=True)
        with requests.get(download_url, proxies=req_proxies, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"[download/local] ready: {path}", flush=True)
        return path

    yt_dlp = _import_ytdlp()
    print(f"[download/local] {video_url} @ {fmt}p → {out_dir}/", flush=True)

    cookie_path = _load_cookiefile(out_dir)

    # ── Try each player-client strategy in order ──
    last_err: Optional[Exception] = None

    base_opts = {
        "format": _format_for(fmt),
        "outtmpl": os.path.join(out_dir, "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "sleep_interval_requests": 1,
    }
    if ytdlp_proxy:
        base_opts["proxy"] = ytdlp_proxy
    if cookie_path:
        base_opts["cookiefile"] = cookie_path

    username = os.getenv("YOUTUBE_USERNAME")
    password = os.getenv("YOUTUBE_PASSWORD")
    if username:
        base_opts["username"] = username
    if password:
        base_opts["password"] = password

    # Phase 1: Standard strategies
    for idx, strategy in enumerate(_FALLBACK_STRATEGIES):
        extractor_args = _youtube_extractor_args(player_clients_override=strategy)
        clients_label = strategy or (os.getenv("YT_DLP_PLAYER_CLIENTS") or "web_creator,default")
        print(f"[download/local] Strategy {idx}: player_client={clients_label}", flush=True)

        ydl_opts = base_opts.copy()
        ydl_opts["extractor_args"] = extractor_args

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                path = _resolve_output_path(ydl, info)
            print(f"[download/local] ready: {path}", flush=True)
            return path
        except Exception as err:
            last_err = err
            print(
                f"[download/local] Strategy {idx} failed ({type(err).__name__}): {err}",
                flush=True,
            )

    # Phase 2: Elite fallback using local browser cookies automatically
    print("[download/local] Standard strategies failed. Attempting fallback via local browser cookies...", flush=True)
    browser_fallback = ["chrome", "safari", "firefox", "edge", "opera"]
    
    for browser in browser_fallback:
        print(f"[download/local] Trying extraction from '{browser}' browser...", flush=True)
        ydl_opts_browser = base_opts.copy()
        # Remove explicitly passed cookiefile if we're trying automated browser extraction
        ydl_opts_browser.pop("cookiefile", None)
        ydl_opts_browser["cookiesfrombrowser"] = (browser,)
        ydl_opts_browser["extractor_args"] = _youtube_extractor_args(player_clients_override=["default"])

        try:
            with yt_dlp.YoutubeDL(ydl_opts_browser) as ydl:
                info = ydl.extract_info(video_url, download=True)
                path = _resolve_output_path(ydl, info)
            print(f"[download/local] Success via '{browser}' cookies! ready: {path}", flush=True)
            return path
        except Exception as berr:
            last_err = berr
            clean_err = str(berr).split("\n")[0]
            print(f"[download/local] '{browser}' extraction skipped/failed: {clean_err}", flush=True)

    raise RuntimeError(f"YouTube download failed after all attempts (including browser cookie extraction). Details: {last_err}")
