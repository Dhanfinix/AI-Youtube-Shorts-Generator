import os
import pickle
import socket
from typing import Optional

import google.oauth2.credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

# SCOPES needed for full access to upload and manage your YouTube channel
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'
CLIENT_SECRETS_FILE = 'client_secrets.json'
TOKEN_PICKLE = 'token.pickle'

def get_authenticated_service():
    """Load saved creds or run local flow to authenticate via client_secrets.json."""
    credentials = None
    
    if os.path.exists(TOKEN_PICKLE):
        with open(TOKEN_PICKLE, 'rb') as token:
            credentials = pickle.load(token)
            
    # If credentials not available or invalid, login
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            print("[uploader] Refreshing existing credentials...", flush=True)
            try:
                credentials.refresh(Request())
            except Exception as e:
                print(f"[uploader] Refresh failed ({e}), restarting auth.", flush=True)
                credentials = None
                
        if not credentials:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(
                    f"\nCRITICAL: Missing '{CLIENT_SECRETS_FILE}'!\n"
                    "To enable uploading, please obtain OAuth 2.0 Client ID credentials from Google Cloud Console\n"
                    "and place the file in the root directory naming it exactly 'client_secrets.json'.\n"
                )
            
            print("\n" + "="*60)
            print("  🔑  STARTING GOOGLE OAUTH AUTHENTICATION  🔑")
            print("  Check your default browser window to log in...")
            print("="*60 + "\n")
            
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        with open(TOKEN_PICKLE, 'wb') as token:
            pickle.dump(credentials, token)
            print(f"[uploader] Saved session to {TOKEN_PICKLE}.", flush=True)

    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)


def upload_video_to_youtube(
    video_path: str,
    title: str,
    description: str,
    privacy: str = "private",
    thumbnail_path: Optional[str] = None
) -> str:
    """
    Orchestrates standard chunked upload of a video, and sets thumbnail if provided.
    Returns the video ID string on success.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    print(f"\n[uploader] Initializing upload for: {os.path.basename(video_path)}", flush=True)
    youtube = get_authenticated_service()
    
    # Truncate title if too long (YT limit 100)
    safe_title = title[:95] + "..." if len(title) > 100 else title
    
    # Build the Resource body
    body = {
        'snippet': {
            'title': safe_title,
            'description': description,
            'tags': ['shorts', 'aishorts'],
            'categoryId': '22'  # People & Blogs
        },
        'status': {
            'privacyStatus': privacy,
            'selfDeclaredMadeForKids': False
        }
    }
    
    # Prepare the file stream (resumable upload capability)
    media = MediaFileUpload(
        video_path, 
        mimetype='video/mp4', 
        chunksize=1024*1024, 
        resumable=True
    )
    
    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )
    
    video_id = None
    print(f"[uploader] Pushing to YouTube ({privacy})...", flush=True)
    
    try:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"[uploader]   Progress: {int(status.progress() * 100)}%", flush=True)
                
        video_id = response.get("id")
        print(f"\n✅ SUCCESS: Video uploaded! Video ID: {video_id}", flush=True)
        print(f"🔗 URL: https://youtu.be/{video_id}", flush=True)
        
    except HttpError as e:
        # Graceful Catch for Quota Exceeded
        if e.resp.status in [403]:
            content_str = e.content.decode('utf-8')
            if "quotaExceeded" in content_str:
                print("\n❌ QUOTA EXCEEDED: Daily YouTube API limit hit. Please try again tomorrow.", flush=True)
                raise Exception("DAILY_QUOTA_HIT")
            else:
                print(f"\n❌ HTTP 403 Error: {e.content}", flush=True)
                raise
        else:
            print(f"\n❌ An HTTP error {e.resp.status} occurred:\n{e.content}", flush=True)
            raise
    except Exception as generic_ex:
        print(f"\n❌ An unexpected error occurred: {generic_ex}", flush=True)
        raise

    # Step 2: Upload Custom Thumbnail if provided
    if video_id and thumbnail_path and os.path.exists(thumbnail_path):
        print(f"[uploader] Pushing custom thumbnail: {os.path.basename(thumbnail_path)}...", flush=True)
        try:
            # Standard set requires application/octet-stream or image/jpeg
            thumb_media = MediaFileUpload(
                thumbnail_path, 
                mimetype='image/jpeg'
            )
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=thumb_media
            ).execute()
            print("🎨 Thumbnail set successfully!", flush=True)
        except HttpError as te:
            print(f"⚠️ Warning: Could not set thumbnail ({te.resp.status}). (Note: requires verified YT channel)", flush=True)
        except Exception as ex:
            print(f"⚠️ Warning: Thumbnail hook failed ({ex})", flush=True)

    return video_id
