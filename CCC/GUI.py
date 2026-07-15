import sys
import csv
import time
import math
import cv2
import numpy as np

from pathlib import Path
from datetime import datetime
from collections import deque

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QSpinBox, QDoubleSpinBox,
    QLineEdit, QCheckBox, QFileDialog
)


class WellState:
    def __init__(self):
        self.last_position = None
        self.movement_window = deque()
        self.total_window_distance = 0.0
        self.sleep_state = "UNKNOWN"


class ExperimentSettings:
    def __init__(self):
        self.camera_index = 0
        self.width = 1280
        self.height = 720

        self.frames_per_second = 1.0
        self.rows = 4
        self.cols = 6

        self.plate_x = 0
        self.plate_y = 0
        self.plate_w = 1280
        self.plate_h = 720

        self.threshold = 35
        self.min_area = 5
        self.max_area = 500
        self.sleep_window_sec = 300
        self.sleep_distance_threshold = 5

        self.foreground_mode = "dark"
        self.save_frames = True
        self.output_dir = "fly_experiment"


class ExperimentWorker(QThread):
    frame_ready = pyqtSignal(QImage)
    status_ready = pyqtSignal(str)
    finished_ready = pyqtSignal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.running = False

        self.background = None
        self.bg_frames = []
        self.bg_sample_count = 30

    def stop(self):
        self.running = False

    def run(self):
        self.running = True

        s = self.settings

        output_root = Path(s.output_dir)
        frames_dir = output_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        results_csv = output_root / "motion_results.csv"
        frame_index_csv = output_root / "frame_index.csv"

        cap = cv2.VideoCapture(s.camera_index)

        if not cap.isOpened():
            self.status_ready.emit("ERROR: Could not open camera.")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, s.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s.height)

        rois = self.generate_well_rois(s)
        states = {well["well_id"]: WellState() for well in rois}

        frame_number = 0
        start_time = time.time()
        next_capture_time = start_time
        interval = 1.0 / s.frames_per_second

        self.status_ready.emit("Experiment started.")

        with open(results_csv, "w", newline="") as result_file, open(frame_index_csv, "w", newline="") as index_file:
            result_writer = csv.writer(result_file)
            index_writer = csv.writer(index_file)

            result_writer.writerow([
                "frame_number",
                "elapsed_sec",
                "well_id",
                "row",
                "col",
                "fly_x",
                "fly_y",
                "area",
                "aspect_ratio",
                "movement_distance",
                "rolling_distance",
                "sleep_state",
                "detection_status"
            ])

            index_writer.writerow([
                "frame_number",
                "timestamp_unix",
                "elapsed_sec",
                "filename"
            ])

            while self.running:
                ret, frame = cap.read()

                if not ret:
                    self.status_ready.emit("WARNING: Camera frame failed.")
                    continue

                now = time.time()

                if now >= next_capture_time:
                    elapsed = now - start_time

                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                    if self.background is None:
                        self.bg_frames.append(gray)

                        self.status_ready.emit(
                            f"Building background: {len(self.bg_frames)}/{self.bg_sample_count}"
                        )

                        if len(self.bg_frames) >= self.bg_sample_count:
                            self.background = np.median(
                                np.stack(self.bg_frames, axis=0),
                                axis=0
                            ).astype(np.uint8)

                            self.status_ready.emit("Background ready. Detecting flies.")

                        display_frame = frame.copy()

                    else:
                        mask = self.create_foreground_mask(gray, self.background, s)
                        detections = {}

                        for well in rois:
                            well_id = well["well_id"]
                            roi = well["roi"]
                            state = states[well_id]

                            detection = self.detect_fly(mask, roi, s)

                            centroid = None
                            area = ""
                            aspect_ratio = ""
                            status = "NO_OBJECT"

                            if detection is not None:
                                centroid = detection["centroid"]
                                area = detection["area"]
                                aspect_ratio = detection["aspect_ratio"]
                                status = "OK"

                            movement, rolling, sleep_state = self.update_motion_state(
                                state,
                                centroid,
                                elapsed,
                                s
                            )

                            if centroid is None:
                                fly_x = ""
                                fly_y = ""
                            else:
                                fly_x, fly_y = centroid

                            result_writer.writerow([
                                frame_number,
                                elapsed,
                                well_id,
                                well["row"],
                                well["col"],
                                fly_x,
                                fly_y,
                                area,
                                aspect_ratio,
                                movement,
                                rolling,
                                sleep_state,
                                status
                            ])

                            detections[well_id] = {
                                "centroid": centroid,
                                "sleep_state": sleep_state,
                                "status": status
                            }

                        display_frame = self.draw_overlay(frame, rois, detections)

                    if s.save_frames:
                        filename = f"frame_{frame_number:06d}.jpg"
                        frame_path = frames_dir / filename
                        cv2.imwrite(
                            str(frame_path),
                            gray,
                            [cv2.IMWRITE_JPEG_QUALITY, 85]
                        )

                        index_writer.writerow([
                            frame_number,
                            now,
                            elapsed,
                            filename
                        ])

                    frame_number += 1
                    next_capture_time += interval

                    qimg = self.cv_to_qimage(display_frame)
                    self.frame_ready.emit(qimg)

                self.msleep(1)

        cap.release()
        self.finished_ready.emit(f"Experiment stopped. Data saved to: {output_root}")

    def generate_well_rois(self, s):
        rois = []

        well_w = s.plate_w / s.cols
        well_h = s.plate_h / s.rows

        well_id = 0

        for r in range(s.rows):
            for c in range(s.cols):
                x = int(s.plate_x + c * well_w)
                y = int(s.plate_y + r * well_h)
                w = int(well_w)
                h = int(well_h)

                rois.append({
                    "well_id": well_id,
                    "row": r,
                    "col": c,
                    "roi": (x, y, w, h)
                })

                well_id += 1

        return rois

    def create_foreground_mask(self, gray, background, s):
        if s.foreground_mode == "dark":
            fg = cv2.subtract(background, gray)
        elif s.foreground_mode == "bright":
            fg = cv2.subtract(gray, background)
        else:
            fg = cv2.absdiff(gray, background)

        fg = cv2.GaussianBlur(fg, (5, 5), 0)

        _, mask = cv2.threshold(
            fg,
            s.threshold,
            255,
            cv2.THRESH_BINARY
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )

        return mask

    def detect_fly(self, mask, roi, s):
        x, y, w, h = roi
        well_mask = mask[y:y + h, x:x + w]

        contours, _ = cv2.findContours(
            well_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        best = None
        best_area = 0

        for c in contours:
            area = cv2.contourArea(c)

            if area < s.min_area or area > s.max_area:
                continue

            if area <= best_area:
                continue

            bx, by, bw, bh = cv2.boundingRect(c)

            if bh == 0:
                continue

            aspect_ratio = bw / bh

            M = cv2.moments(c)

            if M["m00"] == 0:
                continue

            cx = int(M["m10"] / M["m00"]) + x
            cy = int(M["m01"] / M["m00"]) + y

            best = {
                "centroid": (cx, cy),
                "area": area,
                "aspect_ratio": aspect_ratio
            }

            best_area = area

        return best

    def update_motion_state(self, state, position, elapsed, s):
        movement = 0.0

        if position is not None and state.last_position is not None:
            dx = position[0] - state.last_position[0]
            dy = position[1] - state.last_position[1]
            movement = math.sqrt(dx * dx + dy * dy)

        if position is not None:
            state.last_position = position

        state.movement_window.append((elapsed, movement))
        state.total_window_distance += movement

        while state.movement_window:
            old_time, old_dist = state.movement_window[0]

            if elapsed - old_time <= s.sleep_window_sec:
                break

            state.movement_window.popleft()
            state.total_window_distance -= old_dist

        if elapsed < s.sleep_window_sec:
            state.sleep_state = "UNKNOWN"
        elif state.total_window_distance <= s.sleep_distance_threshold:
            state.sleep_state = "SLEEP"
        else:
            state.sleep_state = "AWAKE"

        return movement, state.total_window_distance, state.sleep_state

    def draw_overlay(self, frame, rois, detections):
        output = frame.copy()

        for well in rois:
            well_id = well["well_id"]
            x, y, w, h = well["roi"]

            cv2.rectangle(output, (x, y), (x + w, y + h), (120, 120, 120), 1)
            cv2.putText(
                output,
                str(well_id),
                (x + 5, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (255, 255, 255),
                1
            )

            det = detections.get(well_id)

            if det and det["centroid"]:
                cx, cy = det["centroid"]
                cv2.circle(output, (cx, cy), 5, (0, 255, 0), -1)

                cv2.putText(
                    output,
                    det["sleep_state"],
                    (x + 5, y + h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (0, 255, 0),
                    1
                )

        return output

    def cv_to_qimage(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w

        return QImage(
            rgb.data,
            w,
            h,
            bytes_per_line,
            QImage.Format.Format_RGB888
        ).copy()


class FlyShakerGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.worker = None

        self.setWindowTitle("Fly Shaker Motion Detection GUI")
        self.setMinimumSize(1200, 800)

        self.preview = QLabel("Camera preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background-color: black; color: white;")
        self.preview.setMinimumSize(900, 600)

        self.status = QLabel("Ready.")

        self.camera_index = QSpinBox()
        self.camera_index.setValue(0)

        self.width = QSpinBox()
        self.width.setRange(320, 4096)
        self.width.setValue(1280)

        self.height = QSpinBox()
        self.height.setRange(240, 2160)
        self.height.setValue(720)

        self.fps = QDoubleSpinBox()
        self.fps.setRange(0.1, 60.0)
        self.fps.setValue(1.0)
        self.fps.setSingleStep(0.5)

        self.rows = QSpinBox()
        self.rows.setRange(1, 32)
        self.rows.setValue(4)

        self.cols = QSpinBox()
        self.cols.setRange(1, 32)
        self.cols.setValue(6)

        self.plate_x = QSpinBox()
        self.plate_x.setRange(0, 4096)
        self.plate_x.setValue(0)

        self.plate_y = QSpinBox()
        self.plate_y.setRange(0, 4096)
        self.plate_y.setValue(0)

        self.plate_w = QSpinBox()
        self.plate_w.setRange(1, 4096)
        self.plate_w.setValue(1280)

        self.plate_h = QSpinBox()
        self.plate_h.setRange(1, 2160)
        self.plate_h.setValue(720)

        self.threshold = QSpinBox()
        self.threshold.setRange(1, 255)
        self.threshold.setValue(35)

        self.min_area = QSpinBox()
        self.min_area.setRange(1, 10000)
        self.min_area.setValue(5)

        self.max_area = QSpinBox()
        self.max_area.setRange(1, 100000)
        self.max_area.setValue(500)

        self.sleep_window = QSpinBox()
        self.sleep_window.setRange(1, 3600)
        self.sleep_window.setValue(300)

        self.sleep_threshold = QDoubleSpinBox()
        self.sleep_threshold.setRange(0.0, 10000.0)
        self.sleep_threshold.setValue(5.0)

        self.save_frames = QCheckBox("Save sampled frames")
        self.save_frames.setChecked(True)

        self.output_dir = QLineEdit(
            datetime.now().strftime("fly_experiment_%Y%m%d_%H%M%S")
        )

        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.choose_output_dir)

        self.start_button = QPushButton("Start Experiment")
        self.stop_button = QPushButton("Stop Experiment")

        self.start_button.clicked.connect(self.start_experiment)
        self.stop_button.clicked.connect(self.stop_experiment)

        self.stop_button.setEnabled(False)

        controls = QGridLayout()

        controls.addWidget(QLabel("Camera Index"), 0, 0)
        controls.addWidget(self.camera_index, 0, 1)

        controls.addWidget(QLabel("Camera Width"), 1, 0)
        controls.addWidget(self.width, 1, 1)

        controls.addWidget(QLabel("Camera Height"), 2, 0)
        controls.addWidget(self.height, 2, 1)

        controls.addWidget(QLabel("Frames/sec"), 3, 0)
        controls.addWidget(self.fps, 3, 1)

        controls.addWidget(QLabel("Well Rows"), 4, 0)
        controls.addWidget(self.rows, 4, 1)

        controls.addWidget(QLabel("Well Columns"), 5, 0)
        controls.addWidget(self.cols, 5, 1)

        controls.addWidget(QLabel("Plate X"), 6, 0)
        controls.addWidget(self.plate_x, 6, 1)

        controls.addWidget(QLabel("Plate Y"), 7, 0)
        controls.addWidget(self.plate_y, 7, 1)

        controls.addWidget(QLabel("Plate Width"), 8, 0)
        controls.addWidget(self.plate_w, 8, 1)

        controls.addWidget(QLabel("Plate Height"), 9, 0)
        controls.addWidget(self.plate_h, 9, 1)

        controls.addWidget(QLabel("Threshold"), 10, 0)
        controls.addWidget(self.threshold, 10, 1)

        controls.addWidget(QLabel("Min Fly Area"), 11, 0)
        controls.addWidget(self.min_area, 11, 1)

        controls.addWidget(QLabel("Max Fly Area"), 12, 0)
        controls.addWidget(self.max_area, 12, 1)

        controls.addWidget(QLabel("Sleep Window sec"), 13, 0)
        controls.addWidget(self.sleep_window, 13, 1)

        controls.addWidget(QLabel("Sleep Distance Threshold"), 14, 0)
        controls.addWidget(self.sleep_threshold, 14, 1)

        controls.addWidget(self.save_frames, 15, 0, 1, 2)

        controls.addWidget(QLabel("Output Folder"), 16, 0)
        controls.addWidget(self.output_dir, 16, 1)
        controls.addWidget(self.browse_button, 17, 0, 1, 2)

        controls.addWidget(self.start_button, 18, 0, 1, 2)
        controls.addWidget(self.stop_button, 19, 0, 1, 2)

        left_layout = QVBoxLayout()
        left_layout.addLayout(controls)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.preview)
        right_layout.addWidget(self.status)

        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)

        self.setLayout(main_layout)

    def choose_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")

        if folder:
            self.output_dir.setText(folder)

    def collect_settings(self):
        s = ExperimentSettings()

        s.camera_index = self.camera_index.value()
        s.width = self.width.value()
        s.height = self.height.value()

        s.frames_per_second = self.fps.value()

        s.rows = self.rows.value()
        s.cols = self.cols.value()

        s.plate_x = self.plate_x.value()
        s.plate_y = self.plate_y.value()
        s.plate_w = self.plate_w.value()
        s.plate_h = self.plate_h.value()

        s.threshold = self.threshold.value()
        s.min_area = self.min_area.value()
        s.max_area = self.max_area.value()

        s.sleep_window_sec = self.sleep_window.value()
        s.sleep_distance_threshold = self.sleep_threshold.value()

        s.save_frames = self.save_frames.isChecked()
        s.output_dir = self.output_dir.text()

        return s

    def start_experiment(self):
        settings = self.collect_settings()

        self.worker = ExperimentWorker(settings)
        self.worker.frame_ready.connect(self.update_preview)
        self.worker.status_ready.connect(self.update_status)
        self.worker.finished_ready.connect(self.experiment_finished)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self.worker.start()

    def stop_experiment(self):
        if self.worker:
            self.worker.stop()

        self.stop_button.setEnabled(False)

    def update_preview(self, qimg):
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self.preview.width(),
            self.preview.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        self.preview.setPixmap(scaled)

    def update_status(self, text):
        self.status.setText(text)

    def experiment_finished(self, text):
        self.status.setText(text)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
            self.worker.wait()

        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = FlyShakerGUI()
    gui.show()
    sys.exit(app.exec())