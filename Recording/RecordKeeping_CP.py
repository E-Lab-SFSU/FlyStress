#!/usr/bin/env python3
"""
Record Keeping Cross Platform (CP)
Author: Cherese Jordan
Records video with multiple devices.
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
python -m pip install opencv-python
can change FN_EXT to desired output file (.avi, .mp4, ...)
"""
