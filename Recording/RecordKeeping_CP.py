#!/usr/bin/env python3
"""
Record Keeping GUI
Author: Cherese Jordan
Records video with multiple devices and cameras.
Can record with Windows, Mac, Linux, and Raspberry Pi 5

Current Features:
- record video with connected cameras
- takes input for desired duration (optional)
- creates a folder for recordings (VideoA)
- Save MP4 or MJPG (avi) video
-

Future:
Improve Resolution of Camera

Notes:
    If video is too fast, reduce default fps and vice versa
    version Python 3.12 or higher
python -m pip install opencv-python
can change recording path to desired output file (.avi, .mp4, "new_recording_", ...)
search in file to adjust FPS: change FPS here
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROGRAM_TITLE = "Record Keeping"
FILENAME_PREFIX = "com_record_"

DEFAULT_CAMERA_INDEX = 0
DEFAULT_DEVICE = "/dev/video0"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 9.2


def default_output_folder() -> Path:
    system = platform.system().lower()

    if system == "windows":
        return Path.home() / "Videos" / "FS-recordings"

    if system == "darwin":
        return Path.home() / "Movies" / "FS-recordings"

    return Path.home() / "Videos" / "FS-recordings"


def next_recording_path(folder: Path, suffix: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)

    index = 0
    while True:
        candidate = folder / f"{FILENAME_PREFIX}{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def ask_duration() -> float:
    while True:
        try:
            duration = float(input("Enter Duration (s): ").strip())
            if duration <= 0:
                print("Duration must be greater than 0.")
                continue
            return duration
        except ValueError:
            print("Please enter a positive number.")


def command_exists(command_name: str) -> bool:
    return shutil.which(command_name) is not None


def detect_default_mode(requested_mode: str) -> str:
    if requested_mode != "auto":
        return requested_mode

    system = platform.system().lower()

    if system == "windows" or system == "darwin":
        return "opencv"

    if system == "linux":
        if command_exists("ffmpeg"):
            return "usb"
        return "opencv"

    return "opencv"


def record_usb_camera_ffmpeg(
        duration: float,
        output_path: Path,
        device: str,
        width: int,
        height: int,
        fps: int,
) -> None:
    if not command_exists("ffmpeg"):
        raise RuntimeError(
            "ffmpeg is required for USB mode on Linux/Raspberry Pi.\n"
            "Install it with: sudo apt install ffmpeg\n"
            "Or run with: --mode opencv"
        )

    command = [
        "ffmpeg",
        "-y",
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-video_size", f"{width}x{height}",
        "-framerate", str(fps),
        "-t", str(duration),
        "-i", device,
        "-c:v", "copy",
        str(output_path),
    ]

    print(f"Recording USB camera to: {output_path}")
    print(f"Device: {device}")
    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps}")
    print(f"Duration: {duration} seconds")

    subprocess.run(command, check=True)


def find_pi_camera_command() -> str | None:
    for command_name in ("rpicam-vid", "libcamera-vid"):
        if command_exists(command_name):
            return command_name
    return None


def record_pi_camera(
        duration: float,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
) -> None:
    command_name = find_pi_camera_command()

    if command_name is None:
        raise RuntimeError(
            "Could not find rpicam-vid or libcamera-vid.\n"
            "Use this mode only for Raspberry Pi ribbon/CSI cameras."
        )

    milliseconds = int(duration * 1000)

    command = [
        command_name,
        "--timeout", str(milliseconds),
        "--width", str(width),
        "--height", str(height),
        "--framerate", str(fps),
        "--codec", "h264",
        "--output", str(output_path),
    ]

    print(f"Recording ribbon/CSI camera to: {output_path}")
    print(f"Using: {command_name}")
    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps}")
    print(f"Duration: {duration} seconds")

    subprocess.run(command, check=True)


def opencv_backend():
    import cv2

    system = platform.system().lower()
    if system == "windows":
        return cv2.CAP_DSHOW
    if system == "darwin":
        return cv2.CAP_AVFOUNDATION
    if system == "linux":
        return cv2.CAP_V4L2
    return 0


def record_opencv(
        duration: float,
        output_path: Path,
        camera_index: int,
        width: int,
        height: int,
        fps: float,
) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is not installed. Install it with:\n"
            "python -m pip install opencv-python"
        ) from exc

    cap = cv2.VideoCapture(camera_index, opencv_backend())

    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {camera_index}")

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    print("Warming up camera...")
    warmup_start = time.monotonic()

    while time.monotonic() - warmup_start < 5:
        cap.read()

    ok, frame = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError("Could not read a frame after camera warmup.")

    actual_height, actual_width = frame.shape[:2]

    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(
        str(output_path),
        fourcc,
        fps,
        (actual_width, actual_height),
    )

    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not create OpenCV video writer.")

    print(f"Recording OpenCV camera to: {output_path}")
    print(f"Camera index: {camera_index}")
    print(f"Resolution: {actual_width}x{actual_height}")
    print(f"FPS: {fps}")
    print(f"Duration: {duration} seconds")

    start = time.monotonic()
    frames = 0

    try:
        while time.monotonic() - start < duration:
            ok, frame = cap.read()
            if not ok:
                print("Warning: dropped frame")
                continue

            writer.write(frame)
            frames += 1

    finally:
        writer.release()
        cap.release()

    print(f"Frames recorded: {frames}")


def main() -> int:
    print(f"=== {PROGRAM_TITLE} ===")

    parser = argparse.ArgumentParser(description=PROGRAM_TITLE)

    parser.add_argument("--duration", type=float, help="Recording duration in seconds")
    parser.add_argument(
        "--mode",
        choices=["auto", "usb", "pi-camera", "opencv"],
        default="auto",
        help=(
            "auto chooses the best default for your OS; "
            "usb uses ffmpeg/V4L2 for Linux USB cameras; "
            "pi-camera uses rpicam/libcamera for ribbon cameras; "
            "opencv is the cross-platform fallback"
        ),
    )
    parser.add_argument("--folder", default=str(default_output_folder()), help="Output folder")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Linux USB camera device, usually /dev/video0")
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA_INDEX, help="OpenCV camera index")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)

    args = parser.parse_args()

    duration = args.duration if args.duration is not None else ask_duration()

    if duration <= 0:
        print("Duration must be greater than 0.", file=sys.stderr)
        return 1

    selected_mode = detect_default_mode(args.mode)
    print(f"Selected mode: {selected_mode}")

    folder = Path(args.folder).expanduser().resolve()

    if selected_mode == "pi-camera":
        output_path = next_recording_path(folder, ".h264")
    else:
        output_path = next_recording_path(folder, ".avi")

    try:
        if selected_mode == "usb":
            record_usb_camera_ffmpeg(
                duration,
                output_path,
                args.device,
                args.width,
                args.height,
                args.fps,
            )

        elif selected_mode == "pi-camera":
            record_pi_camera(
                duration,
                output_path,
                args.width,
                args.height,
                args.fps,
            )

        else:
            record_opencv(
                duration,
                output_path,
                args.camera,
                args.width,
                args.height,
                args.fps,
            )

    except Exception as error:
        print(f"Recording failed: {error}", file=sys.stderr)
        return 1

    print(f"Saved video: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
