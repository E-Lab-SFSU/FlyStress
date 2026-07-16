import sys
import csv
import cv2
import math
import time
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QFileDialog, QCheckBox, QMessageBox, QGroupBox
)

try:
    from smbus2 import SMBus
except ImportError:
    SMBus = None


@dataclass
class MPUSettings:
    enabled: bool = True
    bus_number: int = 1
    address: int = 0x68
    sample_interval_ms: int = 50


class MPUReader(QThread):
    sample_ready = pyqtSignal(dict)
    status_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.running = False
        self.recording = False
        self.bus = None
        self.csv_file = None
        self.csv_writer = None

    def run(self):
        if SMBus is None:
            self.error_signal.emit("smbus2 is not installed.")
            return

        try:
            self.bus = SMBus(self.settings.bus_number)
            self.bus.write_byte_data(self.settings.address, 0x6B, 0x00)
            self.running = True
            self.status_signal.emit("MPU connected.")

            while self.running:
                sample = self.read_sample()
                self.sample_ready.emit(sample)

                if self.recording and self.csv_writer:
                    self.write_sample(sample)

                self.msleep(self.settings.sample_interval_ms)

        except Exception as e:
            self.error_signal.emit(f"MPU error: {e}")

        finally:
            self.close_csv()
            if self.bus:
                self.bus.close()
            self.status_signal.emit("MPU stopped.")

    def stop(self):
        self.running = False
        self.wait(2000)

    def read_word(self, reg):
        high = self.bus.read_byte_data(self.settings.address, reg)
        low = self.bus.read_byte_data(self.settings.address, reg + 1)
        value = (high << 8) + low
        if value >= 0x8000:
            value = -((65535 - value) + 1)
        return value

    def read_sample(self):
        ax_raw = self.read_word(0x3B)
        ay_raw = self.read_word(0x3D)
        az_raw = self.read_word(0x3F)

        gx_raw = self.read_word(0x43)
        gy_raw = self.read_word(0x45)
        gz_raw = self.read_word(0x47)

        ax = ax_raw / 16384.0
        ay = ay_raw / 16384.0
        az = az_raw / 16384.0

        gx = gx_raw / 131.0
        gy = gy_raw / 131.0
        gz = gz_raw / 131.0

        x_rot = math.degrees(math.atan2(ay, math.sqrt(ax * ax + az * az)))
        y_rot = -math.degrees(math.atan2(ax, math.sqrt(ay * ay + az * az)))

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            "accel_x_g": ax,
            "accel_y_g": ay,
            "accel_z_g": az,
            "gyro_x_deg_s": gx,
            "gyro_y_deg_s": gy,
            "gyro_z_deg_s": gz,
            "x_rotation": x_rot,
            "y_rotation": y_rot,
        }

    def start_recording(self, path):
        self.close_csv()
        self.csv_file = open(path, "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)

        self.csv_writer.writerow([
            "timestamp",
            "accel_x_g", "accel_y_g", "accel_z_g",
            "gyro_x_deg_s", "gyro_y_deg_s", "gyro_z_deg_s",
            "x_rotation", "y_rotation"
        ])

        self.recording = True
        self.status_signal.emit(f"MPU recording: {path}")

    def write_sample(self, s):
        self.csv_writer.writerow([
            s["timestamp"],
            s["accel_x_g"], s["accel_y_g"], s["accel_z_g"],
            s["gyro_x_deg_s"], s["gyro_y_deg_s"], s["gyro_z_deg_s"],
            s["x_rotation"], s["y_rotation"]
        ])
        self.csv_file.flush()

    def stop_recording(self):
        self.recording = False
        self.close_csv()

    def close_csv(self):
        if self.csv_file:
            self.csv_file.close()
        self.csv_file = None
        self.csv_writer = None
        self.recording = False


class ExperimentGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Simple Experiment Camera + MPU Recorder")
        self.setGeometry(100, 100, 850, 650)

        self.output_folder = Path.cwd()
        self.cap = None
        self.video_writer = None
        self.recording = False

        self.mpu_settings = MPUSettings()
        self.mpu_reader = None

        self.camera_timer = QTimer()
        self.camera_timer.timeout.connect(self.update_camera_frame)

        self.experiment_timer = QTimer()
        self.experiment_timer.setSingleShot(True)
        self.experiment_timer.timeout.connect(self.stop_experiment)

        self.build_ui()

    def build_ui(self):
        main = QVBoxLayout()

        title = QLabel("Experiment Camera + MPU Recorder")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        main.addWidget(title)

        self.preview_label = QLabel("Camera Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(640, 360)
        self.preview_label.setStyleSheet("background-color: black; color: white;")
        main.addWidget(self.preview_label)

        exp_group = QGroupBox("Experiment Settings")
        exp_layout = QVBoxLayout()

        self.name_input = QLineEdit("experiment")
        self.duration_input = QLineEdit("10")

        exp_layout.addWidget(QLabel("Experiment Name:"))
        exp_layout.addWidget(self.name_input)
        exp_layout.addWidget(QLabel("Duration seconds:"))
        exp_layout.addWidget(self.duration_input)

        exp_group.setLayout(exp_layout)
        main.addWidget(exp_group)

        cam_group = QGroupBox("Camera Settings")
        cam_layout = QVBoxLayout()

        self.enable_camera_checkbox = QCheckBox("Enable camera recording")
        self.enable_camera_checkbox.setChecked(True)

        self.camera_index_input = QLineEdit("0")
        self.fps_input = QLineEdit("20")
        self.width_input = QLineEdit("640")
        self.height_input = QLineEdit("480")

        cam_layout.addWidget(self.enable_camera_checkbox)
        cam_layout.addWidget(QLabel("Camera Index:"))
        cam_layout.addWidget(self.camera_index_input)
        cam_layout.addWidget(QLabel("FPS:"))
        cam_layout.addWidget(self.fps_input)
        cam_layout.addWidget(QLabel("Width:"))
        cam_layout.addWidget(self.width_input)
        cam_layout.addWidget(QLabel("Height:"))
        cam_layout.addWidget(self.height_input)

        cam_group.setLayout(cam_layout)
        main.addWidget(cam_group)

        mpu_group = QGroupBox("MPU6050 Settings")
        mpu_layout = QVBoxLayout()

        self.enable_mpu_checkbox = QCheckBox("Enable MPU6050 recording")
        self.enable_mpu_checkbox.setChecked(True)

        self.sample_interval_input = QLineEdit("50")

        mpu_layout.addWidget(self.enable_mpu_checkbox)
        mpu_layout.addWidget(QLabel("MPU Sample Interval ms:"))
        mpu_layout.addWidget(self.sample_interval_input)

        mpu_group.setLayout(mpu_layout)
        main.addWidget(mpu_group)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel(f"Output Folder: {self.output_folder}")
        choose_button = QPushButton("Choose Folder")
        choose_button.clicked.connect(self.choose_folder)

        folder_row.addWidget(self.folder_label)
        folder_row.addWidget(choose_button)
        main.addLayout(folder_row)

        button_row = QHBoxLayout()

        self.start_preview_button = QPushButton("Start Preview")
        self.stop_preview_button = QPushButton("Stop Preview")
        self.start_button = QPushButton("Start Experiment")
        self.stop_button = QPushButton("Stop Experiment")

        self.start_preview_button.clicked.connect(self.start_preview)
        self.stop_preview_button.clicked.connect(self.stop_preview)
        self.start_button.clicked.connect(self.start_experiment)
        self.stop_button.clicked.connect(self.stop_experiment)

        button_row.addWidget(self.start_preview_button)
        button_row.addWidget(self.stop_preview_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)

        main.addLayout(button_row)

        self.status_label = QLabel("Status: Ready")
        main.addWidget(self.status_label)

        self.mpu_data_label = QLabel("MPU: --")
        main.addWidget(self.mpu_data_label)

        self.setLayout(main)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder", str(self.output_folder))
        if folder:
            self.output_folder = Path(folder)
            self.folder_label.setText(f"Output Folder: {self.output_folder}")

    def start_preview(self):
        if self.cap and self.cap.isOpened():
            return

        try:
            camera_index = int(self.camera_index_input.text())
            width = int(self.width_input.text())
            height = int(self.height_input.text())

            self.cap = cv2.VideoCapture(camera_index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

            if not self.cap.isOpened():
                raise RuntimeError("Could not open camera.")

            self.camera_timer.start(30)
            self.status_label.setText("Status: Camera preview running.")

        except Exception as e:
            QMessageBox.warning(self, "Camera Error", str(e))

    def stop_preview(self):
        self.camera_timer.stop()

        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None

        if self.cap:
            self.cap.release()
            self.cap = None

        self.preview_label.setText("Camera Preview")
        self.status_label.setText("Status: Camera stopped.")

    def update_camera_frame(self):
        if not self.cap:
            return

        ret, frame = self.cap.read()
        if not ret:
            return

        if self.recording and self.video_writer:
            self.video_writer.write(frame)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.preview_label.setPixmap(
            pixmap.scaled(
                self.preview_label.width(),
                self.preview_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio
            )
        )

    def start_mpu(self):
        if not self.enable_mpu_checkbox.isChecked():
            return

        try:
            self.mpu_settings.sample_interval_ms = int(self.sample_interval_input.text())
        except ValueError:
            self.mpu_settings.sample_interval_ms = 50
            self.sample_interval_input.setText("50")

        if self.mpu_reader and self.mpu_reader.isRunning():
            return

        self.mpu_reader = MPUReader(self.mpu_settings)
        self.mpu_reader.sample_ready.connect(self.update_mpu_display)
        self.mpu_reader.status_signal.connect(self.update_status)
        self.mpu_reader.error_signal.connect(self.show_error)
        self.mpu_reader.start()

    def update_mpu_display(self, s):
        self.mpu_data_label.setText(
            f"MPU: Accel X={s['accel_x_g']:.3f}g, "
            f"Y={s['accel_y_g']:.3f}g, "
            f"Z={s['accel_z_g']:.3f}g | "
            f"Gyro X={s['gyro_x_deg_s']:.2f}, "
            f"Y={s['gyro_y_deg_s']:.2f}, "
            f"Z={s['gyro_z_deg_s']:.2f}"
        )

    def start_experiment(self):
        try:
            duration = float(self.duration_input.text())
            fps = float(self.fps_input.text())
            width = int(self.width_input.text())
            height = int(self.height_input.text())
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Check duration, FPS, width, and height.")
            return

        self.output_folder.mkdir(parents=True, exist_ok=True)

        name = self.name_input.text().strip() or "experiment"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{name}_{timestamp}"

        if self.enable_camera_checkbox.isChecked():
            self.start_preview()

            video_path = self.output_folder / f"{base}.avi"
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            self.video_writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))

        self.start_mpu()

        if self.enable_mpu_checkbox.isChecked() and self.mpu_reader:
            csv_path = self.output_folder / f"{base}_mpu.csv"
            self.mpu_reader.start_recording(str(csv_path))

        self.recording = True
        self.experiment_timer.start(int(duration * 1000))
        self.status_label.setText("Status: Experiment running.")

    def stop_experiment(self):
        self.recording = False

        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None

        if self.mpu_reader:
            self.mpu_reader.stop_recording()

        self.status_label.setText("Status: Experiment stopped and files saved.")

    def update_status(self, msg):
        self.status_label.setText(f"Status: {msg}")

    def show_error(self, msg):
        self.status_label.setText(f"Status: {msg}")
        QMessageBox.warning(self, "Error", msg)

    def closeEvent(self, event):
        self.recording = False
        self.stop_preview()

        if self.mpu_reader:
            self.mpu_reader.stop()

        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = ExperimentGUI()
    gui.show()
    sys.exit(app.exec())