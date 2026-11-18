#!/usr/bin/env python3
"""clip-watcher: record a live Twitch stream, detect chat spikes, cut vertical clips, transcribe them."""

import argparse
import collections
import datetime
import queue
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Deque, List, Optional, Tuple

BASE_DIRECTORY: Path = Path(__file__).resolve().parent
RECORDINGS_DIRECTORY: Path = BASE_DIRECTORY / "rec"
CLIPS_DIRECTORY: Path = BASE_DIRECTORY / "clips"

IRC_HOST: str = "irc.chat.twitch.tv"
IRC_PORT: int = 6667
IRC_NICKNAME: str = "justinfan12345"

BUCKET_SECONDS: int = 10
BASELINE_WINDOW_SECONDS: int = 180  # median of the last 3 minutes
BASELINE_BUCKET_COUNT: int = BASELINE_WINDOW_SECONDS // BUCKET_SECONDS
MIN_BASELINE_BUCKETS: int = 6  # don't judge spikes until we have at least 1 minute of history
SPIKE_COOLDOWN_SECONDS: int = 30  # avoid emitting overlapping clips for the same moment


def log(message: str) -> None:
    print(f"[{datetime.datetime.now():%H:%M:%S}] {message}", flush=True)


def chat_reader(channel: str, message_timestamps: "queue.Queue[float]", stop_event: threading.Event) -> None:
    """Connect anonymously to Twitch IRC and push one wall-clock timestamp per chat message."""
    while not stop_event.is_set():
        try:
            with socket.create_connection((IRC_HOST, IRC_PORT), timeout=30) as irc_socket:
                irc_socket.settimeout(60)
                irc_socket.sendall(f"NICK {IRC_NICKNAME}\r\n".encode())
                irc_socket.sendall(f"JOIN #{channel.lower()}\r\n".encode())
                log(f"chat: joined #{channel.lower()} anonymously")
                pending_buffer: str = ""
                while not stop_event.is_set():
                    data = irc_socket.recv(4096)
                    if not data:
                        raise ConnectionError("IRC connection closed")
                    pending_buffer += data.decode("utf-8", errors="replace")
                    *complete_lines, pending_buffer = pending_buffer.split("\r\n")
                    for line in complete_lines:
                        if line.startswith("PING"):
                            irc_socket.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif " PRIVMSG " in line:
                            message_timestamps.put(time.time())
        except (OSError, ConnectionError) as error:
            if stop_event.is_set():
                return
            log(f"chat: connection problem ({error}); reconnecting in 5s")
            time.sleep(5)


VIDEO_STYLE: str = "crop"  # "crop", "blur" or "stack"; set from --style in main()
FOCUS: float = 0.5  # horizontal crop position: 0 = far left, 0.5 = center, 1 = far right
FACECAM_BOX: str = "0:540:368:295"  # x:y:w:h of the streamer's facecam in the 1920x1080 source (--facecam)


def video_filter_arguments() -> List[str]:
    """Return the ffmpeg filter arguments that produce a 1080x1920 (9:16) clip."""
    if VIDEO_STYLE == "blur":
        # Whole 16:9 frame sits in the middle over a blurred, zoomed copy of itself (nothing is cropped away).
        filter_graph: str = (
            "[0:v]split=2[background][foreground];"
            "[background]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[blurred];"
            "[foreground]scale=1080:-2[sharp];"
            "[blurred][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1[out]"
        )
        return ["-filter_complex", filter_graph, "-map", "[out]", "-map", "0:a?"]
    if VIDEO_STYLE == "stack":
        # Gameplay (4:5 crop, zoomed) fills the top 1350px; the facecam sits in the bottom 570px.
        facecam_x, facecam_y, facecam_w, facecam_h = FACECAM_BOX.split(":")
        filter_graph = (
            "[0:v]split=3[game][face][backdrop];"
            f"[game]crop=trunc(ih*4/5/2)*2:ih:(iw-out_w)*{FOCUS}:0,scale=1080:1350:flags=lanczos[top];"
            f"[face]crop={facecam_w}:{facecam_h}:{facecam_x}:{facecam_y},scale=-2:570:flags=lanczos[cam];"
            "[backdrop]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:570,boxblur=30:5[bottomfill];"
            "[bottomfill][cam]overlay=(W-w)/2:0[bottom];"
            "[top][bottom]vstack,setsar=1[out]"
        )
        return ["-filter_complex", filter_graph, "-map", "[out]", "-map", "0:a?"]
    # Center-crop to 9:16 (width = height * 9/16), then scale up to 1080x1920.
    return ["-vf", f"crop=trunc(ih*9/16/2)*2:ih:(iw-out_w)*{FOCUS}:0,scale=1080:1920:flags=lanczos,setsar=1"]


