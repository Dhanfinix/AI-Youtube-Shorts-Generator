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
    # Strategy 3: web + web_creator
    ["web", "web_creator"],
    # Strategy 4: ios client (different bot-detection path)
    ["ios"],
    # Strategy 5: android_vr (least restricted historically)
    ["android_vr"],
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

    for idx, strategy in enumerate(_FALLBACK_STRATEGIES):
        extractor_args = _youtube_extractor_args(player_clients_override=strategy)
        clients_label = strategy or (os.getenv("YT_DLP_PLAYER_CLIENTS") or "web_creator,default")
        print(f"[download/local] Strategy {idx}: player_client={clients_label}", flush=True)

        ydl_opts = {
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
            "extractor_args": extractor_args,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if ytdlp_proxy:
            ydl_opts["proxy"] = ytdlp_proxy
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path

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
            # Only continue retrying for bot-detection / auth errors
            err_str = str(err).lower()
            is_bot_error = any(
                phrase in err_str
                for phrase in [
                    "sign in to confirm",
                    "not a bot",
                    "confirm your age",
                    "login required",
                    "403",
                ]
            )
            if not is_bot_error:
                # Non-auth error (e.g. video not found, network timeout) — stop retrying
                break

    # All strategies exhausted
    raise RuntimeError(f"yt-dlp download failed after all strategies: {last_err}")
