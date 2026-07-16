"""
PyQt6 MPU6050 Experiment GUI
Author: Cherese Jordan + ChatGPT

Purpose:
- Read MPU6050 accelerometer and gyroscope data
- Display live MPU values in a PyQt6 GUI
- Start/stop experiments
- Save MPU data only during the experiment/shake recording window
- Later, the shake protocol can be inserted where marked

Install:
    pip install PyQt6 smbus2

Run on Raspberry Pi:
    python3 mpu_experiment_gui.py
"""

import sys
import csv
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from smbus2 import SMBus

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QFileDialog,
    QCheckBox,
    QMessageBox,
    QGroupBox,
)


# -----------------------------
# MPU SETTINGS
# -----------------------------

@dataclass
class MPUSettings:
    enabled: bool = True
    bus_number: int = 1
    address: int = 0x68
    sample_interval_ms: int = 50
    show_live_window: bool = True
    save_only_during_shake: bool = True


# -----------------------------
# MPU READER THREAD
# -----------------------------

class MPUReader(QThread):
    sample_ready = pyqtSignal(dict)
    error_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    POWER_MGMT_1 = 0x6B
    POWER_MGMT_2 = 0x6C

    def __init__(self, settings: MPUSettings):
        super().__init__()
        self.settings = settings
        self.bus = None
        self.running = False

        self.recording = False
        self.csv_file = None
        self.csv_writer = None
        self.csv_path = None

    def run(self):
        if not self.settings.enabled:
            self.status_signal.emit("MPU disabled.")
            return

        try:
            self.bus = SMBus(self.settings.bus_number)
            time.sleep(1)

            self.bus.write_byte_data(
                self.settings.address,
                self.POWER_MGMT_1,
                0x00
            )

            self.running = True
            self.status_signal.emit("MPU connected and streaming.")

            while self.running:
                sample = self.read_sample()

                if sample:
                    self.sample_ready.emit(sample)

                    if self.recording and self.csv_writer:
                        self.write_sample(sample)

                self.msleep(self.settings.sample_interval_ms)

        except Exception as e:
            self.error_signal.emit(f"MPU error: {e}")

        finally:
            self.close_csv()

            if self.bus:
                try:
                    self.bus.close()
                except Exception:
                    pass

            self.status_signal.emit("MPU stopped.")

    def stop(self):
        self.running = False
        self.wait(2000)

    def read_word(self, register):
        high = self.bus.read_byte_data(self.settings.address, register)
        low = self.bus.read_byte_data(self.settings.address, register + 1)
        value = (high << 8) + low
        return value

    def read_word_2c(self, register):
        value = self.read_word(register)

        if value >= 0x8000:
            return -((65535 - value) + 1)

        return value

    @staticmethod
    def dist(a, b):
        return math.sqrt((a * a) + (b * b))

    def get_y_rotation(self, x, y, z):
        radians = math.atan2(x, self.dist(y, z))
        return -math.degrees(radians)

    def get_x_rotation(self, x, y, z):
        radians = math.atan2(y, self.dist(x, z))
        return math.degrees(radians)

    def read_sample(self):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

            gyro_x_raw = self.read_word_2c(0x43)
            gyro_y_raw = self.read_word_2c(0x45)
            gyro_z_raw = self.read_word_2c(0x47)

            gyro_x_deg_s = gyro_x_raw / 131.0
            gyro_y_deg_s = gyro_y_raw / 131.0
            gyro_z_deg_s = gyro_z_raw / 131.0

            accel_x_raw = self.read_word_2c(0x3B)
            accel_y_raw = self.read_word_2c(0x3D)
            accel_z_raw = self.read_word_2c(0x3F)

            accel_x_g = accel_x_raw / 16384.0
            accel_y_g = accel_y_raw / 16384.0
            accel_z_g = accel_z_raw / 16384.0

            x_rotation = self.get_x_rotation(accel_x_g, accel_y_g, accel_z_g)
            y_rotation = self.get_y_rotation(accel_x_g, accel_y_g, accel_z_g)

            return {
                "timestamp": timestamp,
                "accel_x_raw": accel_x_raw,
                "accel_y_raw": accel_y_raw,
                "accel_z_raw": accel_z_raw,
                "accel_x_g": accel_x_g,
                "accel_y_g": accel_y_g,
                "accel_z_g": accel_z_g,
                "gyro_x_raw": gyro_x_raw,
                "gyro_y_raw": gyro_y_raw,
                "gyro_z_raw": gyro_z_raw,
                "gyro_x_deg_s": gyro_x_deg_s,
                "gyro_y_deg_s": gyro_y_deg_s,
                "gyro_z_deg_s": gyro_z_deg_s,
                "x_rotation": x_rotation,
                "y_rotation": y_rotation,
            }

        except Exception as e:
            self.error_signal.emit(f"MPU read error: {e}")
            return None

    def start_recording(self, csv_path):
        try:
            self.close_csv()

            self.csv_path = csv_path
            self.csv_file = open(csv_path, "w", newline="")
            self.csv_writer = csv.writer(self.csv_file)

            self.csv_writer.writerow([
                "timestamp",
                "accel_x_raw",
                "accel_y_raw",
                "accel_z_raw",
                "accel_x_g",
                "accel_y_g",
                "accel_z_g",
                "gyro_x_raw",
                "gyro_y_raw",
                "gyro_z_raw",
                "gyro_x_deg_s",
                "gyro_y_deg_s",
                "gyro_z_deg_s",
                "x_rotation",
                "y_rotation",
            ])

            self.recording = True
            self.status_signal.emit(f"MPU recording started: {csv_path}")

        except Exception as e:
            self.error_signal.emit(f"Could not start recording: {e}")

    def stop_recording(self):
        self.recording = False
        self.close_csv()
        self.status_signal.emit("MPU recording stopped.")

    def write_sample(self, sample):
        self.csv_writer.writerow([
            sample["timestamp"],
            sample["accel_x_raw"],
            sample["accel_y_raw"],
            sample["accel_z_raw"],
            sample["accel_x_g"],
            sample["accel_y_g"],
            sample["accel_z_g"],
            sample["gyro_x_raw"],
            sample["gyro_y_raw"],
            sample["gyro_z_raw"],
            sample["gyro_x_deg_s"],
            sample["gyro_y_deg_s"],
            sample["gyro_z_deg_s"],
            sample["x_rotation"],
            sample["y_rotation"],
        ])

        self.csv_file.flush()

    def close_csv(self):
        if self.csv_file:
            try:
                self.csv_file.close()
            except Exception:
                pass

        self.csv_file = None
        self.csv_writer = None
        self.csv_path = None
        self.recording = False