def cut_and_transcribe(
    recording_path: Path,
    recording_start_epoch: float,
    spike_epoch: float,
    pre_seconds: int,
    post_seconds: int,
    whisper_model: str,
    channel: str,
) -> None:
    """Wait for the post-roll to be recorded, cut a 9:16 center-cropped clip, then write an .srt."""
    clip_start_offset: float = max(0.0, spike_epoch - recording_start_epoch - pre_seconds)
    clip_duration: int = pre_seconds + post_seconds

    # Wait until the recording actually contains the post-roll (plus a little safety margin).
    wait_seconds: float = (spike_epoch + post_seconds + 3) - time.time()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    timestamp_label: str = datetime.datetime.fromtimestamp(spike_epoch).strftime("%Y%m%d_%H%M%S")
    clip_path: Path = CLIPS_DIRECTORY / f"{channel}_{timestamp_label}.mp4"

    ffmpeg_command: List[str] = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{clip_start_offset:.2f}", "-i", str(recording_path),
        "-t", str(clip_duration),
        *video_filter_arguments(),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(clip_path),
    ]
    log(f"clip: cutting {clip_path.name} (start offset {clip_start_offset:.0f}s, {clip_duration}s long)")
    ffmpeg_result = subprocess.run(ffmpeg_command, capture_output=True, text=True)
    if ffmpeg_result.returncode != 0 or not clip_path.exists():
        log(f"clip: ffmpeg failed: {ffmpeg_result.stderr.strip()[-300:]}")
        return

    whisper_command: List[str] = [
        "whisper", str(clip_path),
        "--model", whisper_model,
        "--output_format", "srt",
        "--output_dir", str(CLIPS_DIRECTORY),
        "--fp16", "False",
    ]
    log(f"clip: transcribing {clip_path.name} with whisper ({whisper_model})")
    whisper_result = subprocess.run(whisper_command, capture_output=True, text=True)
    if whisper_result.returncode != 0:
        log(f"clip: whisper failed: {whisper_result.stderr.strip()[-300:]}")
        return
    log(f"clip: done -> {clip_path.with_suffix('.srt').name}")


