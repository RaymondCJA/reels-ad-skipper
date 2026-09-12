#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
arduino_dir="$project_dir/.arduino"
config_file="$arduino_dir/arduino-cli.yaml"

mkdir -p "$arduino_dir"
if [[ ! -f "$config_file" ]]; then
  arduino-cli config init --dest-dir "$arduino_dir"
fi

arduino-cli config set directories.data "$arduino_dir/data" --config-file "$config_file"
arduino-cli config set directories.downloads "$arduino_dir/downloads" --config-file "$config_file"
arduino-cli config set directories.user "$arduino_dir/user" --config-file "$config_file"
arduino-cli config set board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json \
  --config-file "$config_file"
arduino-cli core update-index --config-file "$config_file"
arduino-cli core install esp32:esp32 --config-file "$config_file"
arduino-cli lib update-index --config-file "$config_file"
arduino-cli lib install "NimBLE-Arduino" --config-file "$config_file"
arduino-cli lib install "HijelHID_BLEMouse" --config-file "$config_file"

echo "Arduino toolchain ready at $arduino_dir"
