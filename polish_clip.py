#!/usr/bin/env python3
"""polish_clip: turn a plain 9:16 gameplay clip into an engaging short.

Adds: slow zoom + punch-in on the spike moment, word-by-word pop-in captions (current word in yellow),
a hook title at the start, a progress bar, light color grade, and -14 LUFS loudness.
No face, no facecam: gameplay only.

Usage: polish_clip.py <clip.mp4> [--hook "WAIT FOR IT"] [--punch-at 20] [--out out.mp4]
Needs: ffmpeg, the whisper CLI (from the project's .venv) and bin/render_text (built from render_text.swift).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

BASE_DIRECTORY: Path = Path(__file__).resolve().parent
RENDERER_PATH: Path = BASE_DIRECTORY / "bin" / "render_text"
WHISPER_PATH: str = str(BASE_DIRECTORY / ".venv" / "bin" / "whisper") if (BASE_DIRECTORY / ".venv" / "bin" / "whisper").exists() else (shutil.which("whisper") or "whisper")

CAPTION_CANVAS_WIDTH: int = 1080
CAPTION_CANVAS_HEIGHT: int = 260
CAPTION_TOP_Y: int = 1230  # inside the Shorts/Reels safe zone (above the bottom UI)
MAX_WORDS_PER_CAPTION: int = 3
POP_SCALES: List[float] = [0.88, 1.07]  # first two frames of each word, then settles at 1.0
POP_FRAME_SECONDS: List[float] = [0.04, 0.05]


def run(command: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def probe_duration_seconds(clip_path: Path) -> float:
    output = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip_path)],
                 capture_output=True).stdout
    return float(output.strip())


def transcribe_words(clip_path: Path, working_directory: Path, model: str, language: str) -> List[Dict]:
    """Run Whisper with word-level timestamps; return [{'word', 'start', 'end'}, ...]."""
    run([WHISPER_PATH, str(clip_path), "--model", model, "--language", language,
         "--word_timestamps", "True", "--output_format", "json",
         "--output_dir", str(working_directory), "--fp16", "False"], capture_output=True)
    transcript = json.loads((working_directory / f"{clip_path.stem}.json").read_text())
    words: List[Dict] = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words", []):
            cleaned = word["word"].strip().strip(".,;:\"()").upper()
            if cleaned:
                words.append({"word": cleaned, "start": float(word["start"]), "end": float(word["end"])})
    return words


def group_into_captions(words: List[Dict]) -> List[List[Dict]]:
    """Group words into short on-screen phrases: <= 3 words, break at sentence ends and pauses."""
    captions: List[List[Dict]] = []
    current: List[Dict] = []
    for index, word in enumerate(words):
        current.append(word)
        next_word = words[index + 1] if index + 1 < len(words) else None
        sentence_end = word["word"].endswith(("?", "!")) or word["word"] == "."
        long_pause = next_word is not None and next_word["start"] - word["end"] > 0.5
        if len(current) >= MAX_WORDS_PER_CAPTION or sentence_end or long_pause or next_word is None:
            captions.append(current)
            current = []
    return captions


def build_caption_timeline(captions: List[List[Dict]], total_duration: float, image_directory: Path):
    """Return (render_items, timeline) where timeline is [(png_path, seconds), ...] covering the whole clip."""
    render_items: List[Dict] = []
    timeline: List = []

    blank_path = image_directory / "blank.png"
    render_items.append({"out": str(blank_path), "words": [], "highlight": -1, "size": 100,
                         "width": CAPTION_CANVAS_WIDTH, "height": CAPTION_CANVAS_HEIGHT, "scale": 1.0})

    cursor_seconds = 0.0
    for caption_index, caption in enumerate(captions):
        caption_words = [word["word"] for word in caption]
        next_caption_start = captions[caption_index + 1][0]["start"] if caption_index + 1 < len(captions) else total_duration
        for word_index, word in enumerate(caption):
            word_start = max(word["start"], cursor_seconds)
            if word_index + 1 < len(caption):
                word_end = caption[word_index + 1]["start"]
            else:
                word_end = min(word["end"] + 0.2, next_caption_start)
            if word_end - word_start < 0.12:
                word_end = word_start + 0.12
            if word_start > cursor_seconds:
                timeline.append((blank_path, word_start - cursor_seconds))

            duration = word_end - word_start
            frame_scales = POP_SCALES + [1.0]
            frame_seconds = POP_FRAME_SECONDS + [max(0.04, duration - sum(POP_FRAME_SECONDS))]
            for frame_number, (scale, seconds) in enumerate(zip(frame_scales, frame_seconds)):
                frame_path = image_directory / f"c{caption_index:03d}_w{word_index}_f{frame_number}.png"
                render_items.append({"out": str(frame_path), "words": caption_words, "highlight": word_index,
                                     "size": 118, "width": CAPTION_CANVAS_WIDTH,
                                     "height": CAPTION_CANVAS_HEIGHT, "scale": scale})
                timeline.append((frame_path, seconds))
            cursor_seconds = word_start + sum(frame_seconds)

    if cursor_seconds < total_duration:
        timeline.append((blank_path, total_duration - cursor_seconds))
    return render_items, timeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Add captions, zoom effects and a hook to a 9:16 clip.")
    parser.add_argument("clip", type=Path)
    parser.add_argument("--hook", default="WAIT FOR IT", help="title shown for the first ~2.5 seconds ('' to disable)")
    parser.add_argument("--punch-at", type=float, default=20.0,
                        help="second in the clip where the spike happens (watch.sh default --pre is 20)")
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", type=Path)
    arguments = parser.parse_args()

    clip_path: Path = arguments.clip.resolve()
    output_path: Path = arguments.out or clip_path.with_name(clip_path.stem + "_polished.mp4")
    total_duration: float = probe_duration_seconds(clip_path)

    with tempfile.TemporaryDirectory(prefix="polish_") as temporary_name:
        working_directory = Path(temporary_name)
        print("transcribing (word timestamps)...", flush=True)
        words = transcribe_words(clip_path, working_directory, arguments.whisper_model, arguments.language)
        captions = group_into_captions(words)
        print(f"{len(words)} words -> {len(captions)} caption groups", flush=True)

        render_items, timeline = build_caption_timeline(captions, total_duration, working_directory)
        if arguments.hook:
            render_items.append({"out": str(working_directory / "hook.png"), "words": arguments.hook.upper().split(),
                                 "highlight": -2, "size": 150, "width": 1080, "height": 300, "scale": 1.0})
        # macOS: native Swift renderer. Linux/CI (or no binary): Pillow renderer with the same JSON interface.
        renderer_command = ([str(RENDERER_PATH)] if RENDERER_PATH.exists()
                            else [sys.executable, str(BASE_DIRECTORY / "render_text.py")])
        run(renderer_command, input=json.dumps(render_items), capture_output=True)

        # ffmpeg concat list: one entry per image with its on-screen duration (repeat the last file as required).
        concat_lines: List[str] = []
        for image_path, seconds in timeline:
            concat_lines += [f"file '{image_path}'", f"duration {seconds:.4f}"]
        concat_lines.append(f"file '{timeline[-1][0]}'")
        (working_directory / "captions.txt").write_text("\n".join(concat_lines) + "\n")

        punch = arguments.punch_at
        # Slow push-in over the whole clip, plus a quick "punch" zoom that eases out right after the spike moment.
        zoom_expression = (f"1+0.10*t/{total_duration:.3f}"
                           f"+if(gte(t,{punch}),0.07*exp(-7*(t-{punch})),0)")
        filter_graph = (
            f"[0:v]scale=w='trunc(1080*({zoom_expression})/2)*2':h=-2:eval=frame:flags=bilinear,"
            "crop=1080:1920,eq=contrast=1.07:saturation=1.18,fade=t=in:d=0.25,setsar=1,format=yuv420p[base];"
            "[1:v]fps=60,format=rgba[captions];"
            f"[base][captions]overlay=0:{CAPTION_TOP_Y}:eof_action=pass[with_captions];"
        )
        inputs: List[str] = ["-i", str(clip_path), "-f", "concat", "-safe", "0", "-i", str(working_directory / "captions.txt")]
        last_label = "with_captions"
        input_index = 2
        if arguments.hook:
            inputs += ["-loop", "1", "-t", "3", "-i", str(working_directory / "hook.png")]
            filter_graph += (
                f"[{input_index}:v]format=rgba,fade=t=in:st=0.1:d=0.2:alpha=1,fade=t=out:st=2.3:d=0.3:alpha=1[hook];"
                f"[{last_label}][hook]overlay=0:330:shortest=0:eof_action=pass[with_hook];"
            )
            last_label = "with_hook"
            input_index += 1
        inputs += ["-f", "lavfi", "-i", "color=c=0xFFD21A:s=1080x14:r=60"]
        filter_graph += (
            f"[{last_label}][{input_index}:v]overlay=x='-w+w*t/{total_duration:.3f}':y=0:eof_action=pass[video]"
        )

        command = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
                   "-filter_complex", filter_graph,
                   "-map", "[video]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                   "-t", f"{total_duration:.3f}", "-r", "60",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path)]
        print("rendering...", flush=True)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr[-1500:], file=sys.stderr)
            return 1
    print(f"Created {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
