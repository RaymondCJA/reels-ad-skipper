from __future__ import annotations

import argparse
import asyncio
import json
import logging
import struct
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from pathlib import Path

import cv2
import mss
import numpy as np
import serial

try:
    import Quartz
except ImportError:  # pragma: no cover - only relevant away from macOS
    Quartz = None

LOG = logging.getLogger("reels-skipper")


@dataclass(frozen=True)
class Config:
    monitor: int
    capture_source: str
    capture_helper: Path
    capture_width: int
    capture_height: int
    capture_fps: float
    device_udid: str | None
    window_owner: str
    window_title: str
    roi: tuple[int, int, int, int]
    templates_dir: Path
    match_threshold: float
    confirmations_required: int
    cooldown_seconds: float
    sample_interval_seconds: float
    serial_port: str
    serial_baud: int
    trigger_command: str

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = json.loads(path.read_text())
        base = path.parent
        templates = Path(raw["templates_dir"])
        if not templates.is_absolute():
            templates = base / templates
        helper = Path(raw.get("capture_helper", "capture-helper/.build/release/iphone-capture"))
        if not helper.is_absolute():
            helper = base / helper
        return cls(
            monitor=int(raw["monitor"]),
            capture_source=str(raw.get("capture_source", "screen")),
            capture_helper=helper,
            capture_width=int(raw.get("capture_width", 450)),
            capture_height=int(raw.get("capture_height", 970)),
            capture_fps=float(raw.get("capture_fps", 4.0)),
            device_udid=raw.get("device_udid"),
            window_owner=str(raw.get("window_owner", "QuickTime Player")),
            window_title=str(raw.get("window_title", "Movie Recording")),
            roi=tuple(int(v) for v in raw["roi"]),
            templates_dir=templates,
            match_threshold=float(raw["match_threshold"]),
            confirmations_required=int(raw["confirmations_required"]),
            cooldown_seconds=float(raw["cooldown_seconds"]),
            sample_interval_seconds=float(raw["sample_interval_seconds"]),
            serial_port=str(raw["serial_port"]),
            serial_baud=int(raw["serial_baud"]),
            trigger_command=str(raw["trigger_command"]).upper(),
        )


def grab_monitor(capture: mss.MSS, monitor_index: int) -> np.ndarray:
    if monitor_index < 1 or monitor_index >= len(capture.monitors):
        raise ValueError(
            f"monitor must be 1..{len(capture.monitors) - 1}; got {monitor_index}"
        )
    return np.asarray(capture.grab(capture.monitors[monitor_index]))[:, :, :3]


def find_window_id(owner: str, title: str) -> int:
    if Quartz is None:
        raise RuntimeError("QuickTime capture requires pyobjc-framework-Quartz on macOS")
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    for window in windows:
        if (
            window.get(Quartz.kCGWindowOwnerName) == owner
            and window.get(Quartz.kCGWindowName) == title
            and window.get(Quartz.kCGWindowLayer) == 0
        ):
            return int(window[Quartz.kCGWindowNumber])
    raise RuntimeError(
        f"Could not find {owner!r} window {title!r}. Open New Movie Recording "
        "and select the iPhone screen; do not minimise the window."
    )


