#!/usr/bin/env bash
# Put YOUR OWN talking-head video under a clip (reaction / commentary layout).
# Usage: ./add_my_face.sh <clip.mp4> <my_video.mp4> [output.mp4] [game_volume 0..1]
#   clip.mp4      a clip made by watch.sh (1080x1920, gameplay only: --style crop)
#   my_video.mp4  you talking, recorded while watching the clip (any size; cropped to fill the bottom panel)
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <clip.mp4> <my_video.mp4> [output.mp4] [game_volume 0..1]" >&2
  exit 2
fi

CLIP_PATH="$1"
MY_VIDEO_PATH="$2"
OUTPUT_PATH="${3:-${CLIP_PATH%.mp4}_with_me.mp4}"
GAME_VOLUME="${4:-0.35}"   # game audio is turned down so your voice stays clear

CLIP_DURATION_SECONDS="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "${CLIP_PATH}")"

# Layout (1080x1920): top 1350px = gameplay, bottom 570px = you (cropped to fill, no bars).
case "$(printf "%s" "${MY_VIDEO_PATH}" | tr "[:upper:]" "[:lower:]")" in
  *.jpg|*.jpeg|*.png|*.heic)
    # A photo of you: shown for the whole clip with a slow, subtle zoom-in so the panel isn't dead.
    # No voice track, so the game audio stays at full volume.
    ffmpeg -y -loglevel error \
      -i "${CLIP_PATH}" -loop 1 -framerate 60 -i "${MY_VIDEO_PATH}" \
      -filter_complex "
        [0:v]crop=1080:1350:0:(ih-1350)/2,setsar=1[gameplay];
        [1:v]scale=1620:-2:flags=lanczos,crop=1620:855:0:(ih-855)*0.6,
             zoompan=z='min(1.0+0.0004*on,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x570:fps=60,
             setsar=1[me];
        [gameplay][me]vstack,format=yuv420p[video]" \
      -map "[video]" -map 0:a -t "${CLIP_DURATION_SECONDS}" \
      -c:v libx264 -preset veryfast -crf 20 -c:a copy -movflags +faststart "${OUTPUT_PATH}"
    ;;
  *)
    # If your video is shorter than the clip, its last frame is held; it is cut at the clip length.
    ffmpeg -y -loglevel error \
      -i "${CLIP_PATH}" -i "${MY_VIDEO_PATH}" \
      -filter_complex "
        [0:v]crop=1080:1350:0:(ih-1350)/2,setsar=1[gameplay];
        [1:v]scale=1080:570:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:570,
             tpad=stop_mode=clone:stop_duration=60,setsar=1[me];
        [gameplay][me]vstack[video];
        [0:a]volume=${GAME_VOLUME}[game_audio];
        [1:a]volume=1.0,apad[my_audio];
        [game_audio][my_audio]amix=inputs=2:duration=first:normalize=0[mixed_audio]" \
      -map "[video]" -map "[mixed_audio]" -t "${CLIP_DURATION_SECONDS}" \
      -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart \
      "${OUTPUT_PATH}"
    ;;
esac

echo "Created ${OUTPUT_PATH}"
