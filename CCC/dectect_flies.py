# Copyright (c) 2025 Thomas Zimmerman — MIT License
"""
detector.py

Foreground detection module for plankton tracking.

This module performs perception only:
- Background subtraction
- Thresholding
- Morphology cleanup
- Contour extraction

It does NOT:
- assign identity
- infer behavior
- compute motion or polarity

Those are handled downstream.
"""

import cv2
import numpy as np
import config
from mask_utils import apply_circular_mask

# ------------------------------------------------------------
# Global cached background (median V channel)
# ------------------------------------------------------------
_BG_MEDIAN_V = None


def _build_median_background(cap):
    """
    Build median background from HSV Value channel.
    Uses evenly sampled frames across the video.
    """
    Vs = []

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        raise RuntimeError("Invalid frame count while building background")

    n = min(config.BG_SAMPLES, frame_count)
    idxs = np.linspace(0, frame_count - 1, n).astype(int)

    print(f"Building median V background using {n} frames")

    for k, idx in enumerate(idxs, start=1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        V = hsv[:, :, 2]
        Vs.append(V)

        if k % 10 == 0:
            cv2.waitKey(1)

    if not Vs:
        raise RuntimeError("No frames collected for background")

    return np.median(np.stack(Vs, axis=0), axis=0).astype(np.uint8)


def detect_plankton(frame, cap=None):
    """
    Detect plankton contours in a frame.

    Returns
    -------
    detections : list of dict
        Each dict contains:
            - contour
            - centroid
    mask : uint8
        Binary foreground mask
    status : str
        "OK" or "NO_OBJECT"
    """
    global _BG_MEDIAN_V

    # --------------------------------------------------------
    # Initialize background model
    # --------------------------------------------------------
    if _BG_MEDIAN_V is None:
        if cap is None:
            raise RuntimeError("VideoCapture required to initialize background")
        _BG_MEDIAN_V = _build_median_background(cap)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    # --------------------------------------------------------
    # HSV → Value channel
    # --------------------------------------------------------
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2]

    # --------------------------------------------------------
    # Foreground (saturating subtraction)
    # --------------------------------------------------------
    Fg = cv2.subtract(V, _BG_MEDIAN_V)

    # --------------------------------------------------------
    # Optional Gaussian blur
    # --------------------------------------------------------
    if hasattr(config, "GAUSSIAN_BLUR") and config.GAUSSIAN_BLUR:
        kx, ky = config.GAUSSIAN_BLUR
        if kx > 1 and ky > 1:
            Fg = cv2.GaussianBlur(Fg, (kx, ky), 0)

    # --------------------------------------------------------
    # Thresholding
    # --------------------------------------------------------
    if config.THRESH_METHOD.lower() == "otsu":
        _, mask = cv2.threshold(
            Fg, 0, 255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
    else:
        _, mask = cv2.threshold(
            Fg,
            int(config.THRESH),
            255,
            cv2.THRESH_BINARY
        )

    # --------------------------------------------------------
    # Apply circular ROI mask (if enabled)
    # --------------------------------------------------------
    mask = apply_circular_mask(mask)

    # --------------------------------------------------------
    # Foreground energy gate (reject noise-only frames)
    # --------------------------------------------------------
    if hasattr(config, "MAX_FOREGROUND_PIXELS"):
        if np.count_nonzero(mask) > config.MAX_FOREGROUND_PIXELS:
            return [], mask, "NO_OBJECT"

    # --------------------------------------------------------
    # Morphology cleanup
    # --------------------------------------------------------
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    )

    # --------------------------------------------------------
    # Contour extraction
    # --------------------------------------------------------
    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    detections = []
    h_img, w_img = mask.shape

    for c in contours:
        area = cv2.contourArea(c)
        if area < config.MIN_A or area > config.MAX_A:
            continue

        rect = cv2.minAreaRect(c)
        (x, y), (rw, rh), _ = rect

        w = int(min(rw, rh))
        h = int(max(rw, rh))

        if x <= 2 or y <= 2 or x >= w_img - 2 or y >= h_img - 2:
            continue

        if w < config.MIN_WH or w > config.MAX_WH:
            continue
        if h < config.MIN_WH or h > config.MAX_WH:
            continue

        M = cv2.moments(c)
        if M["m00"] == 0:
            continue

        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])

        detections.append({
            "contour": c,
            "centroid": (cx, cy)
        })

    if not detections:
        return [], mask, "NO_OBJECT"

    return detections, mask, "OK"


# ------------------------------------------------------------
# Public API expected by pipeline.py
# ------------------------------------------------------------
def detect_objects(frame, cap=None):
    """
    Wrapper to match pipeline API.
    """
    detections, mask, _ = detect_plankton(frame, cap)
    return detections, mask
