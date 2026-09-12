#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir"

echo "Starting Reels Ad Skipper..."
echo "Keep this window open. Press Control-C to stop."
exec ./scripts/run.sh
