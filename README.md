# Reels Ad Skipper

Automatically skips Instagram Reels ads on an iPhone. The Mac polls iOS still
screenshots, OpenCV detects the small **Ad** label, and an ESP32 paired as a BLE
mouse performs a quick upward drag.

This version does not use QuickTime or continuous screen mirroring. Audio stays
on the iPhone and the Mac does not need to display the phone screen.

## Everyday use

1. Connect the iPhone and ESP32 to the Mac with data-capable USB cables.
2. Unlock the iPhone and open Instagram Reels.
3. Confirm **Reels Skipper** is connected in Settings -> Bluetooth.
4. Confirm AssistiveTouch is on in Settings -> Accessibility -> Touch ->
   AssistiveTouch.
5. Double-click **Start Reels Ad Skipper.command** in this folder.

Alternatively, start it from Terminal:

```bash
cd ~/repos/reels-ad-skipper
./Start\ Reels\ Ad\ Skipper.command
```

Keep the Terminal window open. Press `Control-C` to stop the skipper.

## One-time setup

### iPhone

1. Connect the iPhone by USB and choose **Trust** if prompted.
2. Enable Developer Mode under Settings -> Privacy & Security -> Developer Mode.
3. Enable AssistiveTouch under Settings -> Accessibility -> Touch.
4. Pair **Reels Skipper** under Settings -> Bluetooth.

### Mac detector

```bash
cd ~/repos/reels-ad-skipper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
```

Set `device_udid` in `config.json` to the iPhone identifier reported by:

```bash
.venv/bin/python -m pymobiledevice3 usbmux list
```

The current phone and detection region are already configured in the local
`config.json`.

### ESP32 firmware

The tested board is the Keyestudio XC3800 / ESP-WROOM-32. Install the Arduino
toolchain and flash it with:

```bash
./scripts/setup_arduino.sh
./scripts/flash.sh
```

After changing firmware, reconnect **Reels Skipper** in the iPhone Bluetooth
settings. The serial commands are `PING`, `STATUS`, `WHEEL`, and `SWIPE`.

## Calibration and testing

The current configuration uses a 450 x 970 screenshot and an ROI around the
bottom-right **Ad** label. Every PNG in `templates/` is loaded, allowing
slightly different label sizes such as the standard and small Instagram
variants. To recalibrate or add another variant:

```bash
.venv/bin/python -m reels_skipper.app --config config.json --select-roi
.venv/bin/python -m reels_skipper.app --config config.json --capture-template ad
```

Test detection without scrolling:

```bash
.venv/bin/python -m reels_skipper.app --config config.json --run --dry-run --preview
```

Test the ESP32 drag directly:

```bash
.venv/bin/python -m reels_skipper.app --config config.json --test-serial SWIPE
```

`match_threshold` controls detection sensitivity. Raise it if ordinary Reels
trigger false skips; lower it cautiously if genuine ads are missed.

## Troubleshooting

- **No phone audio:** stop QuickTime or any other live iPhone capture. This
  project must use `"capture_source": "screenshot"` in `config.json`.
- **Serial port error:** reconnect the ESP32 and check its `/dev/cu.usbserial-*`
  name in `config.json`.
- **No scrolling:** reconnect **Reels Skipper** in Bluetooth and ensure
  AssistiveTouch remains enabled.
- **No screenshots:** unlock and trust the iPhone, keep Developer Mode enabled,
  then reconnect its USB cable.
- **Stop the program:** focus the Terminal window and press `Control-C`.

## Project layout

- `reels_skipper/app.py` - screenshot polling, detection, and serial trigger
- `firmware/reels_hid/reels_hid.ino` - ESP32 BLE mouse firmware
- `config.json` - this Mac/iPhone's live configuration
- `templates/ad.png` - calibrated ad-label template
- `scripts/flash.sh` - compile and flash the ESP32
- `scripts/run.sh` - underlying headless launcher
