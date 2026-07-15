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
    QLineEdit, QCheckBox, QFileDialog, QComboBox
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

        self.foreground_mode = "dark"
        self.threshold = 35

        self.min_area = 5
        self.max_area = 500
        self.min_width = 1
        self.max_width = 100
        self.min_height = 1
        self.max_height = 100
        self.min_aspect_ratio = 0.2
        self.max_aspect_ratio = 5.0
        self.min_solidity = 0.0
        self.max_circularity = 1.2

        self.sleep_window_sec = 300
        self.sleep_distance_threshold = 5.0

        self.save_frames = True
        self.output_dir = "fly_experiment"


class VisionTools:
    @staticmethod
    def generate_well_rois(settings):
        rois = []
        well_w = settings.plate_w / settings.cols
        well_h = settings.plate_h / settings.rows
        well_id = 0

        for r in range(settings.rows):
            for c in range(settings.cols):
                x = int(settings.plate_x + c * well_w)
                y = int(settings.plate_y + r * well_h)
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

    @staticmethod
    def create_foreground_mask(gray, background, settings):
        if settings.foreground_mode == "dark":
            fg = cv2.subtract(background, gray)
        elif settings.foreground_mode == "bright":
            fg = cv2.subtract(gray, background)
        else:
            fg = cv2.absdiff(gray, background)

        fg = cv2.GaussianBlur(fg, (5, 5), 0)

        _, mask = cv2.threshold(
            fg,
            settings.threshold,
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

    @staticmethod
    def contour_features(contour, roi):
        x, y, _, _ = roi
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        bx, by, bw, bh = cv2.boundingRect(contour)

        if area <= 0 or bw <= 0 or bh <= 0:
            return None

        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            return None

        cx = int(moments["m10"] / moments["m00"]) + x
        cy = int(moments["m01"] / moments["m00"]) + y

        aspect_ratio = bw / bh

        if perimeter > 0:
            circularity = (4 * math.pi * area) / (perimeter * perimeter)
        else:
            circularity = 0.0

        hull = cv2.convexHull(contour)
        hull_area = cv2.contourArea(hull)
        solidity = area / hull_area if hull_area > 0 else 0.0

        return {
            "centroid": (cx, cy),
            "area": float(area),
            "perimeter": float(perimeter),
            "width": int(bw),
            "height": int(bh),
            "aspect_ratio": float(aspect_ratio),
            "circularity": float(circularity),
            "solidity": float(solidity)
        }

    @staticmethod
    def passes_filters(features, settings):
        if features["area"] < settings.min_area or features["area"] > settings.max_area:
            return False
        if features["width"] < settings.min_width or features["width"] > settings.max_width:
            return False
        if features["height"] < settings.min_height or features["height"] > settings.max_height:
            return False
        if features["aspect_ratio"] < settings.min_aspect_ratio:
            return False
        if features["aspect_ratio"] > settings.max_aspect_ratio:
            return False
        if features["solidity"] < settings.min_solidity:
            return False
        if features["circularity"] > settings.max_circularity:
            return False
        return True

    @staticmethod
    def detect_fly(mask, roi, settings):
        x, y, w, h = roi
        well_mask = mask[y:y + h, x:x + w]

        contours, _ = cv2.findContours(
            well_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        best = None
        best_area = 0

        for contour in contours:
            features = VisionTools.contour_features(contour, roi)
            if features is None:
                continue

            if not VisionTools.passes_filters(features, settings):
                continue

            if features["area"] > best_area:
                best = features
                best_area = features["area"]

        return best

    @staticmethod
    def draw_overlay(frame, rois, detections):
        output = frame.copy()

        for well in rois:
            well_id = well["well_id"]
            x, y, w, h = well["roi"]

            cv2.rectangle(output, (x, y), (x + w, y + h), (120, 120, 120), 1)
            cv2.putText(output, str(well_id), (x + 5, y + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

            det = detections.get(well_id)
            if det and det.get("centroid"):
                cx, cy = det["centroid"]
                state = det.get("sleep_state", "")
                cv2.circle(output, (cx, cy), 5, (0, 255, 0), -1)
                cv2.putText(output, state, (x + 5, y + h - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        return output

    @staticmethod
    def cv_to_qimage(frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        return QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()


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

        rois = VisionTools.generate_well_rois(s)
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
                "frame_number", "elapsed_sec", "well_id", "row", "col",
                "fly_x", "fly_y", "area", "perimeter", "width", "height",
                "aspect_ratio", "circularity", "solidity",
                "movement_distance", "rolling_distance", "sleep_state", "detection_status"
            ])

            index_writer.writerow(["frame_number", "timestamp_unix", "elapsed_sec", "filename"])

            while self.running:
                ret, frame = cap.read()
                if not ret:
                    self.status_ready.emit("WARNING: Camera frame failed.")
                    self.msleep(10)
                    continue

                now = time.time()
                if now >= next_capture_time:
                    elapsed = now - start_time
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    display_frame = frame.copy()

                    if self.background is None:
                        self.bg_frames.append(gray)
                        self.status_ready.emit(f"Building background: {len(self.bg_frames)}/{self.bg_sample_count}")

                        if len(self.bg_frames) >= self.bg_sample_count:
                            self.background = np.median(np.stack(self.bg_frames, axis=0), axis=0).astype(np.uint8)
                            self.status_ready.emit("Background ready. Detecting flies.")

                        display_frame = VisionTools.draw_overlay(frame, rois, {})

                    else:
                        mask = VisionTools.create_foreground_mask(gray, self.background, s)
                        detections = {}

                        for well in rois:
                            well_id = well["well_id"]
                            state = states[well_id]
                            detection = VisionTools.detect_fly(mask, well["roi"], s)

                            centroid = None
                            status = "NO_OBJECT"
                            values = {
                                "area": "", "perimeter": "", "width": "", "height": "",
                                "aspect_ratio": "", "circularity": "", "solidity": ""
                            }

                            if detection is not None:
                                centroid = detection["centroid"]
                                status = "OK"
                                values = detection

                            movement, rolling, sleep_state = self.update_motion_state(state, centroid, elapsed, s)
                            fly_x, fly_y = ("", "") if centroid is None else centroid

                            result_writer.writerow([
                                frame_number, elapsed, well_id, well["row"], well["col"],
                                fly_x, fly_y,
                                values["area"], values["perimeter"], values["width"], values["height"],
                                values["aspect_ratio"], values["circularity"], values["solidity"],
                                movement, rolling, sleep_state, status
                            ])

                            detections[well_id] = {
                                "centroid": centroid,
                                "sleep_state": sleep_state,
                                "status": status
                            }

                        display_frame = VisionTools.draw_overlay(frame, rois, detections)

                    if s.save_frames:
                        filename = f"frame_{frame_number:06d}.jpg"
                        frame_path = frames_dir / filename
                        cv2.imwrite(str(frame_path), gray, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        index_writer.writerow([frame_number, now, elapsed, filename])

                    frame_number += 1
                    next_capture_time += interval
                    self.frame_ready.emit(VisionTools.cv_to_qimage(display_frame))

                self.msleep(1)

        cap.release()
        self.finished_ready.emit(f"Experiment stopped. Data saved to: {output_root}")

    def update_motion_state(self, state, position, elapsed, settings):
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
            if elapsed - old_time <= settings.sleep_window_sec:
                break
            state.movement_window.popleft()
            state.total_window_distance -= old_dist

        if elapsed < settings.sleep_window_sec:
            state.sleep_state = "UNKNOWN"
        elif state.total_window_distance <= settings.sleep_distance_threshold:
            state.sleep_state = "SLEEP"
        else:
            state.sleep_state = "AWAKE"

        return movement, state.total_window_distance, state.sleep_state


class FlyShakerGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None

        self.setWindowTitle("Fly Shaker Motion Detection GUI")
        self.setMinimumSize(1300, 850)

        self.preview = QLabel("Camera preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background-color: black; color: white;")
        self.preview.setMinimumSize(900, 650)

        self.status = QLabel("Ready.")

        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 20)
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

        self.mode = QComboBox()
        self.mode.addItems(["dark", "bright", "absdiff"])

        self.threshold = QSpinBox()
        self.threshold.setRange(1, 255)
        self.threshold.setValue(35)

        self.min_area = QSpinBox()
        self.min_area.setRange(1, 100000)
        self.min_area.setValue(5)

        self.max_area = QSpinBox()
        self.max_area.setRange(1, 100000)
        self.max_area.setValue(500)

        self.min_width = QSpinBox()
        self.min_width.setRange(1, 1000)
        self.min_width.setValue(1)

        self.max_width = QSpinBox()
        self.max_width.setRange(1, 1000)
        self.max_width.setValue(100)

        self.min_height = QSpinBox()
        self.min_height.setRange(1, 1000)
        self.min_height.setValue(1)

        self.max_height = QSpinBox()
        self.max_height.setRange(1, 1000)
        self.max_height.setValue(100)

        self.min_aspect = QDoubleSpinBox()
        self.min_aspect.setRange(0.01, 100.0)
        self.min_aspect.setValue(0.2)
        self.min_aspect.setSingleStep(0.1)

        self.max_aspect = QDoubleSpinBox()
        self.max_aspect.setRange(0.01, 100.0)
        self.max_aspect.setValue(5.0)
        self.max_aspect.setSingleStep(0.1)

        self.min_solidity = QDoubleSpinBox()
        self.min_solidity.setRange(0.0, 1.0)
        self.min_solidity.setValue(0.0)
        self.min_solidity.setSingleStep(0.05)

        self.max_circularity = QDoubleSpinBox()
        self.max_circularity.setRange(0.0, 2.0)
        self.max_circularity.setValue(1.2)
        self.max_circularity.setSingleStep(0.05)

        self.sleep_window = QSpinBox()
        self.sleep_window.setRange(1, 3600)
        self.sleep_window.setValue(300)

        self.sleep_threshold = QDoubleSpinBox()
        self.sleep_threshold.setRange(0.0, 10000.0)
        self.sleep_threshold.setValue(5.0)

        self.save_frames = QCheckBox("Save sampled frames")
        self.save_frames.setChecked(True)

        self.output_dir = QLineEdit(datetime.now().strftime("fly_experiment_%Y%m%d_%H%M%S"))

        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.choose_output_dir)

        self.calibrate_button = QPushButton("Calibrate Detection")
        self.calibrate_button.clicked.connect(self.calibrate_detection)

        self.start_button = QPushButton("Start Experiment")
        self.stop_button = QPushButton("Stop Experiment")
        self.start_button.clicked.connect(self.start_experiment)
        self.stop_button.clicked.connect(self.stop_experiment)
        self.stop_button.setEnabled(False)

        controls = QGridLayout()
        row = 0
        for label, widget in [
            ("Camera Index", self.camera_index),
            ("Camera Width", self.width),
            ("Camera Height", self.height),
            ("Frames/sec", self.fps),
            ("Well Rows", self.rows),
            ("Well Columns", self.cols),
            ("Plate X", self.plate_x),
            ("Plate Y", self.plate_y),
            ("Plate Width", self.plate_w),
            ("Plate Height", self.plate_h),
            ("Foreground Mode", self.mode),
            ("Threshold", self.threshold),
            ("Min Area", self.min_area),
            ("Max Area", self.max_area),
            ("Min Width", self.min_width),
            ("Max Width", self.max_width),
            ("Min Height", self.min_height),
            ("Max Height", self.max_height),
            ("Min Aspect", self.min_aspect),
            ("Max Aspect", self.max_aspect),
            ("Min Solidity", self.min_solidity),
            ("Max Circularity", self.max_circularity),
            ("Sleep Window sec", self.sleep_window),
            ("Sleep Distance Threshold", self.sleep_threshold),
        ]:
            controls.addWidget(QLabel(label), row, 0)
            controls.addWidget(widget, row, 1)
            row += 1

        controls.addWidget(self.save_frames, row, 0, 1, 2)
        row += 1
        controls.addWidget(QLabel("Output Folder"), row, 0)
        controls.addWidget(self.output_dir, row, 1)
        row += 1
        controls.addWidget(self.browse_button, row, 0, 1, 2)
        row += 1
        controls.addWidget(self.calibrate_button, row, 0, 1, 2)
        row += 1
        controls.addWidget(self.start_button, row, 0, 1, 2)
        row += 1
        controls.addWidget(self.stop_button, row, 0, 1, 2)

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
        s.foreground_mode = self.mode.currentText()
        s.threshold = self.threshold.value()
        s.min_area = self.min_area.value()
        s.max_area = self.max_area.value()
        s.min_width = self.min_width.value()
        s.max_width = self.max_width.value()
        s.min_height = self.min_height.value()
        s.max_height = self.max_height.value()
        s.min_aspect_ratio = self.min_aspect.value()
        s.max_aspect_ratio = self.max_aspect.value()
        s.min_solidity = self.min_solidity.value()
        s.max_circularity = self.max_circularity.value()
        s.sleep_window_sec = self.sleep_window.value()
        s.sleep_distance_threshold = self.sleep_threshold.value()
        s.save_frames = self.save_frames.isChecked()
        s.output_dir = self.output_dir.text()
        return s

    def calibrate_detection(self):
        s = self.collect_settings()
        cap = cv2.VideoCapture(s.camera_index)

        if not cap.isOpened():
            self.status.setText("ERROR: Could not open camera for calibration.")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, s.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s.height)

        frames = []
        for i in range(30):
            ret, frame = cap.read()
            if ret:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frames.append(gray)
            cv2.waitKey(20)

        cap.release()

        if len(frames) < 5:
            self.status.setText("ERROR: Not enough frames for calibration.")
            return

        background = np.median(np.stack(frames, axis=0), axis=0).astype(np.uint8)
        test_frame = frames[-1]
        mask = VisionTools.create_foreground_mask(test_frame, background, s)
        rois = VisionTools.generate_well_rois(s)

        found = []
        detections = {}

        for well in rois:
            x, y, w, h = well["roi"]
            well_mask = mask[y:y + h, x:x + w]
            contours, _ = cv2.findContours(well_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            candidates = []
            for contour in contours:
                features = VisionTools.contour_features(contour, well["roi"])
                if features is not None:
                    candidates.append(features)
                    found.append(features)

            if candidates:
                best = max(candidates, key=lambda item: item["area"])
                detections[well["well_id"]] = {"centroid": best["centroid"], "sleep_state": "CAL"}

        if not found:
            self.status.setText("Calibration found no objects. Try lowering threshold or use absdiff mode.")
            return

        areas = np.array([f["area"] for f in found])
        widths = np.array([f["width"] for f in found])
        heights = np.array([f["height"] for f in found])
        aspects = np.array([f["aspect_ratio"] for f in found])
        solidities = np.array([f["solidity"] for f in found])
        circularities = np.array([f["circularity"] for f in found])

        self.min_area.setValue(max(1, int(np.percentile(areas, 10) * 0.75)))
        self.max_area.setValue(max(2, int(np.percentile(areas, 90) * 1.25)))
        self.min_width.setValue(max(1, int(np.percentile(widths, 10) * 0.75)))
        self.max_width.setValue(max(2, int(np.percentile(widths, 90) * 1.25)))
        self.min_height.setValue(max(1, int(np.percentile(heights, 10) * 0.75)))
        self.max_height.setValue(max(2, int(np.percentile(heights, 90) * 1.25)))
        self.min_aspect.setValue(max(0.01, float(np.percentile(aspects, 10) * 0.75)))
        self.max_aspect.setValue(max(0.02, float(np.percentile(aspects, 90) * 1.25)))
        self.min_solidity.setValue(max(0.0, float(np.percentile(solidities, 10) * 0.75)))
        self.max_circularity.setValue(min(2.0, float(np.percentile(circularities, 90) * 1.25)))

        preview_frame = cv2.cvtColor(test_frame, cv2.COLOR_GRAY2BGR)
        overlay = VisionTools.draw_overlay(preview_frame, rois, detections)
        self.update_preview(VisionTools.cv_to_qimage(overlay))

        self.status.setText(
            f"Calibration complete. Objects: {len(found)} | "
            f"Avg area: {np.mean(areas):.1f} | "
            f"Area range set: {self.min_area.value()}-{self.max_area.value()}"
        )

    def start_experiment(self):
        settings = self.collect_settings()
        self.worker = ExperimentWorker(settings)
        self.worker.frame_ready.connect(self.update_preview)
        self.worker.status_ready.connect(self.update_status)
        self.worker.finished_ready.connect(self.experiment_finished)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.calibrate_button.setEnabled(False)
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
        self.calibrate_button.setEnabled(True)

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