def grab_window(owner: str, title: str) -> np.ndarray:
    assert Quartz is not None
    window_id = find_window_id(owner, title)
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise RuntimeError("macOS refused the QuickTime window capture")
    width = Quartz.CGImageGetWidth(image)
    height = Quartz.CGImageGetHeight(image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(image)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
    rows = np.frombuffer(data, dtype=np.uint8).reshape(height, bytes_per_row)
    # Quartz supplies BGRA bytes on the currently supported macOS formats.
    return rows[:, : width * 4].reshape(height, width, 4)[:, :, :3].copy()


class AVFoundationCapture:
    """Read length-prefixed BGRA frames from the native USB capture helper."""

    def __init__(self, config: Config) -> None:
        if not config.capture_helper.is_file():
            raise RuntimeError(
                f"Capture helper not found at {config.capture_helper}. "
                "Run scripts/build_capture_helper.sh first."
            )
        self.process = subprocess.Popen(
            [
                str(config.capture_helper),
                "--width", str(config.capture_width),
                "--height", str(config.capture_height),
                "--fps", str(config.capture_fps),
            ],
            stdout=subprocess.PIPE,
        )

    def _read_exactly(self, size: int) -> bytes:
        assert self.process.stdout is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self.process.stdout.read(remaining)
            if not chunk:
                code = self.process.poll()
                raise RuntimeError(f"iPhone capture helper stopped (exit code {code})")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def grab(self) -> np.ndarray:
        magic, width, height, payload_size = struct.unpack(
            ">4sIII", self._read_exactly(16)
        )
        expected = width * height * 4
        if magic != b"RLS1" or payload_size != expected:
            raise RuntimeError(
                f"Invalid capture packet: magic={magic!r}, size={payload_size}, expected={expected}"
            )
        payload = self._read_exactly(payload_size)
        return np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)

    def __enter__(self) -> "AVFoundationCapture":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class IOSScreenshotCapture:
    """Poll iOS still screenshots without opening a media capture stream."""

    def __init__(self, config: Config) -> None:
        try:
            from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
        except ImportError as error:
            raise RuntimeError(
                "Screenshot capture requires pymobiledevice3; reinstall requirements.txt"
            ) from error

        self.width = config.capture_width
        self.height = config.capture_height
        self.loop = asyncio.new_event_loop()
        self.tunnel = PreferredRsdTunnel(serial=config.device_udid)
        self.provider = None
        self.screenshot = None
        try:
            rsd = self.loop.run_until_complete(self.tunnel.aopen())
            self.provider = DvtProvider(rsd)
            self.loop.run_until_complete(self.provider.connect())
            self.screenshot = Screenshot(self.provider)
            self.loop.run_until_complete(self.screenshot.connect())
        except Exception:
            self.close()
            raise

    def grab(self) -> np.ndarray:
        assert self.screenshot is not None
        encoded = self.loop.run_until_complete(self.screenshot.get_screenshot())
        frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("iPhone returned an invalid screenshot")
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        return frame

    def close(self) -> None:
        if self.loop.is_closed():
            return
        try:
            if self.screenshot is not None:
                self.loop.run_until_complete(self.screenshot.close())
            if self.provider is not None:
                self.loop.run_until_complete(self.provider.close())
            self.loop.run_until_complete(self.tunnel.aclose())
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        finally:
            self.loop.close()

    def __enter__(self) -> "IOSScreenshotCapture":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@contextmanager
def open_capture(config: Config) -> Iterator[object | None]:
    if config.capture_source == "avfoundation":
        with AVFoundationCapture(config) as capture:
            yield capture
    elif config.capture_source == "screenshot":
        with IOSScreenshotCapture(config) as capture:
            yield capture
    elif config.capture_source == "screen":
        with mss.MSS() as capture:
            yield capture
    else:
        yield None


def grab_frame(config: Config, capture: object | None = None) -> np.ndarray:
    if config.capture_source == "avfoundation":
        if capture is None:
            with AVFoundationCapture(config) as temporary_capture:
                return temporary_capture.grab()
        if not isinstance(capture, AVFoundationCapture):
            raise TypeError("Expected an AVFoundationCapture")
        return capture.grab()
    if config.capture_source == "screenshot":
        if capture is None:
            with IOSScreenshotCapture(config) as temporary_capture:
                return temporary_capture.grab()
        if not isinstance(capture, IOSScreenshotCapture):
            raise TypeError("Expected an IOSScreenshotCapture")
        return capture.grab()
    if config.capture_source == "quicktime":
        return grab_window(config.window_owner, config.window_title)
    if config.capture_source == "screen":
        if capture is None:
            with mss.MSS() as temporary_capture:
                return grab_monitor(temporary_capture, config.monitor)
        if not isinstance(capture, mss.MSS):
            raise TypeError("Expected an MSS capture")
        return grab_monitor(capture, config.monitor)
    raise ValueError(f"Unknown capture_source: {config.capture_source!r}")


