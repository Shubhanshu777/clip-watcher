#!/usr/bin/env python3
"""top_streamer: find the most-watched live streamer in a Twitch category and fetch their recent clips.

Usage:
  top_streamer.py                          # list the top live streams in the category
  top_streamer.py --clips                  # also list that streamer's top clips in the category (last 7 days)
  top_streamer.py --clips --download 2     # download the top 2 clips, make 9:16 versions, polish them,
                                           # and write a .post.json (nothing is uploaded)
  top_streamer.py --allowlist ok.txt       # only consider streamers listed in ok.txt (one login per line)

Credentials (free): register an app at https://dev.twitch.tv/console, then set TWITCH_CLIENT_ID and
TWITCH_CLIENT_SECRET, or put {"client_id": "...", "client_secret": "..."} in ~/.clip-watcher/twitch.json.

Reminder: the stream and its clips belong to the streamer (and the game footage to its publisher). Get their
permission before reposting; use --allowlist to enforce that.
"""

import argparse
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIRECTORY: Path = Path(__file__).resolve().parent
CLIPS_DIRECTORY: Path = BASE_DIRECTORY / "clips"
CONFIG_PATH: Path = Path.home() / ".clip-watcher" / "twitch.json"
HELIX: str = "https://api.twitch.tv/helix"


def load_credentials() -> Dict[str, str]:
    client_id, client_secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret}
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    sys.exit("Missing Twitch credentials. Set TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET or create "
             f"{CONFIG_PATH}. Register a free app at https://dev.twitch.tv/console")


