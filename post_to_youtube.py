#!/usr/bin/env python3
"""post_to_youtube: upload a clip to YOUR YouTube channel, only after you confirm.

Usage:
  post_to_youtube.py <video.mp4> [--meta video.post.json] [--privacy private|unlisted|public] [--dry-run]

Setup (once): create an OAuth "Desktop app" client in Google Cloud Console (YouTube Data API v3 enabled),
download its JSON and save it as ~/.clip-watcher/client_secret.json. The first run opens your browser so YOU
sign in and approve; a refresh token is then stored in ~/.clip-watcher/youtube_token.json (mode 600).
This script never sees your Google password.

Nothing is uploaded until you type the word  post  at the prompt.
"""

import argparse
import http.server
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, Optional

CONFIG_DIRECTORY: Path = Path.home() / ".clip-watcher"
CLIENT_SECRET_PATH: Path = CONFIG_DIRECTORY / "client_secret.json"
TOKEN_PATH: Path = CONFIG_DIRECTORY / "youtube_token.json"
UPLOAD_SCOPE: str = "https://www.googleapis.com/auth/youtube.upload"
GAMING_CATEGORY_ID: str = "20"


def http_json(url: str, data: Dict[str, str]) -> Dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def load_client() -> Dict:
    # CI: credentials come from environment variables (GitHub Actions secrets) instead of files.
    if os.environ.get("YT_CLIENT_ID") and os.environ.get("YT_CLIENT_SECRET"):
        return {"client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"}
    if not CLIENT_SECRET_PATH.exists():
        sys.exit(f"Missing {CLIENT_SECRET_PATH}. See the setup notes at the top of this script.")
    raw = json.loads(CLIENT_SECRET_PATH.read_text())
    return raw.get("installed") or raw.get("web") or sys.exit("client_secret.json has no 'installed' section")


def save_token(token: Dict) -> None:
    CONFIG_DIRECTORY.mkdir(exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(token))
    os.chmod(TOKEN_PATH, 0o600)


def browser_sign_in(client: Dict) -> Dict:
    """Loopback OAuth: you approve in your browser; we only receive a one-time code."""
    received: Dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            received.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"You can close this tab and return to the terminal.")

        def log_message(self, *args):  # silence request logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    query = urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": UPLOAD_SCOPE, "access_type": "offline", "prompt": "consent"})
    print("Opening your browser to sign in to Google...")
    webbrowser.open(f"{client['auth_uri']}?{query}")
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    thread.join(timeout=300)
    if "code" not in received:
        sys.exit("No authorization received (cancelled or timed out).")
    token = http_json(client["token_uri"], {
        "code": received["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    save_token(token)
    return token


def access_token(client: Dict) -> str:
    if os.environ.get("YT_REFRESH_TOKEN"):
        stored = {"refresh_token": os.environ["YT_REFRESH_TOKEN"]}
    elif not TOKEN_PATH.exists():
        return browser_sign_in(client)["access_token"]
    else:
        stored = json.loads(TOKEN_PATH.read_text())
    refreshed = http_json(client["token_uri"], {
        "refresh_token": stored["refresh_token"], "client_id": client["client_id"],
        "client_secret": client["client_secret"], "grant_type": "refresh_token"})
    return refreshed["access_token"]


def upload(video_path: Path, metadata: Dict, privacy: str, token: str) -> str:
    body = json.dumps({
        "snippet": {"title": metadata["youtube_title"][:100], "description": metadata["youtube_description"],
                    "tags": [tag.lstrip("#") for tag in metadata.get("hashtags", [])],
                    "categoryId": GAMING_CATEGORY_ID},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }).encode()
    size = video_path.stat().st_size
    start = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=body, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    with urllib.request.urlopen(start, timeout=60) as response:
        session_url = response.headers["Location"]
    put = urllib.request.Request(session_url, data=video_path.read_bytes(), method="PUT",
                                 headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    with urllib.request.urlopen(put, timeout=1800) as response:
        return json.load(response)["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a clip to YouTube after explicit confirmation.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--meta", type=Path, help="JSON with youtube_title / youtube_description / hashtags")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    parser.add_argument("--dry-run", action="store_true", help="show what would be posted, never upload")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt (used by the scheduled workflow)")
    arguments = parser.parse_args()

    meta_path: Path = arguments.meta or arguments.video.with_suffix(".post.json")
    metadata = json.loads(meta_path.read_text())

    print("\n--- ABOUT TO POST ---")
    print(f"video:       {arguments.video}  ({arguments.video.stat().st_size / 1e6:.1f} MB)")
    print(f"title:       {metadata['youtube_title']}")
    print(f"privacy:     {arguments.privacy}")
    print(f"hashtags:    {' '.join(metadata.get('hashtags', []))}")
    print(f"description:\n{metadata['youtube_description']}\n---------------------")
    if arguments.dry_run:
        print("dry run: nothing uploaded.")
        return 0
    if not arguments.yes and input("Type  post  to upload to your channel (anything else cancels): ").strip().lower() != "post":
        print("Cancelled. Nothing was uploaded.")
        return 1

    video_id = upload(arguments.video, metadata, arguments.privacy, access_token(load_client()))
    print(f"Uploaded: https://youtube.com/shorts/{video_id}  (privacy: {arguments.privacy})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
