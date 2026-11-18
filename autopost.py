#!/usr/bin/env python3
"""autopost: one scheduled run = pick the best unposted GTA VI clip from an allowlisted streamer,
turn it into a polished 9:16 Short, and upload it to YouTube. Designed to run hourly in GitHub Actions.

Environment:
  TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET      Twitch app credentials
  YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN   YouTube OAuth (see README)
  GAME (default "Grand Theft Auto VI"), MAX_PER_DAY (default 6: YouTube API quota allows ~6 uploads/day),
  CLIP_DAYS (default 2), YT_PRIVACY (default "public"), DRY_RUN=1 (do everything except upload)

Streamers come from allowlist.txt (one Twitch login per line). The run refuses to start if it is empty.
"""

import datetime
import importlib.util
import json
import os
import sys
import urllib.error
from pathlib import Path
from typing import Dict, List

BASE_DIRECTORY: Path = Path(__file__).resolve().parent
STATE_PATH: Path = BASE_DIRECTORY / "state" / "posted.json"
ALLOWLIST_PATH: Path = Path(os.environ.get("ALLOWLIST_PATH", BASE_DIRECTORY / "allowlist.txt"))


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, BASE_DIRECTORY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_state() -> Dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {"posted": []}


def save_state(state: Dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def main() -> int:
    allowed_streamers: List[str] = []
    if ALLOWLIST_PATH.exists():
        allowed_streamers = [line.strip().lower() for line in ALLOWLIST_PATH.read_text().splitlines()
                             if line.strip() and not line.startswith("#")]
    if not allowed_streamers:
        print("allowlist.txt is empty. Add the Twitch logins of streamers who allow clipping, one per line.")
        return 2

    dry_run: bool = os.environ.get("DRY_RUN") == "1"
    max_per_day: int = int(os.environ.get("MAX_PER_DAY", "6"))
    clip_days: int = int(os.environ.get("CLIP_DAYS", "2"))
    privacy: str = os.environ.get("YT_PRIVACY", "public")
    game_name: str = os.environ.get("GAME", "Grand Theft Auto VI")

    state = load_state()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    posted_today = sum(1 for entry in state["posted"] if entry["at"].startswith(today))
    if posted_today >= max_per_day and not dry_run:
        print(f"daily cap reached ({posted_today}/{max_per_day}); nothing to do.")
        return 0
    already_posted = {entry["clip_id"] for entry in state["posted"]}

    top_streamer = load_module("top_streamer")
    credentials = top_streamer.load_credentials()
    token = top_streamer.app_access_token(credentials)
    category = top_streamer.find_category(game_name, credentials, token)

    if "*" in allowed_streamers:
        # Explicit opt-in: use whoever is most-watched in the category right now (no per-streamer permission).
        print("NOTE: allowlist contains '*': using the top live streamers in the category. "
              "You are responsible for having the right to repost their clips.")
        live_streams = top_streamer.helix("streams", {"game_id": category["id"], "first": "10"}, credentials, token)
        allowed_streamers = [stream["user_login"].lower() for stream in live_streams]

    started_at = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=clip_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidates: List[Dict] = []
    for login in allowed_streamers:
        users = top_streamer.helix("users", {"login": login}, credentials, token)
        if not users:
            print(f"  {login}: not found on Twitch, skipping")
            continue
        clips = top_streamer.helix("clips", {"broadcaster_id": users[0]["id"], "started_at": started_at,
                                             "first": "100"}, credentials, token)
        matching = [clip for clip in clips
                    if clip.get("game_id") == category["id"] and clip["id"] not in already_posted
                    and 10 <= clip["duration"] <= 60]
        print(f"  {login}: {len(matching)} unposted {category['name']} clips in the last {clip_days} days")
        for clip in matching:
            clip["_login"] = login
        candidates += matching

    if not candidates:
        print("no new clips from the allowlisted streamers; nothing to post this hour.")
        return 0
    best = max(candidates, key=lambda clip: clip["view_count"])
    print(f"chosen: {best['_login']} - '{best['title']}' ({best['view_count']} views) {best['url']}")

    final_path = top_streamer.download_and_polish(best, best["_login"])
    metadata = json.loads(final_path.with_suffix(".post.json").read_text())
    # Add the game tags to the title/description hashtags.
    for extra_tag in ("#GTA6", "#GTAVI"):
        if extra_tag not in metadata["hashtags"]:
            metadata["hashtags"].append(extra_tag)
            metadata["youtube_description"] += f" {extra_tag}"

    if dry_run:
        print(f"DRY_RUN: would upload {final_path} as '{metadata['youtube_title']}' ({privacy}); skipping upload.")
        return 0

    uploader = load_module("post_to_youtube")
    try:
        video_id = uploader.upload(final_path, metadata, privacy, uploader.access_token(uploader.load_client()))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode(errors="replace")
        if error.code == 403 and "quota" in error_body.lower():
            # YouTube's daily upload quota is used up: skip this hour instead of failing the workflow.
            print("YouTube daily quota exhausted; will try again next hour.")
            return 0
        print(f"YouTube upload failed ({error.code}): {error_body[:500]}")
        return 1
    print(f"uploaded: https://youtube.com/shorts/{video_id}")
    state["posted"].append({"clip_id": best["id"], "streamer": best["_login"], "video_id": video_id,
                            "at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
