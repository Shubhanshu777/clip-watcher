#!/usr/bin/env bash
# clip-watcher launcher: ./watch.sh <twitch_channel> [--min N] [--ratio X] [--pre S] [--post S] [--whisper-model M]
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer the project's virtualenv (where streamlink + whisper are installed).
if [[ -d "${SCRIPT_DIRECTORY}/.venv/bin" ]]; then
  export PATH="${SCRIPT_DIRECTORY}/.venv/bin:${PATH}"
fi

for required_tool in ffmpeg streamlink whisper python3; do
  if ! command -v "${required_tool}" >/dev/null 2>&1; then
    echo "Missing dependency: ${required_tool}" >&2
    echo "Install: brew install ffmpeg && pip install streamlink openai-whisper" >&2
    exit 1
  fi
done

if [[ $# -lt 1 || "$1" == -* ]]; then
  echo "Usage: $0 <twitch_channel> [--min N] [--ratio X] [--pre S] [--post S] [--whisper-model M]" >&2
  exit 2
fi

cd "${SCRIPT_DIRECTORY}"
exec python3 -I "${SCRIPT_DIRECTORY}/clip_watcher.py" "$@"
