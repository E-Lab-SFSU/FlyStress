#!/usr/bin/env python3
"""
FlyStress (FS) Ender 3 Shake Test + Record + MPU Data Log: 
Authors: Keith Curry (shake test logic) Cherese Jordan (MPU log and GUI)

Flow:
1. Opens a PyQt6 window with a live camera preview.
2. Camera preview starts as soon as you press Start (before recording begins).
3. Calibrates MPU6050 while still.
4. Starts writing the camera preview to a video file (recording).
5. Starts Ender 3 Y-axis shake test in a background thread.
6. Logs MPU accel/gyro/temp data to CSV for the configured duration.
7. Stops video recording (camera preview keeps running until the window closes).
8. Creates a line plot PNG.

Run:
    python3 FS-SDR-v2.py

Requires:
    pip install PyQt6 opencv-python smbus2 pyserial matplotlib numpy

"""

import csv
import math
import os
import sys
import time
import threading
from datetime import datetime
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMutex, QWaitCondition
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QGridLayout, QVBoxLayout, QHBoxLayout, QGroupBox, QTextEdit, QMessageBox,
    QSizePolicy, QFrame,
)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

try:
    from smbus2 import SMBus
    SMBUS_AVAILABLE = True
except ImportError:
    SMBUS_AVAILABLE = False

# =========================
# USER SETTINGS
# =========================

SERIAL_PORT = "/dev/ttyUSB0"   # Try /dev/ttyACM0 if needed
BAUDRATE = 115200

SHAKE_DISTANCE = 4.0       # mm, total peak-to-peak Y travel
VELOCITY = 220.0           # mm/s
ACCELERATION = 2000.0      # mm/s^2
JERK = 20.0                # mm/s
DURATION = 20.0            # seconds of MPU logging and recording

CENTER_Y = 25.0
HOME_FIRST = True
RAISE_Z_FIRST = True
Z_LIFT = 50.0              # mm
Z_FEEDRATE = 600.0         # mm/min

MPU_BUS_ID = 1
MPU_ADDRESS = 0x68
SAMPLE_RATE_HZ = 100.0
CALIBRATION_SAMPLES = 300
ACCEL_AFS_SEL = 1          # 0=+-2g, 1=+-4g, 2=+-8g, 3=+-16g
GYRO_FS_SEL = 0             # 0=+-250 dps, 1=+-500, 2=+-1000, 3=+-2000

CAMERA_DEVICE = "/dev/video0"
CAMERA_FRAMERATE = 30
CAMERA_SIZE = ""           # Example: "640x480" or leave blank

OUTPUT_DIR = os.path.expanduser("~/Desktop/FlyStressData")

# necessary value for shake test calculations
# saves default setting that can be changed in the GUI, but not saved to config file
@dataclass
class RunSettings:
    # Settings displayed/editable in the GUI
    shake_duration: float = DURATION
    jerk: float = JERK
    distance: float = SHAKE_DISTANCE
    acceleration: float = ACCELERATION
    velocity: float = VELOCITY
    afs_sel: int = ACCEL_AFS_SEL

    # Hidden/default settings used by the program
    serial_port: str = SERIAL_PORT
    baudrate: int = BAUDRATE
    center_y: float = CENTER_Y
    home_first: bool = HOME_FIRST
    raise_z_first: bool = RAISE_Z_FIRST
    z_lift: float = Z_LIFT
    z_feedrate: float = Z_FEEDRATE
    mpu_bus_id: int = MPU_BUS_ID
    mpu_address: int = MPU_ADDRESS
    sample_rate_hz: float = SAMPLE_RATE_HZ
    calibration_samples: int = CALIBRATION_SAMPLES
    gyro_fs_sel: int = GYRO_FS_SEL
    camera_device: str = CAMERA_DEVICE
    camera_framerate: int = CAMERA_FRAMERATE
    camera_size: str = CAMERA_SIZE
    output_dir: str = OUTPUT_DIR


