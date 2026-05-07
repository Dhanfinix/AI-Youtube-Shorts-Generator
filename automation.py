#!/usr/bin/env python3
"""YtClipper Standalone Automation Pipeline.

Synchronizes with a Google Sheet to pull pending YouTube clipping jobs,
processes them in local mode, sends the resulting vertical video files
directly to Telegram, and updates the row status.
"""
import os
import sys
import json
import time
import traceback
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from shorts_generator import generate_shorts

# Configuration from environment variables
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "YtClipper_Jobs")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def get_gspread_client():
    """Authenticate and return gspread client."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    # Try loading from GOOGLE_SERVICE_ACCOUNT_JSON env variable (for GitHub Actions / Production)
    sa_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json_str:
        try:
            sa_info = json.loads(sa_json_str)
            creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"[automation] Failed to authenticate from GOOGLE_SERVICE_ACCOUNT_JSON env: {e}")

    # Fallback to local file credentials
    local_credentials_path = "google_credentials.json"
    if os.path.exists(local_credentials_path):
        try:
            creds = Credentials.from_service_account_file(local_credentials_path, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"[automation] Failed to authenticate from local file {local_credentials_path}: {e}")

    raise RuntimeError(
        "No valid Google credentials found. Please set GOOGLE_SERVICE_ACCOUNT_JSON environment variable "
        "or provide a local google_credentials.json file."
    )


def send_telegram_video(video_path: str, caption: str) -> bool:
    """Send video to Telegram chat via Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[automation/telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured. Skipping upload.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    print(f"[automation/telegram] Uploading {os.path.basename(video_path)} to Telegram...", flush=True)

    try:
        with open(video_path, "rb") as video_file:
            files = {"video": video_file}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "Markdown",
                "supports_streaming": True,
            }
            # Set a high timeout because video files can be large
            response = requests.post(url, data=data, files=files, timeout=120)
            result = response.json()
            if response.status_code == 200 and result.get("ok"):
                print("[automation/telegram] Video sent successfully!", flush=True)
                return True
            else:
                print(f"[automation/telegram] Failed to send video: {result}", flush=True)
    except Exception as e:
        print(f"[automation/telegram] Error during Telegram upload: {e}", flush=True)
    return False


def process_automation():
    """Main execution block for pulling, running, and updating rows."""
    print(f"[automation] Connecting to Google Sheet: '{GOOGLE_SHEET_NAME}'...", flush=True)
    client = get_gspread_client()
    
    try:
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
    except Exception as e:
        print(f"[automation] Could not find or open sheet named '{GOOGLE_SHEET_NAME}'. Ensure it exists and is shared with your Service Account client email.")
        sys.exit(1)

    # Expected column headers:
    # 1: Timestamp | 2: YouTube URL | 3: Clips Count | 4: Language | 5: Status | 6: Processed At | 7: Clips Output
    records = sheet.get_all_records()
    pending_row_idx = None
    job_data = {}

    for idx, r in enumerate(records, start=2): # Headers are row 1, data starts at row 2
        status = str(r.get("Status", "")).strip().lower()
        url = str(r.get("YouTube URL", "")).strip()
        if status == "pending" and url:
            pending_row_idx = idx
            job_data = r
            break

    if not pending_row_idx:
        print("[automation] No pending jobs found. Exiting gracefully.", flush=True)
        return

    youtube_url = job_data["YouTube URL"]
    clips_count = int(job_data.get("Clips Count", 3) or 3)
    language_code = job_data.get("Language", "auto")
    if str(language_code).strip().lower() in ["", "auto", "none"]:
        language_code = None

    print(f"\n[automation] Job Found at Row {pending_row_idx}:", flush=True)
    print(f" - URL:        {youtube_url}", flush=True)
    print(f" - Clips:      {clips_count}", flush=True)
    print(f" - Language:   {language_code or 'auto-detect'}", flush=True)

    # 1. Lock Row to "Processing"
    sheet.update_cell(pending_row_idx, 5, "Processing")
    sheet.update_cell(pending_row_idx, 6, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        # 2. Run YtClipper Local Generator
        result = generate_shorts(
            youtube_url=youtube_url,
            num_clips=clips_count,
            aspect_ratio="9:16",
            download_format="1080",
            language=language_code,
            mode="local",
        )

        # 3. Post Rendered Clips to Telegram
        success_clips = []
        for i, s in enumerate(result.get("shorts", []), start=1):
            clip_path = s.get("clip_url")
            title = s.get("title", f"Clip #{i}")
            hook = s.get("hook_sentence", "")
            score = s.get("score", 95)

            if not clip_path or not os.path.exists(clip_path):
                print(f"[automation] Clip {i} file not found: {clip_path}", flush=True)
                continue

            caption = (
                f"🌟 *{title}*\n"
                f"🔥 {hook}\n\n"
                f"📈 *Virality Score:* {score}%\n"
                f"🎥 *Source:* {youtube_url}"
            )
            
            tele_ok = send_telegram_video(clip_path, caption)
            if tele_ok:
                success_clips.append(f"#{i} - {title} ({score}%)")
            else:
                success_clips.append(f"#{i} - {title} [Failed Upload]")

        # 4. Update row to "Done"
        sheet.update_cell(pending_row_idx, 5, "Done")
        sheet.update_cell(pending_row_idx, 7, "\n".join(success_clips))
        print(f"[automation] Job Row {pending_row_idx} processed successfully!", flush=True)

    except Exception as err:
        tb = traceback.format_exc()
        print(f"[automation] Failed to process Job Row {pending_row_idx}:\n{tb}", flush=True)
        # Mark row as "Failed" and write traceback to Clips Output
        sheet.update_cell(pending_row_idx, 5, "Failed")
        sheet.update_cell(pending_row_idx, 7, f"Error: {err}\n\n{tb[:500]}...")


if __name__ == "__main__":
    process_automation()
