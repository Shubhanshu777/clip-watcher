# clip-watcher

Turns Twitch gameplay clips into polished 9:16 YouTube Shorts.

- `watch.sh <channel>`: records a live stream, detects chat spikes, cuts vertical clips (runs on your machine).
- `polish_clip.py`: zoom/punch-in, word-by-word captions, hook title, progress bar, -14 LUFS audio.
- `top_streamer.py`: lists the top live streamers in a Twitch category and fetches their clips.
- `post_to_youtube.py`: uploads a video to your channel (asks for confirmation unless `--yes`).
- `autopost.py` + `.github/workflows/hourly-short.yml`: **hourly, fully automatic** Short from an allowlisted streamer's best unposted GTA VI clip.

## Set up the hourly GitHub Actions job

1. Push this repo to **your own** GitHub account. `.gitignore` already keeps secrets, recordings and clips out.
2. **Allowlist** (`allowlist.txt`): add Twitch logins of streamers who allow their clips to be reposted. The job refuses to run while it is empty.
3. **Repo secrets** (Settings → Secrets and variables → Actions → Secrets):
   - `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`: from https://dev.twitch.tv/console (Confidential app).
   - `YT_CLIENT_ID`, `YT_CLIENT_SECRET`: your Google Cloud OAuth *Desktop app* client.
   - `YT_REFRESH_TOKEN`: run `python3 post_to_youtube.py <any.mp4> --meta <x.post.json>` once locally, sign in
     in the browser, then copy `refresh_token` out of `~/.clip-watcher/youtube_token.json`.
4. **Repo variables** (same page, Variables tab, all optional):
   `YT_PRIVACY` (`public` by default; set `private` to test), `MAX_PER_DAY` (6), `DRY_RUN` (`1` = do everything except upload).
5. Actions tab → hourly-short → **Run workflow** once by hand. Try it with `DRY_RUN=1` first.

## Limits to know about

- YouTube's default API quota allows about **6 uploads per day**, so the job stops at `MAX_PER_DAY`.
- Until Google verifies your OAuth project, API uploads are forced **private**, and in "Testing" mode the
  refresh token expires after **7 days** (re-run the local sign-in and update the secret).
- Scheduled runs are best-effort: GitHub can delay them by several minutes, and pauses them after 60 days
  without repo activity (the state commit counts as activity).
- Reposting other people's streams can draw copyright claims or strikes. Only list streamers who agree.
- Not yet tested end to end: the real upload, and a full run on a GitHub runner.