def app_access_token(credentials: Dict[str, str]) -> str:
    body = urllib.parse.urlencode({**credentials, "grant_type": "client_credentials"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://id.twitch.tv/oauth2/token", data=body), timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as error:
        # "invalid client" = wrong Client ID; "invalid client secret" = wrong secret.
        sys.exit(f"Twitch rejected the credentials ({error.code}): {error.read().decode()}\n"
                 f"Check client_id / client_secret in {CONFIG_PATH}")


def helix(path: str, parameters: Dict[str, str], credentials: Dict[str, str], token: str) -> List[Dict]:
    url = f"{HELIX}/{path}?{urllib.parse.urlencode(parameters)}"
    request = urllib.request.Request(url, headers={"Client-Id": credentials["client_id"],
                                                    "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["data"]
    except urllib.error.HTTPError as error:
        sys.exit(f"Twitch API error {error.code} on /{path}: {error.read().decode()}")


def find_category(name: str, credentials, token) -> Dict:
    """Resolve a category by search; prefer an exact (case-insensitive) name match."""
    results = helix("search/categories", {"query": name, "first": "10"}, credentials, token)
    if not results:
        sys.exit(f"No Twitch category found for '{name}'.")
    exact = [item for item in results if item["name"].lower() == name.lower()]
    chosen = (exact or results)[0]
    print(f"category: {chosen['name']} (id {chosen['id']})")
    return chosen


def download_and_polish(clip: Dict, streamer_login: str) -> Optional[Path]:
    """Download one Twitch clip, make a 9:16 version, polish it, and write its .post.json. Uploads nothing."""
    CLIPS_DIRECTORY.mkdir(exist_ok=True)
    slug = clip["id"]
    raw_path = CLIPS_DIRECTORY / f"{streamer_login}_{slug}_raw.mp4"
    subprocess.run([str(BASE_DIRECTORY / ".venv" / "bin" / "streamlink") if (BASE_DIRECTORY / ".venv" / "bin" / "streamlink").exists() else (shutil.which("streamlink") or "streamlink"), "-o", str(raw_path), "--force",
                    clip["url"], "best"], check=True)

    spec = importlib.util.spec_from_file_location("clip_watcher", BASE_DIRECTORY / "clip_watcher.py")
    clip_watcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clip_watcher)
    clip_watcher.VIDEO_STYLE = "crop"
    vertical_path = CLIPS_DIRECTORY / f"{streamer_login}_{slug}_vertical.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_path), *clip_watcher.video_filter_arguments(),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", str(vertical_path)], check=True)

    final_path = CLIPS_DIRECTORY / f"{streamer_login}_{slug}_polished.mp4"
    subprocess.run([sys.executable, "-I", str(BASE_DIRECTORY / "polish_clip.py"), str(vertical_path),
                    "--punch-at", "0", "--hook", "", "--whisper-model", "small", "--out", str(final_path)],
                   check=True)

    title = clip["title"].strip()
    # Streamer credit is OFF by default (set CREDIT_STREAMER=1 to add the source line and the streamer's tag).
    credit_streamer = os.environ.get("CREDIT_STREAMER", "0") == "1"
    tags = ["#Shorts", "#GTA6", "#GTAVI", "#gaming", "#gameplay", "#funnymoments", "#streamerclips"]
    description_lines = [title, "", "🎮 The funniest and wildest GTA 6 moments, every hour.",
                         "👍 Like and subscribe so you don't miss the next one."]
    if credit_streamer:
        tags.insert(1, f"#{streamer_login}")
        description_lines += ["", f"Clip from {clip['broadcaster_name']}'s stream: {clip['url']}"]
    description_lines += ["", " ".join(tags)]
    post = {
        "video": final_path.name,
        "youtube_title": f"{title} #Shorts"[:100],
        "youtube_description": "\n".join(description_lines),
        "hashtags": tags,
        "notes": "Auto-drafted from the Twitch clip title.",
    }
    final_path.with_suffix(".post.json").write_text(json.dumps(post, indent=2, ensure_ascii=False))
    return final_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Find the top live streamer in a category and fetch their clips.")
    parser.add_argument("--game", default="Grand Theft Auto VI", help="Twitch category name to search")
    parser.add_argument("--allowlist", type=Path, help="file with one allowed streamer login per line")
    parser.add_argument("--clips", action="store_true", help="list the chosen streamer's top clips in the category")
    parser.add_argument("--days", type=int, default=7, help="how far back to look for clips")
    parser.add_argument("--download", type=int, default=0, help="download + polish this many top clips")
    arguments = parser.parse_args()

    credentials = load_credentials()
    token = app_access_token(credentials)
    category = find_category(arguments.game, credentials, token)

    streams = helix("streams", {"game_id": category["id"], "first": "20"}, credentials, token)  # sorted by viewers
    if arguments.allowlist:
        allowed = {line.strip().lower() for line in arguments.allowlist.read_text().splitlines() if line.strip()}
        streams = [stream for stream in streams if stream["user_login"].lower() in allowed]
    if not streams:
        print("No live streams match (nobody live in this category, or none on the allowlist).")
        return 1

    print(f"\nlive now in '{category['name']}' by viewers:")
    for stream in streams[:10]:
        print(f"  {stream['viewer_count']:>7,}  {stream['user_login']:<20} {stream['title'][:60]}")
    top = streams[0]
    print(f"\ntop streamer: {top['user_login']} ({top['viewer_count']:,} viewers)\n"
          f"record live clips with:  ./watch.sh {top['user_login']} --min 15")

    if arguments.clips or arguments.download:
        started_at = (datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(days=arguments.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # The API accepts only ONE of broadcaster_id / game_id, so filter by game ourselves.
        clips = helix("clips", {"broadcaster_id": top["user_id"], "started_at": started_at, "first": "100"},
                      credentials, token)
        clips = [clip for clip in clips if clip.get("game_id") == category["id"]]
        clips.sort(key=lambda clip: clip["view_count"], reverse=True)
        print(f"\ntop clips (last {arguments.days} days, {category['name']}):")
        for clip in clips[:10]:
            print(f"  {clip['view_count']:>7,} views  {clip['duration']:>4.0f}s  {clip['title'][:50]}  {clip['url']}")
        for clip in clips[:arguments.download]:
            print(f"\nprocessing {clip['url']} ...")
            print(f"-> {download_and_polish(clip, top['user_login'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