def preprocess(image: np.ndarray) -> np.ndarray:
    """Extract small white UI text while suppressing coloured video content."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    channel_max = image.max(axis=2).astype(np.int16)
    channel_min = image.min(axis=2).astype(np.int16)
    # Instagram's label is neutral white. Penalising channel spread removes
    # most colourful Reel pixels before the local bright-feature extraction.
    white_score = np.clip(
        gray.astype(np.int16) - 2 * (channel_max - channel_min), 0, 255
    ).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    local_bright = cv2.morphologyEx(white_score, cv2.MORPH_TOPHAT, kernel)
    _, binary = cv2.threshold(local_bright, 35, 255, cv2.THRESH_BINARY)
    return binary


def load_templates(directory: Path) -> list[tuple[str, np.ndarray]]:
    loaded: list[tuple[str, np.ndarray]] = []
    for path in sorted(directory.glob("*.png")):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is not None:
            loaded.append((path.name, preprocess(image)))
    if not loaded:
        raise RuntimeError(f"No PNG templates found in {directory}")
    return loaded


def best_match(
    roi_image: np.ndarray, templates: list[tuple[str, np.ndarray]]
) -> tuple[float, str]:
    haystack = preprocess(roi_image)
    best_score, best_name = -1.0, ""
    for name, template in templates:
        th, tw = template.shape
        hh, hw = haystack.shape
        if th > hh or tw > hw:
            continue
        result = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())
        if score > best_score:
            best_score, best_name = score, name
    return best_score, best_name


def select_roi(config_path: Path, monitor_index: int) -> None:
    config = Config.load(config_path)
    if config.capture_source == "screen":
        raw = json.loads(config_path.read_text())
        raw["monitor"] = monitor_index
        config_path.write_text(json.dumps(raw, indent=2) + "\n")
        config = Config.load(config_path)
    frame = grab_frame(config)
    x, y, w, h = (int(v) for v in cv2.selectROI(
        "Drag around Sponsored/Ad label, then press Enter", frame, False, False
    ))
    cv2.destroyAllWindows()
    if w <= 0 or h <= 0:
        raise RuntimeError("ROI selection cancelled")
    raw = json.loads(config_path.read_text())
    raw["roi"] = [x, y, w, h]
    config_path.write_text(json.dumps(raw, indent=2) + "\n")
    print(f"Saved ROI {[x, y, w, h]} to {config_path}")


def capture_template(config: Config, name: str) -> None:
    x, y, w, h = config.roi
    if w <= 0 or h <= 0:
        raise RuntimeError("Configure the ROI first with --select-roi")
    frame = grab_frame(config)
    roi = frame[y : y + h, x : x + w]
    # Select tightly around the label within the larger detection ROI.
    tx, ty, tw, th = (int(v) for v in cv2.selectROI(
        "Drag tightly around the label, then press Enter", roi, False, False
    ))
    cv2.destroyAllWindows()
    if tw <= 0 or th <= 0:
        raise RuntimeError("Template selection cancelled")
    config.templates_dir.mkdir(parents=True, exist_ok=True)
    output = config.templates_dir / f"{name}.png"
    cv2.imwrite(str(output), roi[ty : ty + th, tx : tx + tw])
    print(f"Saved template to {output}")


class Esp32Trigger:
    def __init__(self, port: str, baud: int) -> None:
        self.serial = serial.Serial(port, baud, timeout=2)
        # Opening a CP2102 serial port can reset some ESP32 boards.
        time.sleep(1.5)
        self.serial.reset_input_buffer()

    def command(self, value: str) -> str:
        self.serial.write((value.strip().upper() + "\n").encode("ascii"))
        self.serial.flush()
        response = self.serial.readline().decode("utf-8", errors="replace").strip()
        if not response:
            raise RuntimeError(f"ESP32 did not acknowledge {value!r}")
        return response

    def close(self) -> None:
        self.serial.close()


def test_serial(config: Config, command: str) -> None:
    trigger = Esp32Trigger(config.serial_port, config.serial_baud)
    try:
        print(trigger.command(command))
    finally:
        trigger.close()


def run(config: Config, dry_run: bool, preview: bool) -> None:
    x, y, w, h = config.roi
    if w <= 0 or h <= 0:
        raise RuntimeError("Configure the ROI first with --select-roi")
    templates = load_templates(config.templates_dir)
    trigger = None if dry_run else Esp32Trigger(config.serial_port, config.serial_baud)
    consecutive = 0
    last_triggered = -float("inf")
    LOG.info("Loaded %d template(s); threshold %.3f", len(templates), config.match_threshold)
    try:
        with open_capture(config) as capture:
            while True:
                started = time.monotonic()
                frame = grab_frame(config, capture)
                roi = frame[y : y + h, x : x + w]
                score, template_name = best_match(roi, templates)
                detected = score >= config.match_threshold
                consecutive = consecutive + 1 if detected else 0
                LOG.info("score=%.3f template=%s confirmations=%d", score, template_name, consecutive)

                now = time.monotonic()
                ready = now - last_triggered >= config.cooldown_seconds
                if consecutive >= config.confirmations_required and ready:
                    if dry_run:
                        LOG.warning("DRY RUN: would send %s", config.trigger_command)
                    else:
                        assert trigger is not None
                        LOG.warning("ESP32: %s", trigger.command(config.trigger_command))
                    last_triggered = now
                    consecutive = 0

                if preview:
                    colour = (0, 255, 0) if detected else (0, 0, 255)
                    shown = roi.copy()
                    cv2.putText(shown, f"{score:.3f}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, .7, colour, 2)
                    cv2.imshow("Reels detector ROI (q to quit)", shown)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                elapsed = time.monotonic() - started
                time.sleep(max(0.0, config.sample_interval_seconds - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        if trigger is not None:
            trigger.close()
        cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect Reels ads and trigger an ESP32 BLE swipe")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--select-roi", action="store_true")
    action.add_argument("--capture-template", metavar="NAME")
    action.add_argument("--test-serial", metavar="COMMAND")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.select_roi:
        select_roi(args.config, args.monitor)
        return
    config = Config.load(args.config)
    if args.capture_template:
        capture_template(config, args.capture_template)
    elif args.test_serial:
        test_serial(config, args.test_serial)
    else:
        run(config, args.dry_run, args.preview)


if __name__ == "__main__":
    main()
