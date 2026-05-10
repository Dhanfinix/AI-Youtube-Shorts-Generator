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
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
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

    if tor_available and _env_truthy("ENABLE_TOR_ROUTING"):
        proxy = "socks5://127.0.0.1:9050"
        print("[download/local] Tor proxy enabled via ENABLE_TOR_ROUTING=true.", flush=True)
        return proxy, {"http": proxy, "https": proxy}
    if tor_available:
        print("[download/local] Tor is available on 127.0.0.1:9050, but it is disabled. Set ENABLE_TOR_ROUTING=true to use it.", flush=True)
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
    args = {
        "player_client": player_clients,
        "youtubepot-providers": ["bgutil:http"]
    }

    po_token = os.getenv("YOUTUBE_PO_TOKEN") or os.getenv("YT_DLP_PO_TOKEN")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA") or os.getenv("YT_DLP_VISITOR_DATA")
    if po_token:
        args["po_token"] = [po_token]
    if visitor_data:
        args["visitor_data"] = [visitor_data]
    return {"youtube": args}


# ── Retry strategies: each entry is a list of player_client values to try ──
# ── Fallback strategies ordered by maximum probability of success on datacenter IPs ──
_FALLBACK_STRATEGIES = [
    # Strategy 0: TV client combined with web_creator (High priority bypass)
    ["tv", "web_creator"],
    # Strategy 1: TV client alone (DOES NOT require login, natively supports our PO Token generator!)
    ["tv"],
    # Strategy 2: TV Embedded (Alternative no-login endpoint)
    ["tv_embedded"],
    # Strategy 3: Web Embedded
    ["web_embedded"],
    # Strategy 4: web_creator alone (The one that already breached the firewall!)
    ["web_creator"],
    # Strategy 5: iOS (Mobile stable API)
    ["ios"],
    # Strategy 6: Android VR
    ["android_vr"],
    # Strategy 7: Default fallthrough
    ["default"],
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
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "sleep_interval_requests": 1,
        # CRITICAL 2026 UPGRADE: Impersonate Chrome at the TLS layer to bypass advanced fingerprinting
        "impersonate": "chrome",
        # Static high-stability User-Agent aligned with modern desktop distributions
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        },
    }
    if ytdlp_proxy:
        base_opts["proxy"] = ytdlp_proxy
    if cookie_path:
        base_opts["cookiefile"] = cookie_path

    all_errors = []
    
    # Phase 1: Standard strategies
    for idx, strategy in enumerate(_FALLBACK_STRATEGIES):
        clients_label = strategy or (os.getenv("YT_DLP_PLAYER_CLIENTS") or "web_creator,default")
        
        # TRY EACH STRATEGY FOUR TIMES TO COVER ALL COMBINATORIAL PERMUTATIONS:
        # 0: Standard + Cookies
        # 1: Standard + Anonymous
        # 2: Anonymous + SKIP WEBPAGE
        # 3: WITH Cookies + SKIP WEBPAGE (The Final Synthesis!)
        for auth_idx in [0, 1, 2, 3]:
            if auth_idx == 0:
                auth_label = "WITH Cookies"
                use_cookies = True
                skip_webpage = False
            elif auth_idx == 1:
                auth_label = "ANONYMOUS (No Cookies)"
                use_cookies = False
                skip_webpage = False
            elif auth_idx == 2:
                auth_label = "ANONYMOUS + SKIP WEBPAGE (Direct API Bypass)"
                use_cookies = False
                skip_webpage = True
            else:
                auth_label = "WITH Cookies + SKIP WEBPAGE (FINAL SYNTHESIS)"
                use_cookies = True
                skip_webpage = True

            print(f"[download/local] Strategy {idx}.{auth_idx}: player_client={clients_label} [{auth_label}]", flush=True)

            extractor_args = _youtube_extractor_args(player_clients_override=strategy)
            if skip_webpage:
                # Force nuclear skip flag to bypass the front door
                if "youtube" not in extractor_args:
                    extractor_args["youtube"] = {}
                extractor_args["youtube"]["player_skip"] = ["webpage"]

            ydl_opts = base_opts.copy()
            ydl_opts["extractor_args"] = extractor_args
            
            if not use_cookies:
                ydl_opts.pop("cookiefile", None)
                # Ensure we enforce PO token logic explicitly for total anonymous authority

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    path = _resolve_output_path(ydl, info)
                print(f"[download/local] Strategy {idx}.{auth_idx} SUCCEEDED! ready: {path}", flush=True)
                return path
            except Exception as err:
                import traceback
                tb_str = traceback.format_exc()
                all_errors.append(f"Strategy {idx}.{auth_idx} ({clients_label} [{auth_label}]) -> {type(err).__name__}: {err}")
                print("\n" + "!" * 40, flush=True)
                print(f"[download/local] CRITICAL ERROR IN STRATEGY {idx}.{auth_idx} ({clients_label} [{auth_label}]):", flush=True)
                print(tb_str, flush=True)
                print("!" * 40 + "\n", flush=True)

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
            all_errors.append(f"Browser {browser} -> {berr}")
            clean_err = str(berr).split("\n")[0]
            print(f"[download/local] '{browser}' extraction skipped/failed: {clean_err}", flush=True)

    # Phase 3: NUCLEAR LIFEBOAT - Pytubefix Alternative Library
    print("[download/local] ALL yt-dlp pathways EXHAUSTED. Deploying NUCLEAR LIFEBOAT: pytubefix...", flush=True)
    try:
        from pytubefix import YouTube
        from pytubefix.cli import on_progress
        print(f"[download/local] pytubefix: Initializing for {video_url}...", flush=True)
        yt = YouTube(video_url, on_progress_callback=on_progress)
        
        # Try finding standard format progressive mp4 at required resolution or lower
        stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
        if not stream:
            # Fallback to combined best
            stream = yt.streams.get_highest_resolution()
            
        if stream:
            print(f"[download/local] pytubefix: Stream found ({stream.resolution}). Downloading...", flush=True)
            # Define out_tmpl logic manually
            final_filename = f"source_{yt.video_id}.mp4"
            downloaded_path = stream.download(output_path=out_dir, filename=final_filename)
            print(f"[download/local] pytubefix: SUCCESS!!! Video saved to {downloaded_path}", flush=True)
            return downloaded_path
        else:
            print("[download/local] pytubefix: No suitable streams found.", flush=True)
    except Exception as pyerr:
        all_errors.append(f"Pytubefix Lifeboat -> {pyerr}")
        print(f"[download/local] NUCLEAR LIFEBOAT FAILED: {pyerr}", flush=True)

    full_error_summary = "\n".join(all_errors)
    raise RuntimeError(f"YouTube download failed all attempts.\n\n=== ERROR SUMMARY ===\n{full_error_summary}\n=====================\n")
