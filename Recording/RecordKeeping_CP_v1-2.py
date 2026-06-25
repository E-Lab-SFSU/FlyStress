#!/usr/bin/env python3
"""
Record Keeping
Author: Cherese Jordan

Records video on multiple different devices and cameras.
Can be used on Raspberry Pi 5, Windows, Mac, Linux

Modes:
- usb: USB cameras such as Arducam using ffmpeg + V4L2
- pi-camera: Raspberry Pi ribbon/CSI cameras using rpicam-vid/libcamera-vid
- opencv: fallback mode using OpenCV

Install:
sudo apt install ffmpeg v4l-utils
python3 -m pip install opencv-python

Notes:
Uses MJPG because it typically has the highest resolution + fps. can be changed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROGRAM_TITLE = "Record Keeping"
DEFAULT_FOLDER = Path.home() / "Videos" / "FS-recordings"
FILENAME_PREFIX = "com_record_"

DEFAULT_DEVICE = "/dev/video0"
DEFAULT_CAMERA_INDEX = 0

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 50

# creates a unique file name for each video to prevent overriding 
def next_recording_path(folder: Path, suffix: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)

    index = 0
    while True:
        candidate = folder / f"{FILENAME_PREFIX}{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1

# getting input from the terminal, saved desired duration as float
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

# checks if all required commands are installed, breaks if not
def require_command(command_name: str) -> None:
    if shutil.which(command_name) is None:
        raise RuntimeError(
            f"Required command not found: {command_name}\n"
            f"Install it first. Example: sudo apt install {command_name}"
        )

# recording with a USB camera attached to device
# ffmpeg is used to record and what not with any audio or video device
def record_usb_camera_ffmpeg(
    duration: float,
    output_path: Path,
    device: str,
    width: int,
    height: int,
    fps: int,
) -> None:
    require_command("ffmpeg")

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
        if shutil.which(command_name):
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
            "Could not find rpicam-vid or libcamera-vid. "
            "This mode is for Raspberry Pi ribbon/CSI cameras."
        )

    milliseconds = int(duration * 1000)
    # Raspi expects args in milliseconds
    command = [
        command_name,
        "--timeout", str(milliseconds),
        "--width", str(width),
        "--height", str(height),
        "--framerate", str(fps),
        "--codec", "h264",
        "--output", str(output_path),
    ]

    print(f"Recording ribbon camera to: {output_path}")
    print(f"Using: {command_name}")
    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps}")
    print(f"Duration: {duration} seconds")

    subprocess.run(command, check=True)


def record_opencv(
    duration: float,
    output_path: Path,
    camera_index: int,
    width: int,
    height: int,
    fps: int,
) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is not installed. Install it with:\n"
            "python3 -m pip install opencv-python"
        ) from exc

    cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)

    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {camera_index}")

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or width
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or height

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

    print(f"Recording to: {output_path}")
    print(f"Resolution: {actual_width}x{actual_height}")
    print(f"FPS: {fps}")
    print(f"Duration (s): {duration}")

    start = time.monotonic()
    frames = 0

    try:
        while time.monotonic() - start < duration:
            ok, frame = cap.read()
            if not ok:
                print("Warning: frames dropping quickly...")
                continue

            writer.write(frame)
            frames += 1

    finally:
        writer.release()
        cap.release()

    print(f"Frames captured: {frames}")


def main() -> int:
    print(f"=== {PROGRAM_TITLE} ===")

    parser = argparse.ArgumentParser(description=PROGRAM_TITLE)

    parser.add_argument("--duration", type=float, help="Enter duration (s): ")
    parser.add_argument(
        "--mode",
        choices=["usb", "pi-camera", "opencv"],
        default="usb",
        help="usb for USB camera, pi-camera for ribbon camera, opencv as fallback",
    )
    parser.add_argument("--folder", default=str(DEFAULT_FOLDER), help="Output folder")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="USB camera device")
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA_INDEX)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)

    args = parser.parse_args()

    duration = args.duration if args.duration is not None else ask_duration()

    if duration <= 0:
        print("Duration must be greater than 0.", file=sys.stderr)
        return 1

    folder = Path(args.folder).expanduser().resolve()

    # determines output (avi, mjpg, mp4) based on camera being used
    if args.mode == "pi-camera":
        output_path = next_recording_path(folder, ".h264")
    else:
        output_path = next_recording_path(folder, ".avi")
    # records with usb camera if detected
    try:
        if args.mode == "usb":
            record_usb_camera_ffmpeg(
                duration,
                output_path,
                args.device,
                args.width,
                args.height,
                args.fps,
            )
        # records with pi cam if detected
        elif args.mode == "pi-camera":
            record_pi_camera(
                duration,
                output_path,
                args.width,
                args.height,
                args.fps,
            )
        # records with any camera. Open CV to record
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
