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
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

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
        with requests.get(download_url, stream=True, timeout=60) as r:
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
                        res = requests.post(cobalt_url, json=payload, headers=headers, timeout=15)
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
                    with requests.get(download_url, stream=True, timeout=60) as r:
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
                    inst_res = requests.get("https://api.invidious.io/instances.json?pretty=1&sort_by=type,users", timeout=10)
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
                            print(f"[download/local] Querying Invidious instance: {instance}...", flush=True)
                            api_url = f"{instance}/api/v1/videos/{video_id}"
                            res = requests.get(api_url, headers=req_headers, timeout=10)
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
                                    if download_url:
                                        selected_instance = instance
                                        break
                        except Exception as inst_err:
                            print(f"[download/local] Instance {instance} query failed: {inst_err}", flush=True)
                            continue
                            
                    if download_url:
                        out_filename = f"source_{video_id}.mp4"
                        path = os.path.join(out_dir, out_filename)
                        print(f"[download/local] Downloading direct MP4 from Invidious instance ({selected_instance}) to {path}...", flush=True)
                        with requests.get(download_url, headers=req_headers, stream=True, timeout=90) as r:
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
