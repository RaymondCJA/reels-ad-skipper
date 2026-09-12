#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
task_cache="$project_dir/.swift-cache"
mkdir -p "$task_cache/clang" "$task_cache/swiftpm"
export CLANG_MODULE_CACHE_PATH="$task_cache/clang"
export SWIFTPM_MODULECACHE_OVERRIDE="$task_cache/clang"
swift build --package-path "$project_dir/capture-helper" -c release \
  --disable-sandbox \
  --cache-path "$task_cache/swiftpm" \
  --scratch-path "$project_dir/capture-helper/.build"
echo "Built $project_dir/capture-helper/.build/release/iphone-capture"
