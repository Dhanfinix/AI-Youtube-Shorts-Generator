#!/usr/bin/env python3
"""YtClipper Local Sync Daemon.

Background helper script for your local Mac machine. It automatically
detects new YouTube links in your Google Sheet, downloads them using your safe
residential IP, uploads them to your Google Drive, and replaces the YouTube URL in
the Sheet with the Google Drive link so GitHub Actions can process them instantly without bot challenges!
"""
import os
import sys
import json
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment
load_dotenv()

try:
    import gspread
    import yt_dlp
    import requests
    from google.oauth2.service_account import Credentials
except ImportError:
    print("Please ensure dependencies are installed:")
    print("    pip install gspread yt-dlp requests google-auth")
    sys.exit(1)

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "YtClipper_Jobs")


def get_creds():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    sa_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json_str:
        return Credentials.from_service_account_info(json.loads(sa_json_str), scopes=scopes)
    if os.path.exists("google_credentials.json"):
        return Credentials.from_service_account_file("google_credentials.json", scopes=scopes)
    raise RuntimeError(
        "No Google credentials found. Please set GOOGLE_SERVICE_ACCOUNT_JSON in .env "
        "or place your google_credentials.json file in the root folder."
    )


def upload_to_drive(file_path, filename, creds):
    """Uploads file to Google Drive and returns shareable webContentLink."""
    import google.auth.transport.requests as tr_requests
    auth_req = tr_requests.Request()
    creds.refresh(auth_req)
    access_token = creds.token
    
    # 1. Create file metadata
    metadata = {
        "name": filename,
        "mimeType": "video/mp4"
    }
    
    # 2. Upload file content
    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    files = {
        "data": ("metadata", json.dumps(metadata), "application/json"),
        "file": (filename, open(file_path, "rb"), "video/mp4")
    }
    
    print(f"[local-sync] Uploading {filename} to Google Drive...", flush=True)
    res = requests.post(url, headers=headers, files=files, timeout=300)
    if res.status_code != 200:
        raise RuntimeError(f"Drive upload failed: {res.text}")
        
    file_id = res.json().get("id")
    print(f"[local-sync] Uploaded successfully! File ID: {file_id}", flush=True)
    
    # 3. Create public permission so anyone with link can download (required for GitHub Action)
    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    perm_metadata = {
        "role": "reader",
        "type": "anyone"
    }
    perm_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    requests.post(perm_url, headers=perm_headers, json=perm_metadata, timeout=30)
    
    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"


def main():
    print("===================================================", flush=True)
    print("🌟 YtClipper Local Sync Daemon Started!", flush=True)
    print("Checking Google Sheet for pending YouTube jobs...", flush=True)
    print("===================================================", flush=True)
    
    try:
        creds = get_creds()
        gc = gspread.authorize(creds)
        sheet = gc.open(GOOGLE_SHEET_NAME).sheet1
    except Exception as e:
        print(f"[local-sync] Initialization failed: {e}", flush=True)
        return

    while True:
        try:
            records = sheet.get_all_records()
            for idx, r in enumerate(records, start=2):
                status = str(r.get("Status", "")).strip().lower()
                url = str(r.get("YouTube URL", "")).strip()
                
                # Check for pending rows with YouTube links
                if status == "pending" and url.startswith("http") and ("youtube.com" in url or "youtu.be" in url):
                    print(f"\n[local-sync] Found pending YouTube job at row {idx}: {url}", flush=True)
                    
                    # 1. Download video locally using safe residential IP
                    os.makedirs("temp_downloads", exist_ok=True)
                    ydl_opts = {
                        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
                        "outtmpl": "temp_downloads/source_temp.%(ext)s",
                        "quiet": True,
                        "no_warnings": True,
                    }
                    print("[local-sync] Downloading video locally via yt-dlp...", flush=True)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        local_path = ydl.prepare_filename(info)
                        video_id = info.get("id", "video")
                        
                    # 2. Upload to Google Drive and make it shareable
                    drive_link = upload_to_drive(local_path, f"source_{video_id}.mp4", creds)
                    
                    # 3. Clean up the local download file
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    
                    # 4. Update YouTube URL in Google Sheet with the new Drive link
                    sheet.update_cell(idx, 2, drive_link)
                    print(f"[local-sync] Replaced YouTube URL with Google Drive link: {drive_link}", flush=True)
                    print("[local-sync] Row updated in Google Sheets! Ready for GitHub Actions processing.", flush=True)
                    
            time.sleep(15) # Check every 15 seconds
        except Exception as err:
            print(f"[local-sync] Error in daemon loop: {err}", flush=True)
            time.sleep(15)


if __name__ == "__main__":
    main()