# =========================
# MPU6050 REGISTERS
# =========================

REG_PWR_MGMT_1 = 0x6B
REG_ACCEL_CONFIG = 0x1C
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_XOUT_H = 0x3B
REG_TEMP_OUT_H = 0x41
REG_GYRO_XOUT_H = 0x43

ACCEL_LSB_PER_G = {0: 16384.0, 1: 8192.0, 2: 4096.0, 3: 2048.0}
GYRO_LSB_PER_DPS = {0: 131.0, 1: 65.5, 2: 32.8, 3: 16.4}
ACCEL_RANGE_G = {0: 2, 1: 4, 2: 8, 3: 16}


def to_int16(value):
    value &= 0xFFFF
    return value - 65536 if value & 0x8000 else value


def dist(a, b):
    return math.sqrt((a * a) + (b * b))


def get_y_rotation(x, y, z):
    radians = math.atan2(x, dist(y, z))
    return -math.degrees(radians)


def get_x_rotation(x, y, z):
    radians = math.atan2(y, dist(x, z))
    return math.degrees(radians)


class MPU6050:
    def __init__(self, bus_id=1, address=0x68):
        if not SMBUS_AVAILABLE:
            raise RuntimeError("smbus2 is not installed. Run: pip install smbus2")
        self.bus = SMBus(bus_id)
        self.address = address

    def close(self):
        try:
            self.bus.close()
        except Exception:
            pass

    def write_byte(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value & 0xFF)

    def read_block(self, reg, length):
        return list(self.bus.read_i2c_block_data(self.address, reg, length))

    def read_word_2c(self, reg):
        data = self.read_block(reg, 2)
        return to_int16((data[0] << 8) | data[1])

    def wake(self):
        self.write_byte(REG_PWR_MGMT_1, 0x00)
        time.sleep(0.05)

    def set_accel_range(self, afs_sel):
        self.write_byte(REG_ACCEL_CONFIG, (afs_sel & 0x03) << 3)
        time.sleep(0.02)

    def set_gyro_range(self, fs_sel):
        self.write_byte(REG_GYRO_CONFIG, (fs_sel & 0x03) << 3)
        time.sleep(0.02)

    def read_accel_raw(self):
        data = self.read_block(REG_ACCEL_XOUT_H, 6)
        return (
            to_int16((data[0] << 8) | data[1]),
            to_int16((data[2] << 8) | data[3]),
            to_int16((data[4] << 8) | data[5]),
        )

    def read_gyro_raw(self):
        data = self.read_block(REG_GYRO_XOUT_H, 6)
        return (
            to_int16((data[0] << 8) | data[1]),
            to_int16((data[2] << 8) | data[3]),
            to_int16((data[4] << 8) | data[5]),
        )

    def read_temperature(self):
        raw_temp = self.read_word_2c(REG_TEMP_OUT_H)
        temp_c = (raw_temp / 340.0) + 36.53
        temp_f = (temp_c * 9.0 / 5.0) + 32.0
        return raw_temp, temp_c, temp_f


# =========================
# CAMERA THREAD (preview + recording)
# =========================


