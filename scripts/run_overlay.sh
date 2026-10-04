#!/usr/bin/env bash
# Launch the Merge AI overlay. Prefers system xdotool/wmctrl; falls back to
# project-local binaries under .local/ (see README) when apt install isn't available.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -d "$ROOT/.local/bin" ]]; then
  export PATH="$ROOT/.local/bin:$PATH"
fi
if [[ -d "$ROOT/.local/lib" ]]; then
  export LD_LIBRARY_PATH="$ROOT/.local/lib:${LD_LIBRARY_PATH:-}"
fi

exec python3 main.py overlay "$@"
