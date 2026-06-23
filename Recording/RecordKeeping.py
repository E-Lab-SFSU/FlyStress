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
D_CAMERA_INDEX = 0                      # Default (D)
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

def next_recording_path() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)