class CameraThread(QThread):
    frameReady = pyqtSignal(QImage)
    statusMessage = pyqtSignal(str)
    recordingStarted = pyqtSignal()
    recordingStopped = pyqtSignal()
    errorOccurred = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._running = True
        self._recording = False
        self._video_path = None
        self._writer = None
        self._cap = None
        self._mutex = QMutex()
        self.opened_event = threading.Event()

    def run(self):
        if not CV2_AVAILABLE:
            self.errorOccurred.emit(
                "opencv-python (cv2) is not installed. Run: pip install opencv-python"
            )
            return

        self.statusMessage.emit(f"Opening camera {self.settings.camera_device}...")
        cap = cv2.VideoCapture(self.settings.camera_device, cv2.CAP_V4L2)
        if not cap.isOpened():
            self.errorOccurred.emit(f"Could not open camera {self.settings.camera_device}")
            return

        if self.settings.camera_size:
            try:
                w_str, h_str = self.settings.camera_size.lower().split("x")
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(w_str))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(h_str))
            except Exception:
                self.statusMessage.emit(
                    f"Could not parse camera_size '{self.settings.camera_size}', ignoring."
                )
        cap.set(cv2.CAP_PROP_FPS, self.settings.camera_framerate)

        self._cap = cap
        self.opened_event.set()
        self.statusMessage.emit("Camera preview ready.")

        while self._running:
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue

            self._mutex.lock()
            recording = self._recording
            writer = self._writer
            self._mutex.unlock()

            if recording and writer is not None:
                try:
                    writer.write(frame)
                except Exception as e:
                    self.statusMessage.emit(f"Video write error: {e}")

            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                self.frameReady.emit(qimg)
            except Exception:
                pass

        self._close_writer()
        cap.release()
        self.statusMessage.emit("Camera stopped.")

    def start_recording(self, video_path):
        """Synchronous: returns once the writer is open, or raises."""
        if not self.opened_event.wait(timeout=5):
            raise RuntimeError("Camera did not become ready in time.")

        self._mutex.lock()
        try:
            if self._cap is None or not self._cap.isOpened():
                raise RuntimeError("Camera is not open.")
            w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
            h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
            fps = self.settings.camera_framerate
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(video_path, fourcc, fps, (w, h))
            if not writer.isOpened():
                raise RuntimeError(f"Could not open video writer for {video_path}")
            self._writer = writer
            self._video_path = video_path
            self._recording = True
        finally:
            self._mutex.unlock()

        self.recordingStarted.emit()
        self.statusMessage.emit(f"Recording video: {video_path}")

    def stop_recording(self):
        self._mutex.lock()
        self._recording = False
        self._mutex.unlock()
        self._close_writer()
        self.recordingStopped.emit()
        if self._video_path:
            self.statusMessage.emit(f"Saved video: {self._video_path}")

    def _close_writer(self):
        self._mutex.lock()
        writer = self._writer
        self._writer = None
        self._mutex.unlock()
        if writer is not None:
            writer.release()

    def is_recording(self):
        self._mutex.lock()
        r = self._recording
        self._mutex.unlock()
        return r

    def stop(self):
        self._running = False


# =========================
# PRINTER / SHAKE TEST
# =========================


def send_gcode(ser, cmd, log_fn, delay=0.05):
    log_fn(f">> {cmd}")
    ser.write((cmd + "\n").encode())
    ser.flush()
    time.sleep(delay)


def run_shake_test(stop_event, log_fn, settings):
    ser = None
    try:
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial")

        log_fn(f"Connecting to printer on {settings.serial_port}...")
        ser = serial.Serial(settings.serial_port, settings.baudrate, timeout=2)
        time.sleep(3)

        send_gcode(ser, "M110 N0", log_fn)
        send_gcode(ser, "M155 S0", log_fn)

        if settings.home_first:
            send_gcode(ser, "G28", log_fn)

        send_gcode(ser, "G90", log_fn)

        if settings.raise_z_first:
            send_gcode(ser, f"G1 Z{settings.z_lift} F{settings.z_feedrate}", log_fn)
            send_gcode(ser, "M400", log_fn)
            time.sleep(3)

        send_gcode(ser, f"M204 P{settings.acceleration} T{settings.acceleration}", log_fn)
        send_gcode(ser, f"M205 X{settings.jerk} Y{settings.jerk}", log_fn)
        send_gcode(ser, f"G1 Y{settings.center_y} F3000", log_fn)
        send_gcode(ser, "M400", log_fn)

        feedrate = settings.velocity * 60.0
        half = settings.distance / 2.0

        log_fn("BED SHAKING STARTED")
        start_time = time.time()

        while (time.time() - start_time < settings.shake_duration) and not stop_event.is_set():
            send_gcode(ser, f"G1 Y{settings.center_y + half} F{feedrate}", log_fn)
            if stop_event.is_set():
                break
            send_gcode(ser, f"G1 Y{settings.center_y - half} F{feedrate}", log_fn)

        log_fn("Shake motion complete.")

    except Exception as e:
        # Do NOT stop camera/logging here. A printer error should be visible,
        # but the recording and MPU log should continue for the chosen duration.
        log_fn(f"Shake test error: {type(e).__name__}: {e}")

    finally:
        try:
            if ser is not None:
                log_fn("Stopping motion and returning to center...")
                send_gcode(ser, "M400", log_fn)
                send_gcode(ser, f"G1 Y{settings.center_y} F3000", log_fn)
                ser.close()
                log_fn("Printer connection closed.")
        except Exception as e:
            log_fn(f"Printer cleanup error: {e}")


