#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
config_file="$project_dir/.arduino/arduino-cli.yaml"
sketch="$project_dir/firmware/reels_hid"
port="${1:-/dev/cu.usbserial-0001}"
build_dir="$project_dir/.arduino/build"

arduino-cli compile --fqbn esp32:esp32:esp32 --config-file "$config_file" \
  --build-path "$build_dir" "$sketch"
arduino-cli upload --fqbn esp32:esp32:esp32 --config-file "$config_file" \
  --port "$port" --input-dir "$build_dir" "$sketch"
