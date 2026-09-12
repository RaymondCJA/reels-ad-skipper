#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"

exec "$project_dir/.venv/bin/python" -m reels_skipper.app \
  --config "$project_dir/config.json" --run "$@"