# =========================
# DATA FUNCTIONS
# =========================


def create_output_paths(settings):
    os.makedirs(settings.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(settings.output_dir, f"flystress_shake_mpu_{timestamp}.csv")
    plot_path = os.path.join(settings.output_dir, f"flystress_shake_mpu_{timestamp}.png")
    video_path = os.path.join(settings.output_dir, f"flystress_video_{timestamp}.mp4")
    return csv_path, plot_path, video_path


def calibrate_mpu(mpu, log_fn, samples, sample_rate_hz, accel_lsb_per_g):
    log_fn(f"Calibrating MPU6050 with {samples} samples. Keep it still...")
    dt = 1.0 / sample_rate_hz
    sum_ax = sum_ay = sum_az = 0.0
    sum_gx = sum_gy = sum_gz = 0.0

    for i in range(samples):
        ax, ay, az = mpu.read_accel_raw()
        gx, gy, gz = mpu.read_gyro_raw()
        sum_ax += ax
        sum_ay += ay
        sum_az += az
        sum_gx += gx
        sum_gy += gy
        sum_gz += gz
        if i % 50 == 0:
            log_fn(f"Calibrating... {i}/{samples}")
        time.sleep(dt)

    mean_ax = sum_ax / samples
    mean_ay = sum_ay / samples
    mean_az = sum_az / samples
    mean_gx = sum_gx / samples
    mean_gy = sum_gy / samples
    mean_gz = sum_gz / samples

    offsets = {
        "ax": mean_ax,
        "ay": mean_ay,
        "az": mean_az - accel_lsb_per_g,
        "gx": mean_gx,
        "gy": mean_gy,
        "gz": mean_gz,
    }

    log_fn(
        f"Calibration done: ax={mean_ax:.1f}, ay={mean_ay:.1f}, az={mean_az:.1f}, "
        f"gx={mean_gx:.1f}, gy={mean_gy:.1f}, gz={mean_gz:.1f}"
    )
    return offsets


def write_metadata(writer, offsets, accel_lsb_per_g, gyro_lsb_per_dps, video_path, settings):
    writer.writerow(["# FlyStress GUI live-preview camera + shake + MPU6050 log"])
    writer.writerow(["# start_time", datetime.now().isoformat(timespec="seconds")])
    writer.writerow(["# video_path", video_path])
    writer.writerow(["# camera_device", settings.camera_device])
    writer.writerow(["# serial_port", settings.serial_port])
    writer.writerow(["# baudrate", settings.baudrate])
    writer.writerow(["# shake_distance_mm", settings.distance])
    writer.writerow(["# velocity_mm_s", settings.velocity])
    writer.writerow(["# acceleration_mm_s2", settings.acceleration])
    writer.writerow(["# jerk_mm_s", settings.jerk])
    writer.writerow(["# duration_s", settings.shake_duration])
    writer.writerow(["# center_y", settings.center_y])
    writer.writerow(["# sample_rate_hz", settings.sample_rate_hz])
    writer.writerow(["# calibration_samples", settings.calibration_samples])
    writer.writerow(["# accel_afs_sel", settings.afs_sel])
    writer.writerow(["# accel_range_g", ACCEL_RANGE_G[settings.afs_sel]])
    writer.writerow(["# accel_lsb_per_g", accel_lsb_per_g])
    writer.writerow(["# gyro_fs_sel", settings.gyro_fs_sel])
    writer.writerow(["# gyro_lsb_per_deg_s", gyro_lsb_per_dps])
    writer.writerow([
        "# offsets_raw_counts",
        f"ax={offsets['ax']:.4f}",
        f"ay={offsets['ay']:.4f}",
        f"az={offsets['az']:.4f}",
        f"gx={offsets['gx']:.4f}",
        f"gy={offsets['gy']:.4f}",
        f"gz={offsets['gz']:.4f}",
    ])
    writer.writerow([])


def create_line_plot(plot_path, t_values, ax_values, ay_values, az_values, temp_values, log_fn):
    if not t_values:
        log_fn("No samples collected; no plot created.")
        return

    plt.figure(figsize=(12, 7), dpi=150)
    plt.plot(t_values, ax_values, label="ax_g")
    plt.plot(t_values, ay_values, label="ay_g")
    plt.plot(t_values, az_values, label="az_g")
    plt.xlabel("Time (seconds)")
    plt.ylabel("Acceleration (g)")
    plt.title("FlyStress MPU6050 Acceleration During Shake Test")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(plot_path)
    plt.close()
    log_fn(f"Saved line plot: {plot_path}")


# =========================
# RUN WORKER (calibration -> record -> shake -> log -> stop -> plot)
# =========================


class RunWorker(QThread):
    statusMessage = pyqtSignal(str)
    dataMessage = pyqtSignal(str)
    filesReady = pyqtSignal(str, str, str)
    finishedRun = pyqtSignal()

    def __init__(self, settings, camera_thread, stop_event, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.camera_thread = camera_thread
        self.stop_event = stop_event
        self.csv_path = ""
        self.plot_path = ""
        self.video_path = ""

    def log(self, msg):
        self.statusMessage.emit(msg)

    def run(self):
        mpu = None
        try:
            self.csv_path, self.plot_path, self.video_path = create_output_paths(self.settings)
            self.filesReady.emit(self.csv_path, self.plot_path, self.video_path)

            accel_lsb_per_g = ACCEL_LSB_PER_G[self.settings.afs_sel]
            gyro_lsb_per_dps = GYRO_LSB_PER_DPS[self.settings.gyro_fs_sel]

            self.log("Connecting to MPU6050...")
            mpu = MPU6050(bus_id=self.settings.mpu_bus_id, address=self.settings.mpu_address)
            mpu.wake()
            mpu.set_accel_range(self.settings.afs_sel)
            mpu.set_gyro_range(self.settings.gyro_fs_sel)

            offsets = calibrate_mpu(
                mpu,
                self.log,
                samples=self.settings.calibration_samples,
                sample_rate_hz=self.settings.sample_rate_hz,
                accel_lsb_per_g=accel_lsb_per_g,
            )

            if self.stop_event.is_set():
                self.log("Stop requested before recording started; aborting.")
                return

            # Synchronous: only returns once the video writer is actually open.
            self.camera_thread.start_recording(self.video_path)

            shake_thread = threading.Thread(
                target=run_shake_test,
                args=(self.stop_event, self.log, self.settings),
                daemon=True,
            )
            shake_thread.start()

            t_values, ax_values, ay_values, az_values, temp_values = self.log_mpu_data(
                mpu, offsets, accel_lsb_per_g, gyro_lsb_per_dps, self.settings,
            )

            self.stop_event.set()
            shake_thread.join(timeout=10)

            self.camera_thread.stop_recording()

            create_line_plot(self.plot_path, t_values, ax_values, ay_values, az_values, temp_values, self.log)
            self.log("Run complete.")

        except Exception as e:
            self.log(f"ERROR: {type(e).__name__}: {e}")
            try:
                self.camera_thread.stop_recording()
            except Exception:
                pass

        finally:
            if mpu is not None:
                mpu.close()
            self.finishedRun.emit()

    def log_mpu_data(self, mpu, offsets, accel_lsb_per_g, gyro_lsb_per_dps, settings):
        dt_target = 1.0 / settings.sample_rate_hz
        total_samples = max(1, int(settings.shake_duration * settings.sample_rate_hz))

        t_values = []
        ax_values = []
        ay_values = []
        az_values = []
        temp_values = []

        self.log(f"Saving MPU data to: {self.csv_path}")

        with open(self.csv_path, "w", newline="") as csv_file:
            writer = csv.writer(csv_file)
            write_metadata(writer, offsets, accel_lsb_per_g, gyro_lsb_per_dps, self.video_path, settings)
            writer.writerow([
                "t_sec", "timestamp",
                "ax_raw", "ay_raw", "az_raw",
                "ax_corr", "ay_corr", "az_corr",
                "ax_g", "ay_g", "az_g",
                "gx_raw", "gy_raw", "gz_raw",
                "gx_corr", "gy_corr", "gz_corr",
                "gx_deg_s", "gy_deg_s", "gz_deg_s",
                "temp_raw", "temp_c", "temp_f",
                "x_rotation", "y_rotation",
            ])

            t0 = time.perf_counter()
            for sample_idx in range(total_samples):
                if self.stop_event.is_set():
                    break

                now = time.perf_counter()
                t_sec = now - t0
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

                ax_raw, ay_raw, az_raw = mpu.read_accel_raw()
                gx_raw, gy_raw, gz_raw = mpu.read_gyro_raw()
                temp_raw, temp_c, temp_f = mpu.read_temperature()

                ax_corr = ax_raw - offsets["ax"]
                ay_corr = ay_raw - offsets["ay"]
                az_corr = az_raw - offsets["az"]
                gx_corr = gx_raw - offsets["gx"]
                gy_corr = gy_raw - offsets["gy"]
                gz_corr = gz_raw - offsets["gz"]

                ax_g = ax_corr / accel_lsb_per_g
                ay_g = ay_corr / accel_lsb_per_g
                az_g = az_corr / accel_lsb_per_g
                gx_deg_s = gx_corr / gyro_lsb_per_dps
                gy_deg_s = gy_corr / gyro_lsb_per_dps
                gz_deg_s = gz_corr / gyro_lsb_per_dps

                x_rot = get_x_rotation(ax_g, ay_g, az_g)
                y_rot = get_y_rotation(ax_g, ay_g, az_g)

                writer.writerow([
                    f"{t_sec:.6f}", timestamp,
                    int(ax_raw), int(ay_raw), int(az_raw),
                    f"{ax_corr:.4f}", f"{ay_corr:.4f}", f"{az_corr:.4f}",
                    f"{ax_g:.6f}", f"{ay_g:.6f}", f"{az_g:.6f}",
                    int(gx_raw), int(gy_raw), int(gz_raw),
                    f"{gx_corr:.4f}", f"{gy_corr:.4f}", f"{gz_corr:.4f}",
                    f"{gx_deg_s:.6f}", f"{gy_deg_s:.6f}", f"{gz_deg_s:.6f}",
                    int(temp_raw), f"{temp_c:.4f}", f"{temp_f:.4f}",
                    f"{x_rot:.4f}", f"{y_rot:.4f}",
                ])

                t_values.append(t_sec)
                ax_values.append(ax_g)
                ay_values.append(ay_g)
                az_values.append(az_g)
                temp_values.append(temp_c)

                if sample_idx % 10 == 0:
                    self.dataMessage.emit(
                        f"t = {t_sec:7.3f} s\n"
                        f"Accel:  ax={ax_g:8.4f} g   ay={ay_g:8.4f} g   az={az_g:8.4f} g\n"
                        f"Gyro:   gx={gx_deg_s:8.4f} deg/s   gy={gy_deg_s:8.4f} deg/s   gz={gz_deg_s:8.4f} deg/s\n"
                        f"Temp:   {temp_c:6.2f} C   {temp_f:6.2f} F\n"
                        f"Rot:    x={x_rot:8.2f} deg   y={y_rot:8.2f} deg"
                    )

                if sample_idx % 100 == 0:
                    csv_file.flush()
                    os.fsync(csv_file.fileno())
                    self.log(
                        f"sample {sample_idx}/{total_samples} | "
                        f"ax={ax_g:.4f}g ay={ay_g:.4f}g az={az_g:.4f}g temp={temp_c:.2f}C"
                    )

                target_next = t0 + (sample_idx + 1) * dt_target
                sleep_for = target_next - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)

        return t_values, ax_values, ay_values, az_values, temp_values


# =========================
# PYQT6 APP
# =========================


class FlyStressMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FS Shake Test")
        self.resize(1150, 780)

        self.defaults = RunSettings()
        self.entries = {}
        self.camera_thread = None
        self.run_worker = None
        self.stop_event = threading.Event()

        self.build_ui()

    # ---------- UI construction ----------

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)

        # -------- Left column: preview --------
        left = QVBoxLayout()
        preview_box = QGroupBox("Camera Preview")
        preview_layout = QVBoxLayout(preview_box)

        self.preview_label = QLabel("Preview will appear here once recording starts.")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedSize(560, 420)
        self.preview_label.setStyleSheet("background-color: #111; color: #aaa; border: 1px solid #444;")
        self.preview_label.setScaledContents(False)
        preview_layout.addWidget(self.preview_label)
        left.addWidget(preview_box)

        files_box = QGroupBox("Output Files")
        files_layout = QVBoxLayout(files_box)
        self.files_label = QLabel("Files will appear after start.")
        self.files_label.setFont(self._mono_font(10))
        self.files_label.setWordWrap(True)
        files_layout.addWidget(self.files_label)
        left.addWidget(files_box)

        outer.addLayout(left, 3)

        # -------- Right column: controls, settings, data, log --------
        right = QVBoxLayout()

        controls = QHBoxLayout()
        self.start_btn = QPushButton("Start Record + Shake Test")
        self.start_btn.clicked.connect(self.start_run)
        controls.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_run)
        controls.addWidget(self.stop_btn)
        right.addLayout(controls)

        settings_box = QGroupBox("Shake Test Settings")
        settings_grid = QGridLayout(settings_box)
        fields = [
            ("shake_duration", "Shake Duration (s)", self.defaults.shake_duration),
            ("jerk", "Jerk (mm/s)", self.defaults.jerk),
            ("distance", "Distance (mm)", self.defaults.distance),
            ("acceleration", "Acceleration (mm/s^2)", self.defaults.acceleration),
            ("velocity", "Velocity (mm/s)", self.defaults.velocity),
            ("afs_sel", "MPU Range / AFS_SEL", self.defaults.afs_sel),
        ]
        for i, (key, label, value) in enumerate(fields):
            row = i // 2
            col = (i % 2) * 2
            settings_grid.addWidget(QLabel(label), row, col)
            edit = QLineEdit(str(value))
            edit.setFixedWidth(100)
            settings_grid.addWidget(edit, row, col + 1)
            self.entries[key] = edit
        note = QLabel("MPU range key: 0=+-2g, 1=+-4g, 2=+-8g, 3=+-16g")
        settings_grid.addWidget(note, 3, 0, 1, 4)
        right.addWidget(settings_box)

        self.status_label = QLabel("Ready.")
        right.addWidget(self.status_label)

        data_box = QGroupBox("Live MPU Data")
        data_layout = QVBoxLayout(data_box)
        self.data_label = QLabel("No data yet.")
        self.data_label.setFont(self._mono_font(12))
        data_layout.addWidget(self.data_label)
        right.addWidget(data_box)

        log_box = QGroupBox("Run Log")
        log_layout = QVBoxLayout(log_box)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(self._mono_font(10))
        log_layout.addWidget(self.log_text)
        right.addWidget(log_box, 1)

        outer.addLayout(right, 4)

    @staticmethod
    def _mono_font(size):
        from PyQt6.QtGui import QFont
        f = QFont("Courier New")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(size)
        return f

    # ---------- logging helpers ----------

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")
        self.status_label.setText(msg)

    def update_data(self, text):
        self.data_label.setText(text)

    def update_files(self, csv_path, plot_path, video_path):
        self.files_label.setText(f"CSV:   {csv_path}\nPlot:  {plot_path}\nVideo: {video_path}")

    def update_preview(self, qimg):
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    # ---------- settings ----------

    def get_settings(self):
        settings = RunSettings()
        settings.shake_duration = float(self.entries["shake_duration"].text())
        settings.jerk = float(self.entries["jerk"].text())
        settings.distance = float(self.entries["distance"].text())
        settings.acceleration = float(self.entries["acceleration"].text())
        settings.velocity = float(self.entries["velocity"].text())
        settings.afs_sel = int(self.entries["afs_sel"].text())

        if settings.shake_duration <= 0:
            raise ValueError("Shake Duration must be greater than 0.")
        if settings.distance <= 0:
            raise ValueError("Distance must be greater than 0.")
        if settings.velocity <= 0:
            raise ValueError("Velocity must be greater than 0.")
        if settings.acceleration <= 0:
            raise ValueError("Acceleration must be greater than 0.")
        if settings.jerk < 0:
            raise ValueError("Jerk cannot be negative.")
        if settings.afs_sel not in (0, 1, 2, 3):
            raise ValueError("MPU Range / AFS_SEL must be 0, 1, 2, or 3.")
        return settings

    # ---------- run control ----------

    def start_run(self):
        if self.run_worker is not None and self.run_worker.isRunning():
            QMessageBox.information(self, "FlyStress", "A run is already active.")
            return

        try:
            settings = self.get_settings()
        except Exception as e:
            QMessageBox.critical(self, "Settings Error", str(e))
            return

        self.stop_event = threading.Event()
        self.start_btn.setEnabled(False)

        # Camera thread starts fresh each run so it picks up current settings,
        # and the preview appears immediately (before recording/shake begin).
        self.camera_thread = CameraThread(settings)
        self.camera_thread.frameReady.connect(self.update_preview)
        self.camera_thread.statusMessage.connect(self.log)
        self.camera_thread.errorOccurred.connect(self.on_camera_error)
        self.camera_thread.start()

        self.run_worker = RunWorker(settings, self.camera_thread, self.stop_event)
        self.run_worker.statusMessage.connect(self.log)
        self.run_worker.dataMessage.connect(self.update_data)
        self.run_worker.filesReady.connect(self.update_files)
        self.run_worker.finishedRun.connect(self.on_run_finished)
        self.run_worker.start()

    def stop_run(self):
        self.log("Stop requested.")
        self.stop_event.set()

    def on_camera_error(self, msg):
        self.log(f"Camera error: {msg}")
        QMessageBox.critical(self, "Camera Error", msg)

    def on_run_finished(self):
        self.start_btn.setEnabled(True)
        # Camera keeps streaming for preview; recording write is already
        # stopped by RunWorker. Stop the camera thread fully so the device
        # is free for the next run.
        if self.camera_thread is not None:
            self.camera_thread.stop()
            self.camera_thread.wait(3000)
            self.camera_thread = None
        self.preview_label.setText("Preview will appear here once recording starts.")
        self.preview_label.setPixmap(QPixmap())

    # ---------- shutdown ----------

    def closeEvent(self, event):
        self.stop_event.set()
        if self.run_worker is not None and self.run_worker.isRunning():
            self.run_worker.wait(5000)
        if self.camera_thread is not None:
            self.camera_thread.stop()
            self.camera_thread.wait(3000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = FlyStressMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()