# -----------------------------
# LIVE MPU DISPLAY WINDOW
# -----------------------------

class MPULiveDisplay(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("MPU Live Data Stream")
        self.setGeometry(200, 200, 450, 350)

        layout = QVBoxLayout()

        self.status_label = QLabel("Waiting for MPU data...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.accel_label = QLabel("Accelerometer: --")
        self.gyro_label = QLabel("Gyroscope: --")
        self.rotation_label = QLabel("Rotation: --")
        self.timestamp_label = QLabel("Timestamp: --")

        for label in [
            self.status_label,
            self.accel_label,
            self.gyro_label,
            self.rotation_label,
            self.timestamp_label,
        ]:
            label.setStyleSheet("font-size: 14px;")
            layout.addWidget(label)

        self.setLayout(layout)

    def update_sample(self, sample):
        self.timestamp_label.setText(f"Timestamp: {sample['timestamp']}")

        self.accel_label.setText(
            "Accelerometer\n"
            f"X: {sample['accel_x_g']:.4f} g\n"
            f"Y: {sample['accel_y_g']:.4f} g\n"
            f"Z: {sample['accel_z_g']:.4f} g"
        )

        self.gyro_label.setText(
            "Gyroscope\n"
            f"X: {sample['gyro_x_deg_s']:.4f} deg/s\n"
            f"Y: {sample['gyro_y_deg_s']:.4f} deg/s\n"
            f"Z: {sample['gyro_z_deg_s']:.4f} deg/s"
        )

        self.rotation_label.setText(
            "Rotation\n"
            f"X Rotation: {sample['x_rotation']:.2f}\n"
            f"Y Rotation: {sample['y_rotation']:.2f}"
        )

        self.status_label.setText("MPU streaming")


# -----------------------------
# MAIN EXPERIMENT GUI
# -----------------------------

class ExperimentGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Experiment Controller with MPU6050")
        self.setGeometry(100, 100, 600, 400)

        self.settings = MPUSettings()
        self.mpu_reader = None
        self.live_display = MPULiveDisplay()

        self.experiment_running = False
        self.output_folder = Path.cwd()

        self.build_ui()

    def build_ui(self):
        main_layout = QVBoxLayout()

        title = QLabel("Experiment Controller")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        main_layout.addWidget(title)

        settings_group = QGroupBox("MPU Settings")
        settings_layout = QVBoxLayout()

        self.enable_mpu_checkbox = QCheckBox("Enable MPU6050")
        self.enable_mpu_checkbox.setChecked(True)

        self.show_live_checkbox = QCheckBox("Show live MPU display during experiment")
        self.show_live_checkbox.setChecked(True)

        self.sample_interval_input = QLineEdit("50")
        self.sample_interval_input.setPlaceholderText("Sample interval in ms")

        settings_layout.addWidget(self.enable_mpu_checkbox)
        settings_layout.addWidget(self.show_live_checkbox)
        settings_layout.addWidget(QLabel("Sample Interval ms:"))
        settings_layout.addWidget(self.sample_interval_input)

        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)

        folder_layout = QHBoxLayout()
        self.folder_label = QLabel(f"Output Folder: {self.output_folder}")
        self.choose_folder_button = QPushButton("Choose Folder")
        self.choose_folder_button.clicked.connect(self.choose_output_folder)

        folder_layout.addWidget(self.folder_label)
        folder_layout.addWidget(self.choose_folder_button)
        main_layout.addLayout(folder_layout)

        button_layout = QHBoxLayout()

        self.start_stream_button = QPushButton("Start MPU Stream")
        self.stop_stream_button = QPushButton("Stop MPU Stream")
        self.start_experiment_button = QPushButton("Start Experiment")
        self.stop_experiment_button = QPushButton("Stop Experiment")

        self.start_stream_button.clicked.connect(self.start_mpu_stream)
        self.stop_stream_button.clicked.connect(self.stop_mpu_stream)
        self.start_experiment_button.clicked.connect(self.start_experiment)
        self.stop_experiment_button.clicked.connect(self.stop_experiment)

        button_layout.addWidget(self.start_stream_button)
        button_layout.addWidget(self.stop_stream_button)
        button_layout.addWidget(self.start_experiment_button)
        button_layout.addWidget(self.stop_experiment_button)

        main_layout.addLayout(button_layout)

        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("font-size: 14px;")
        main_layout.addWidget(self.status_label)

        self.latest_data_label = QLabel("Latest MPU Data: --")
        self.latest_data_label.setStyleSheet("font-size: 13px;")
        main_layout.addWidget(self.latest_data_label)

        self.setLayout(main_layout)

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Output Folder",
            str(self.output_folder)
        )

        if folder:
            self.output_folder = Path(folder)
            self.folder_label.setText(f"Output Folder: {self.output_folder}")

    def update_settings_from_gui(self):
        self.settings.enabled = self.enable_mpu_checkbox.isChecked()
        self.settings.show_live_window = self.show_live_checkbox.isChecked()

        try:
            interval = int(self.sample_interval_input.text())
            if interval < 10:
                interval = 10

            self.settings.sample_interval_ms = interval

        except ValueError:
            self.settings.sample_interval_ms = 50
            self.sample_interval_input.setText("50")

    def start_mpu_stream(self):
        self.update_settings_from_gui()

        if not self.settings.enabled:
            self.status_label.setText("Status: MPU disabled.")
            return

        if self.mpu_reader and self.mpu_reader.isRunning():
            self.status_label.setText("Status: MPU already streaming.")
            return

        self.mpu_reader = MPUReader(self.settings)
        self.mpu_reader.sample_ready.connect(self.handle_mpu_sample)
        self.mpu_reader.error_signal.connect(self.show_error)
        self.mpu_reader.status_signal.connect(self.update_status)

        self.mpu_reader.start()

        if self.settings.show_live_window:
            self.live_display.show()

    def stop_mpu_stream(self):
        if self.mpu_reader:
            if self.experiment_running:
                self.stop_experiment()

            self.mpu_reader.stop()
            self.mpu_reader = None

        self.live_display.hide()
        self.status_label.setText("Status: MPU stream stopped.")

    def handle_mpu_sample(self, sample):
        self.live_display.update_sample(sample)

        self.latest_data_label.setText(
            f"Latest MPU Data: "
            f"Accel X={sample['accel_x_g']:.3f} g, "
            f"Accel Y={sample['accel_y_g']:.3f} g, "
            f"Accel Z={sample['accel_z_g']:.3f} g"
        )

    def start_experiment(self):
        self.update_settings_from_gui()

        if not self.mpu_reader or not self.mpu_reader.isRunning():
            self.start_mpu_stream()

        if not self.mpu_reader:
            QMessageBox.warning(
                self,
                "MPU Not Running",
                "MPU stream could not be started."
            )
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = self.output_folder / f"experiment_mpu_{timestamp}.csv"

        self.experiment_running = True

        if self.settings.show_live_window:
            self.live_display.show()

        self.mpu_reader.start_recording(str(csv_path))

        self.status_label.setText(
            f"Status: Experiment running. Saving MPU data to {csv_path}"
        )

        # ---------------------------------------------------------
        # LATER SHAKE PROTOCOL GOES HERE
        #
        # Example later:
        #
        # self.run_shake_protocol()
        #
        # The MPU data being saved here will only be the data
        # collected while the experiment/shake protocol is active.
        # ---------------------------------------------------------

    def stop_experiment(self):
        if self.mpu_reader:
            self.mpu_reader.stop_recording()

        self.experiment_running = False
        self.status_label.setText("Status: Experiment stopped.")

    def update_status(self, message):
        self.status_label.setText(f"Status: {message}")

    def show_error(self, message):
        self.status_label.setText(f"Status: {message}")
        QMessageBox.warning(self, "MPU Error", message)

    def closeEvent(self, event):
        if self.mpu_reader:
            self.mpu_reader.stop()

        self.live_display.close()
        event.accept()


# -----------------------------
# APP START
# -----------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ExperimentGUI()
    window.show()
    sys.exit(app.exec())