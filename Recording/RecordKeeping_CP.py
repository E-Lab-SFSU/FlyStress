#!/usr/bin/env python3
"""
Record Keeping Cross Platform (CP)
Author: Cherese Jordan
Records video with multiple devices and cameras.
Can record with Windows, Mac, Linux, and Raspberry Pi 5

Current Features:
- takes input for desired duration of video in terminal
- creates a folder for recordings
- different modes that use Open Cv, rpicam or libcam to record based on what
    camera is being used
- records video, stored in FS-recordings folder

Future:
Use matlab to create small GUI

Notes:
    version Python 3.12 or higher
python -m pip install opencv-python
can change recording path to desired output file (.avi, .mp4, "new_recording_", ...)
search in file to adjust FPS: change FPS here
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import cv2
import time
from pathlib import Path

# operating functions

# creates a file for each video to be saved and stored in specific folder
def next_recording_path(folder: Path, prefix: str = "comp_record_", suffix: str = ".avi") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        candidate = folder / f"{prefix}{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1

# measures the FPS of the camera being used
def measure_camera_fps(camera_index=0, test_duration=2):
    cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera {camera_index}")

    frames = 0
    start_time = time.time()

    while (time.time() - start_time) < test_duration:
        ret, frame = cap.read()

        if ret:
            frames += 1

    elapsed = time.time() - start_time

    cap.release()

    fps = frames / elapsed

    return fps

# gets input from user for duration of video
def ask_duration() -> float:
    while True:
        try:
            duration = float(input("Enter Duration (s): ").strip())

            if duration <= 0:
                raise ValueError
            return duration
        except ValueError:
            print("Please enter a positive number.")

# detect the OS being used
def detect_opencv_backend():
    system = platform.system().lower()
    if system == "windows":
        return "dshow"
    if system == "darwin":
        return "avfoundation"
    return "v4l2"

# if opencv is detected, it is then used to record video
# change FPS here
def record_with_opencv(duration: float, output_path: Path, camera_index: int = 0, fps: float = 9.1) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is not installed in your directory. Install it with: python -m pip install opencv-python"
        ) from exc

    backend_name = detect_opencv_backend()
    backend_map = {
        "dshow": cv2.CAP_DSHOW,
        "avfoundation": cv2.CAP_AVFOUNDATION,
        "v4l2": cv2.CAP_V4L2,
    }

    backend = backend_map.get(backend_name, 0)
    cap = cv2.VideoCapture(camera_index, backend)

    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {camera_index}. Try --camera 1 or another index."
        )

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480

    if width <= 0 or height <= 0:
        width, height = 640, 480

    # mp4v is broadly supported by OpenCV across Windows/macOS/Linux.
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Could not create video writer for MJPG output.")

    print(f"Recording to: {output_path}")
    print("Press Ctrl+C to stop early.")

    start = time.time()
    frames = 0

    try:
        frame_interval = 1.0 / fps
        next_frame_time = time.time()

        while time.time() - start < duration:
            now = time.time()

            if now < next_frame_time:
                time.sleep(next_frame_time - now)

            ok, frame = cap.read()
            if not ok:
                print("Warning: dropped frame")
                continue

            writer.write(frame)
            frames += 1
            next_frame_time += frame_interval
    except KeyboardInterrupt:
        print("Stopped early by user.")
    finally:
        writer.release()
        cap.release()

    print(f"Saved {frames} frames to {output_path}")


def find_pi_camera_command() -> str | None:
    for cmd in ("rpicam-vid", "libcamera-vid"):
        if shutil.which(cmd):
            return cmd
    return None

# record if opencv is not detected
def record_with_pi_camera(duration: float, output_path: Path, width: int = 1280, height: int = 800) -> None:
    cmd_name = find_pi_camera_command()
    if cmd_name is None:
        raise RuntimeError(
            "Could not find rpicam-vid or libcamera-vid. Install/enable the Raspberry Pi camera tools."
        )

    temp_h264 = output_path.with_suffix(".h264")
    milliseconds = int(duration * 1000)

    command = [
        cmd_name,
        "--timeout", str(milliseconds),
        "--width", str(width),
        "--height", str(height),
        "--codec", "h264",
        "--output", str(temp_h264),
    ]

    print(f"Recording with {cmd_name} to: {output_path}")
    subprocess.run(command, check=True)

    # Convert H.264 elementary stream to MP4 if ffmpeg exists.
    # change FPS here
    if shutil.which("ffmpeg"):
        subprocess.run([
            "ffmpeg", "-y", "-framerate", "100", "-i", str(temp_h264),
            "-c", "copy", str(output_path)
        ], check=True)
        try:
            temp_h264.unlink()
        except OSError:
            pass
    else:
        fallback = output_path.with_suffix(".h264")
        print(f"ffmpeg not found, saved raw H.264 video instead: {fallback}")


def main() -> int:

    # change FPS here
    parser = argparse.ArgumentParser(description="Record Keeping")
    parser.add_argument("--duration", type=float, help="Enter duration (s)")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index, default 0")
    parser.add_argument("--fps", type=float, default=9.1, help="Frames per second (FPS) for recording")
    parser.add_argument(
        "--mode",
        choices=["opencv", "pi-camera"],
        default="opencv",
        help="Use opencv for USB/built-in webcams; use pi-camera for Raspberry Pi CSI/ribbon camera",
    )
    parser.add_argument(
        "--folder",
        default=r"C:\Users\chana\Videos\FS-recordings",
        help="Output folder"
    )
    args = parser.parse_args()

    if args.mode == "pi-camera":
        args.fps = 100

    duration = args.duration if args.duration is not None else ask_duration()
    if duration <= 0:
        print("Duration must be greater than 0.", file=sys.stderr)
        return 1

    folder = Path(args.folder).expanduser().resolve()
    output_path = next_recording_path(folder)

    try:
        if args.mode == "pi-camera":
            record_with_pi_camera(duration, output_path)
        else:
            record_with_opencv(duration, output_path, camera_index=args.camera, fps=args.fps)
    except Exception as exc:
        print(f"Recording failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
