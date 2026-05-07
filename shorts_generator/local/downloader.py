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
    cookies_b64_env = os.getenv("YOUTUBE_COOKIES_BASE64")
    cookies_file_env = os.getenv("YOUTUBE_COOKIES_FILE")

    if cookies_b64_env:
        import base64

        try:
            decoded = base64.b64decode(cookies_b64_env).decode("utf-8")
        except Exception as e:
            print(f"[download/local] Error decoding YOUTUBE_COOKIES_BASE64: {e}", flush=True)
        else:
            return _write_cookie_secret(out_dir, decoded, "YOUTUBE_COOKIES_BASE64")

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


def _youtube_extractor_args() -> dict:
    player_clients = [
        item.strip()
        for item in (os.getenv("YT_DLP_PLAYER_CLIENTS") or "web,mweb,android").split(",")
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
        "extractor_args": _youtube_extractor_args(),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if ytdlp_proxy:
        ydl_opts["proxy"] = ytdlp_proxy

    cookie_path = _load_cookiefile(out_dir)
    if cookie_path:
        ydl_opts["cookiefile"] = cookie_path

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
            
            # Try initializing with 'WEB' client to trigger automatic PO token generation, fallback to use_po_token=True
            try:
                yt = YouTube(video_url, 'WEB')
            except Exception:
                yt = YouTube(video_url, use_po_token=True)
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
            # Try RapidAPI YTStream Fallback if key is available
            rapid_api_key = os.getenv("RAPID_YT_API_KEY") or os.getenv("RAPID_API_KEY")
            if rapid_api_key:
                print("[download/local] pytubefix failed. Attempting fallback to RapidAPI YTStream...", flush=True)
                try:
                    import requests
                    video_id = "video"
                    if "v=" in video_url:
                        video_id = video_url.split("v=")[1].split("&")[0]
                    elif "youtu.be/" in video_url:
                        video_id = video_url.split("youtu.be/")[1].split("?")[0]

                    headers = {
                        "x-rapidapi-host": "ytstream-download-youtube-videos.p.rapidapi.com",
                        "x-rapidapi-key": rapid_api_key,
                        "Content-Type": "application/json"
                    }
                    api_url = f"https://ytstream-download-youtube-videos.p.rapidapi.com/dl?id={video_id}"
                    res = requests.get(api_url, headers=headers, proxies=req_proxies, timeout=15)
                    if res.status_code == 200:
                        data = res.json()
                        download_url = data.get("link")
                        if not download_url:
                            formats = data.get("formats", []) or data.get("adaptiveFormats", [])
                            if isinstance(formats, list):
                                for f in formats:
                                    if f.get("quality") == "720p" or "720" in str(f.get("qualityLabel", "")):
                                        download_url = f.get("url")
                                        break
                                if not download_url and formats:
                                    download_url = formats[0].get("url")

                        if download_url:
                            out_filename = f"source_{video_id}.mp4"
                            path = os.path.join(out_dir, out_filename)
                            print(f"[download/local] Downloading direct MP4 from RapidAPI to {path}...", flush=True)
                            dl_headers = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                            }
                            with requests.get(download_url, headers=dl_headers, proxies=req_proxies, stream=True, timeout=90) as r:
                                r.raise_for_status()
                                with open(path, 'wb') as f:
                                    for chunk in r.iter_content(chunk_size=8192):
                                        f.write(chunk)
                            print(f"[download/local] ready via RapidAPI: {path}", flush=True)
                            return path
                except Exception as rapid_err:
                    print(f"[download/local] RapidAPI YTStream failed: {rapid_err}. Continuing with remaining fallbacks...", flush=True)

            print(f"[download/local] pytubefix failed ({pytube_err}). Attempting fallback to Cobalt API...", flush=True)
            try:
                import requests
                cobalt_instances = [
                    "https://co.wuk.sh/api/json",
                    "https://cobalt.luigi-pizz.app/api/json",
                    "https://cobalt.tuqa.me/api/json",
                    "https://cobalt.tools/api/json"
                ]
                download_url = None
                for cobalt_url in cobalt_instances:
                    try:
                        payload = {
                            "url": video_url,
                            "vCodec": "h264",
                            "vQuality": str(fmt),
                            "filenamePattern": "classic"
                        }
                        headers = {
                            "Accept": "application/json",
                            "Content-Type": "application/json"
                        }
                        res = requests.post(cobalt_url, json=payload, headers=headers, proxies=req_proxies, timeout=15)
                        if res.status_code == 200:
                            data = res.json()
                            if data.get("status") == "redirect" or data.get("status") == "stream":
                                download_url = data.get("url")
                                break
                    except Exception as e:
                        print(f"[download/local] Failed to contact Cobalt instance {cobalt_url}: {e}", flush=True)
                
                if download_url:
                    video_id = "video"
                    if "v=" in video_url:
                        video_id = video_url.split("v=")[1].split("&")[0]
                    elif "youtu.be/" in video_url:
                        video_id = video_url.split("youtu.be/")[1].split("?")[0]
                    
                    out_filename = f"source_{video_id}.mp4"
                    path = os.path.join(out_dir, out_filename)
                    
                    print(f"[download/local] Downloading direct MP4 from Cobalt API to {path}...", flush=True)
                    with requests.get(download_url, proxies=req_proxies, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        with open(path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                else:
                    raise RuntimeError("Cobalt API returned no download URL.")
            except Exception as cobalt_err:
                print(f"[download/local] Cobalt API failed ({cobalt_err}). Attempting fallback to Invidious public instances...", flush=True)
                try:
                    import requests
                    video_id = "video"
                    if "v=" in video_url:
                        video_id = video_url.split("v=")[1].split("&")[0]
                    elif "youtu.be/" in video_url:
                        video_id = video_url.split("youtu.be/")[1].split("?")[0]
                    
                    print("[download/local] Fetching active Invidious instances list (sorted by type and users)...", flush=True)
                    inst_res = requests.get(
                        "https://api.invidious.io/instances.json?pretty=1&sort_by=type,users",
                        proxies=req_proxies,
                        timeout=10,
                    )
                    instances_data = inst_res.json()
                    
                    instances = []
                    for item in instances_data:
                        if isinstance(item, list) and len(item) == 2:
                            hostname, info = item
                            # 'online' property was removed by Invidious registry, 'api' can be True, False, or None
                            if isinstance(info, dict) and info.get("type") == "https" and info.get("api") is True:
                                instances.append(f"https://{hostname}")
                    
                    if not instances:
                        instances = ["https://inv.thepixora.com", "https://yewtu.be", "https://vid.puffyan.us"]
                        
                    download_url = None
                    selected_instance = None
                    # Standard User-Agent to bypass Cloudflare blocks on Invidious instances
                    req_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                    
                    for instance in instances[:15]: # Try first 15 instances to be safe
                        try:
                            # Option 1: Try the direct proxied streaming URL via local=true to bypass Google IP bans completely!
                            for itag in ("22", "18"):
                                test_url = f"{instance}/latest_version?id={video_id}&itag={itag}&local=true"
                                print(f"[download/local] Trying Invidious proxied stream: {test_url}...", flush=True)
                                try:
                                    res = requests.get(test_url, headers=req_headers, proxies=req_proxies, stream=True, timeout=10)
                                    if res.status_code == 200:
                                        download_url = test_url
                                        selected_instance = instance
                                        break
                                except Exception:
                                    continue
                            if download_url:
                                break

                            # Option 2: Fallback to querying Invidious API
                            print(f"[download/local] Querying Invidious instance API: {instance}...", flush=True)
                            api_url = f"{instance}/api/v1/videos/{video_id}"
                            res = requests.get(api_url, headers=req_headers, proxies=req_proxies, timeout=10)
                            if res.status_code == 200:
                                data = res.json()
                                streams = data.get("formatStreams", [])
                                if streams:
                                    selected_stream = None
                                    for s in streams:
                                        if s.get("height") == 720 or s.get("quality") == "hd720":
                                            selected_stream = s
                                            break
                                    if not selected_stream:
                                        selected_stream = streams[0]
                                    
                                    download_url = selected_stream.get("url")
                                    # Append &local=true if it is a googlevideo stream to force proxying through the instance!
                                    if download_url:
                                        if "googlevideo.com" in download_url:
                                            download_url += "&local=true"
                                        selected_instance = instance
                                        break
                        except Exception as inst_err:
                            print(f"[download/local] Instance {instance} query/proxy failed: {inst_err}", flush=True)
                            continue
                            
                    if download_url:
                        out_filename = f"source_{video_id}.mp4"
                        path = os.path.join(out_dir, out_filename)
                        print(f"[download/local] Downloading direct MP4 from Invidious instance ({selected_instance}) to {path}...", flush=True)
                        with requests.get(download_url, headers=req_headers, proxies=req_proxies, stream=True, timeout=90) as r:
                            r.raise_for_status()
                            with open(path, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192):
                                    f.write(chunk)
                    else:
                        raise RuntimeError("No healthy Invidious instances returned video streams.")
                except Exception as invidious_err:
                    raise RuntimeError(
                        f"All four downloaders failed.\n"
                        f"  - yt-dlp error: {ytdl_err}\n"
                        f"  - pytubefix error: {pytube_err}\n"
                        f"  - Cobalt API error: {cobalt_err}\n"
                        f"  - Invidious API error: {invidious_err}"
                    ) from invidious_err

    print(f"[download/local] ready: {path}", flush=True)
    return path
