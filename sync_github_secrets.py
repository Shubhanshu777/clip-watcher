#!/usr/bin/env python3
"""sync_github_secrets: copy your local YouTube/Twitch credentials into GitHub Actions secrets in one step.

Reads ~/.clip-watcher/{client_secret.json, youtube_token.json, twitch.json} and runs `gh secret set` for each
value, so the OAuth client id/secret and the refresh token always come from the SAME sign-in (a mismatch
causes Google's "invalid_grant"). Nothing is printed except which secret names were set.

Usage: python3 -I sync_github_secrets.py [owner/repo]     (default: Shubhanshu777/clip-watcher)
"""

import json
import subprocess
import sys
from pathlib import Path

repo = sys.argv[1] if len(sys.argv) > 1 else "Shubhanshu777/clip-watcher"
config = Path.home() / ".clip-watcher"

client = json.loads((config / "client_secret.json").read_text())
client = client.get("installed") or client.get("web")
token = json.loads((config / "youtube_token.json").read_text())
if "refresh_token" not in token:
    sys.exit("youtube_token.json has no refresh_token: run post_to_youtube.py and sign in again first.")
twitch = json.loads((config / "twitch.json").read_text())

secrets = {
    "YT_CLIENT_ID": client["client_id"].strip(),
    "YT_CLIENT_SECRET": client["client_secret"].strip(),
    "YT_REFRESH_TOKEN": token["refresh_token"].strip(),
    "TWITCH_CLIENT_ID": twitch["client_id"].strip(),
    "TWITCH_CLIENT_SECRET": twitch["client_secret"].strip(),
}
for name, value in secrets.items():
    result = subprocess.run(["gh", "secret", "set", name, "--repo", repo], input=value, text=True,
                            capture_output=True)
    print(f"{name}: {'ok' if result.returncode == 0 else 'FAILED ' + result.stderr.strip()[:120]}")