def main() -> int:
    global VIDEO_STYLE, FOCUS, FACECAM_BOX
    parser = argparse.ArgumentParser(description="Record a live Twitch stream and clip chat spikes.")
    parser.add_argument("channel", help="Twitch channel name")
    parser.add_argument("--min", type=int, default=15, dest="minimum_messages",
                        help="minimum messages in a 10s bucket to count as a spike (use 5 for quiet chats)")
    parser.add_argument("--ratio", type=float, default=2.5, help="spike = ratio x median of last 3 min")
    parser.add_argument("--pre", type=int, default=20, help="seconds before spike")
    parser.add_argument("--post", type=int, default=10, help="seconds after spike")
    parser.add_argument("--quality", default="1080p60,1080p,best",
                        help="streamlink quality, tried in order (default: Full HD 60fps, then 1080p, then best available)")
    parser.add_argument("--whisper-model", default="base", help="whisper model name")
    parser.add_argument("--max-minutes", type=float, default=0, help="stop recording after this many minutes (0 = until the stream ends)")
    parser.add_argument("--style", choices=["crop", "blur", "stack"], default="crop",
                        help="crop = zoomed 9:16; blur = full frame over blur; stack = zoomed gameplay on top + facecam below")
    parser.add_argument("--focus", type=float, default=0.5, help="crop position 0 (left) .. 1 (right)")
    parser.add_argument("--facecam", default=FACECAM_BOX, help="facecam box x:y:w:h in the 1920x1080 source (stack style)")
    arguments = parser.parse_args()
    VIDEO_STYLE, FOCUS, FACECAM_BOX = arguments.style, min(1.0, max(0.0, arguments.focus)), arguments.facecam

    channel: str = arguments.channel.lower().lstrip("#")
    RECORDINGS_DIRECTORY.mkdir(exist_ok=True)
    CLIPS_DIRECTORY.mkdir(exist_ok=True)

    recording_path: Path = RECORDINGS_DIRECTORY / f"{channel}_{datetime.datetime.now():%Y%m%d_%H%M%S}.ts"
    log(f"recording {channel} -> {recording_path}")
    streamlink_process = subprocess.Popen(
        ["streamlink", "--twitch-disable-ads", "-o", str(recording_path),
         f"https://twitch.tv/{channel}", arguments.quality],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )

    # Give streamlink a moment; if it exits at once the channel is offline / unavailable.
    time.sleep(5)
    if streamlink_process.poll() is not None:
        error_output = streamlink_process.stderr.read() if streamlink_process.stderr else ""
        log(f"streamlink exited ({streamlink_process.returncode}); is {channel} live? {error_output.strip()[-300:]}")
        return 1
    # Approximation: the recording's t=0 is when we started pulling the stream.
    recording_start_epoch: float = time.time() - 5

    message_timestamps: "queue.Queue[float]" = queue.Queue()
    stop_event = threading.Event()
    threading.Thread(target=chat_reader, args=(channel, message_timestamps, stop_event), daemon=True).start()

    bucket_counts_history: Deque[int] = collections.deque(maxlen=BASELINE_BUCKET_COUNT)
    current_bucket_count: int = 0
    next_bucket_end: float = time.time() + BUCKET_SECONDS
    last_spike_epoch: float = 0.0
    clip_threads: List[threading.Thread] = []
    session_deadline: float = time.time() + arguments.max_minutes * 60 if arguments.max_minutes > 0 else float("inf")

    log(f"watching chat: spike = >= {arguments.ratio}x median(last 3min) and >= {arguments.minimum_messages} msgs/10s")
    try:
        while streamlink_process.poll() is None and time.time() < session_deadline:
            # Drain whatever chat arrived; sleeping on the queue keeps the loop responsive to bucket ends.
            try:
                message_timestamps.get(timeout=max(0.0, next_bucket_end - time.time()))
                current_bucket_count += 1
                continue
            except queue.Empty:
                pass

            if time.time() < next_bucket_end:
                continue

            bucket_end_epoch: float = next_bucket_end
            next_bucket_end += BUCKET_SECONDS

            baseline_median: float = (
                statistics.median(bucket_counts_history)
                if len(bucket_counts_history) >= MIN_BASELINE_BUCKETS else 0.0
            )
            # Floor the median at 1 so a totally silent chat doesn't make any message a "spike" by itself;
            # --min is the real guard in that case.
            threshold: float = arguments.ratio * max(baseline_median, 1.0)
            is_spike: bool = (
                len(bucket_counts_history) >= MIN_BASELINE_BUCKETS
                and current_bucket_count >= threshold
                and current_bucket_count >= arguments.minimum_messages
            )
            log(f"bucket: {current_bucket_count:3d} msgs (median {baseline_median:.1f}, threshold {threshold:.1f})")

            if is_spike and bucket_end_epoch - last_spike_epoch >= SPIKE_COOLDOWN_SECONDS:
                last_spike_epoch = bucket_end_epoch
                log(f"SPIKE: {current_bucket_count} msgs in 10s")
                clip_thread = threading.Thread(
                    target=cut_and_transcribe,
                    args=(recording_path, recording_start_epoch, bucket_end_epoch,
                          arguments.pre, arguments.post, arguments.whisper_model, channel),
                )
                clip_thread.start()
                clip_threads.append(clip_thread)

            bucket_counts_history.append(current_bucket_count)
            current_bucket_count = 0
        log("session finished (stream ended or time limit reached)")
    except KeyboardInterrupt:
        log("stopping...")
    finally:
        stop_event.set()
        if streamlink_process.poll() is None:
            streamlink_process.terminate()
        for clip_thread in clip_threads:
            clip_thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
