#!/usr/bin/env python3
"""
Record Keeping
Author: Cherese Jordan
Records video on Raspberry Pi 5.

Current Features:
- takes input for desired duration of video in terminal
- creates a folder for recordings
- different modes that use Open Cv, rpicam or libcam to record based on what
    camera is being used
- records video, stored in FS-recordings folder

Future:
Use matlab to create small GUI

Notes:
python -m pip install opencv-python
can change FN_EXT to desired output file (.avi, .mp4, ...)
"""

import os
import re # regular expressions (pattern matching)
import sys
import time
from pathlib import Path
import cv2

# constants
P_TITLE = "Record Keeping"               # Program Title
OUTPUT_DIR = Path("FS_recordings")      # Output Directory
FN_PREFIX = "com_record_"               # File Name (FN)
FN_EXT = ".mp4"
D_CAM_INDEX = 0                      # Default (D)
D_FPS = 30.0
D_WIDTH = 1280
D_HEIGHT = 720

# utility functions

def get_duration_from_terminal() -> float | None:
    while True:
        try:
            duration = float(input("Enter Duration (s): "))

            if duration <= 0:
                print("Duration must be greater than 0.")
                continue
            return duration

        except ValueError:
            print("Please enter a number.")

# creates a file path with a unique name to avoid overwriting files/videos
def next_recording_path() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    largest_num = -1
    pattern = re.compile(rf"^{re.escape(FN_PREFIX)}(\d+){re.escape(FN_EXT)}$")

    for item in OUTPUT_DIR.iterdir():
        match = pattern.match(item.name)
        if match:
            largest_num = max(largest_num, int(match.group(1)))

    next_num = largest_num + 1
    return OUTPUT_DIR / f"{FN_PREFIX}{next_num}{FN_EXT}"

# identifies camera and opens it
# uses default settings for FPS and video size
def open_camera(camera_index: int):
    # cv2.CAP_V4L2 targets Linux video devices such as /dev/video0.
    cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {camera_index}. "
            "Check that a USB camera is connected. ls in terminal."
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, D_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, D_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, D_FPS)

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or D_WIDTH
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or D_HEIGHT
    fps = cap.get(cv2.CAP_PROP_FPS)

    if fps <= 1 or fps > 120:
        fps = D_FPS

    return cap, width, height, fps


def create_video_writer(output_path: Path, width: int, height: int, fps: float):
    codecs = ["mp4v", "avc1", "XVID"]

    for codec in codecs:
        fourcc = cv2.VideoWriter_fourcc(*codec)
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
        if writer.isOpened():
            return writer
        writer.release()

    raise RuntimeError("Could not create video writer. Try installing OpenCV video codec support.")


def record_video(duration: float, camera_index: int = D_CAM_INDEX) -> Path:
    output_path = next_recording_path()
    cap, width, height, fps = open_camera(camera_index)
    writer = create_video_writer(output_path, width, height, fps)

    print(f"Recording to: {output_path}")
    print(f"Duration: {duration} seconds")
    print(f"Camera: /dev/video{camera_index}, Resolution: {width}x{height}, FPS: {fps:.2f}")

    start_time = time.monotonic()
    frames_written = 0

    try:
        while time.monotonic() - start_time < duration:
            ok, frame = cap.read()
            if not ok:
                print("Warning: could not read frame from camera.")
                time.sleep(0.05)
                continue

            writer.write(frame)
            frames_written += 1

    finally:
        writer.release()
        cap.release()

    if frames_written == 0:
        try:
            output_path.unlink(missing_ok=True)
        except TypeError:
            if output_path.exists():
                output_path.unlink()
        raise RuntimeError("No frames were recorded. Check camera permissions and device connection.")

    print(f"Saved video: {output_path}")
    print(f"Frames recorded: {frames_written}")
    return output_path

def main():
    print(f"=== {P_TITLE} ===")

    camera_index = D_CAM_INDEX
    if len(sys.argv) >= 2:
        try:
            camera_index = int(sys.argv[1])
        except ValueError:
            print("Camera index must be a number, for example: python3 RecordKeeping.py 0")
            sys.exit(1)

    duration = get_duration_from_terminal()

    try:
        record_video(duration, camera_index)
    except Exception as error:
        print(f"Error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()

