#!/usr/bin/env python3
"""
FlyStress GUI: shared-memory USB camera + MPU6050 + Ender 3 shake test logger

Flow:
1. Opens a Tkinter status/data window.
2. Calibrates MPU6050 while still.
3. Starts USB camera recording through a shared-memory flag.
4. Starts Ender 3 Y-axis shake test in a thread.
5. Logs MPU accel/gyro/temp data to CSV for DURATION seconds.
6. Stops camera recording.
7. Creates a line plot PNG.

Run:
    python3 FS-shake-record-storeData-GUI.py

Requires:
    sudo apt install ffmpeg
    pip install smbus2 pyserial matplotlib
"""

import csv
import math
import os
import queue
import subprocess
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from multiprocessing import Process, Value

import tkinter as tk
from tkinter import messagebox

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
GYRO_FS_SEL = 0            # 0=+-250 dps, 1=+-500, 2=+-1000, 3=+-2000

CAMERA_DEVICE = "/dev/video0"
CAMERA_FRAMERATE = 30
CAMERA_SIZE = ""           # Example: "640x480" or leave blank

OUTPUT_DIR = os.path.expanduser("~/Desktop/FlyStressData")


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


REC_IDLE = 0
REC_START = 1
REC_RECORDING = 2
REC_STOP = 3
REC_EXIT = 4

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
# CAMERA WORKER
# =========================


def camera_worker(recording_flag, video_path, status_q, settings):
    ffmpeg_proc = None
    status_q.put("Camera worker ready.")

    while True:
        flag = recording_flag.value

        if flag == REC_START and ffmpeg_proc is None:
            cmd = [
                "ffmpeg",
                "-f", "v4l2",
                "-framerate", str(settings.camera_framerate),
            ]
            if settings.camera_size:
                cmd.extend(["-video_size", settings.camera_size])
            cmd.extend(["-i", settings.camera_device, "-y", video_path])

            status_q.put("Starting camera recording...")
            try:
                ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                recording_flag.value = REC_RECORDING
                status_q.put(f"Recording video: {video_path}")
            except Exception as e:
                status_q.put(f"Camera start error: {e}")
                recording_flag.value = REC_IDLE

        elif flag == REC_STOP and ffmpeg_proc is not None:
            status_q.put("Stopping camera recording...")
            try:
                ffmpeg_proc.terminate()
                ffmpeg_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
                ffmpeg_proc.wait(timeout=5)
            finally:
                ffmpeg_proc = None
                recording_flag.value = REC_IDLE
                status_q.put(f"Saved video: {video_path}")

        elif flag == REC_EXIT:
            if ffmpeg_proc is not None:
                try:
                    ffmpeg_proc.terminate()
                    ffmpeg_proc.wait(timeout=10)
                except Exception:
                    try:
                        ffmpeg_proc.kill()
                    except Exception:
                        pass
            status_q.put("Camera worker exited.")
            break

        time.sleep(0.05)


# =========================
# PRINTER / SHAKE TEST
# =========================


def send_gcode(ser, cmd, status_q, delay=0.05):
    status_q.put(f">> {cmd}")
    ser.write((cmd + "\n").encode())
    ser.flush()
    time.sleep(delay)


def run_shake_test(stop_event, status_q, settings):
    ser = None
    try:
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial")

        status_q.put(f"Connecting to printer on {settings.serial_port}...")
        ser = serial.Serial(settings.serial_port, settings.baudrate, timeout=2)
        time.sleep(3)

        send_gcode(ser, "M110 N0", status_q)
        send_gcode(ser, "M155 S0", status_q)

        if settings.home_first:
            send_gcode(ser, "G28", status_q)

        send_gcode(ser, "G90", status_q)

        if settings.raise_z_first:
            send_gcode(ser, f"G1 Z{settings.z_lift} F{settings.z_feedrate}", status_q)
            send_gcode(ser, "M400", status_q)
            time.sleep(3)

        send_gcode(ser, f"M204 P{settings.acceleration} T{settings.acceleration}", status_q)
        send_gcode(ser, f"M205 X{settings.jerk} Y{settings.jerk}", status_q)
        send_gcode(ser, f"G1 Y{settings.center_y} F3000", status_q)
        send_gcode(ser, "M400", status_q)

        feedrate = settings.velocity * 60.0
        half = settings.distance / 2.0

        status_q.put("BED SHAKING STARTED")
        start_time = time.time()

        while (time.time() - start_time < settings.shake_duration) and not stop_event.is_set():
            send_gcode(ser, f"G1 Y{settings.center_y + half} F{feedrate}", status_q)
            if stop_event.is_set():
                break
            send_gcode(ser, f"G1 Y{settings.center_y - half} F{feedrate}", status_q)

        status_q.put("Shake motion complete.")

    except Exception as e:
        # Do NOT stop camera/logging here. A printer error should be visible,
        # but the recording and MPU log should continue for the chosen duration.
        status_q.put(f"Shake test error: {type(e).__name__}: {e}")

    finally:
        try:
            if ser is not None:
                status_q.put("Stopping motion and returning to center...")
                send_gcode(ser, "M400", status_q)
                send_gcode(ser, f"G1 Y{settings.center_y} F3000", status_q)
                ser.close()
                status_q.put("Printer connection closed.")
        except Exception as e:
            status_q.put(f"Printer cleanup error: {e}")


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


def calibrate_mpu(mpu, status_q, samples, sample_rate_hz, accel_lsb_per_g):
    status_q.put(f"Calibrating MPU6050 with {samples} samples. Keep it still...")
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
            status_q.put(f"Calibrating... {i}/{samples}")
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

    status_q.put(
        f"Calibration done: ax={mean_ax:.1f}, ay={mean_ay:.1f}, az={mean_az:.1f}, "
        f"gx={mean_gx:.1f}, gy={mean_gy:.1f}, gz={mean_gz:.1f}"
    )
    return offsets


def write_metadata(writer, offsets, accel_lsb_per_g, gyro_lsb_per_dps, video_path, settings):
    writer.writerow(["# FlyStress GUI shared-memory camera + shake + MPU6050 log"])
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


def create_line_plot(plot_path, t_values, ax_values, ay_values, az_values, temp_values, status_q):
    if not t_values:
        status_q.put("No samples collected; no plot created.")
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
    status_q.put(f"Saved line plot: {plot_path}")


def wait_for_camera_state(recording_flag, desired_state, timeout_sec):
    start = time.time()
    while time.time() - start < timeout_sec:
        if recording_flag.value == desired_state:
            return True
        time.sleep(0.05)
    return False


# =========================
# TKINTER APP
# =========================


class FlyStressRunWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("FlyStress Shared Memory Run")
        self.root.geometry("900x650")

        self.status_q = queue.Queue()
        self.data_q = queue.Queue()
        self.stop_event = threading.Event()
        self.run_thread = None
        self.camera_process = None
        self.recording_flag = Value("i", REC_IDLE)
        self.defaults = RunSettings()
        self.entries = {}

        self.csv_path = ""
        self.plot_path = ""
        self.video_path = ""

        self.build_ui()
        self.root.after(100, self.process_queues)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        self.start_btn = tk.Button(top, text="Start Record + Shake Test", command=self.start_run, width=25)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(top, text="Stop", command=self.stop_run, width=12)
        self.stop_btn.pack(side="left", padx=5)

        settings_frame = tk.LabelFrame(self.root, text="Shake Test Settings")
        settings_frame.pack(fill="x", padx=10, pady=(0, 10))

        fields = [
            ("shake_duration", "Shake Duration (s)", self.defaults.shake_duration),
            ("jerk", "Jerk (mm/s)", self.defaults.jerk),
            ("distance", "Distance (mm)", self.defaults.distance),
            ("acceleration", "Acceleration (mm/s^2)", self.defaults.acceleration),
            ("velocity", "Velocity (mm/s)", self.defaults.velocity),
            ("afs_sel", "MPU Range / AFS_SEL", self.defaults.afs_sel),
        ]

        self.entries = {}
        for i, (key, label, value) in enumerate(fields):
            row = i // 2
            col = (i % 2) * 2
            tk.Label(settings_frame, text=label, anchor="w").grid(row=row, column=col, sticky="w", padx=8, pady=4)
            entry = tk.Entry(settings_frame, width=12)
            entry.insert(0, str(value))
            entry.grid(row=row, column=col + 1, sticky="w", padx=8, pady=4)
            self.entries[key] = entry

        tk.Label(
            settings_frame,
            text="MPU range key: 0=+-2g, 1=+-4g, 2=+-8g, 3=+-16g",
            anchor="w"
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=4)

        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10)

        data_frame = tk.LabelFrame(self.root, text="Live MPU Data")
        data_frame.pack(fill="x", padx=10, pady=10)

        self.data_var = tk.StringVar(value="No data yet.")
        tk.Label(data_frame, textvariable=self.data_var, font=("Courier", 13), justify="left", anchor="w").pack(fill="x", padx=10, pady=10)

        file_frame = tk.LabelFrame(self.root, text="Output Files")
        file_frame.pack(fill="x", padx=10, pady=10)

        self.files_var = tk.StringVar(value="Files will appear after start.")
        tk.Label(file_frame, textvariable=self.files_var, font=("Courier", 10), justify="left", anchor="w").pack(fill="x", padx=10, pady=10)

        log_frame = tk.LabelFrame(self.root, text="Run Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.log_text = tk.Text(log_frame, height=18, font=("Courier", 10))
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.status_var.set(msg)

    def process_queues(self):
        while not self.status_q.empty():
            self.log(self.status_q.get_nowait())

        while not self.data_q.empty():
            data = self.data_q.get_nowait()
            self.data_var.set(data)

        self.root.after(100, self.process_queues)

    def get_settings(self):
        settings = RunSettings()
        settings.shake_duration = float(self.entries["shake_duration"].get())
        settings.jerk = float(self.entries["jerk"].get())
        settings.distance = float(self.entries["distance"].get())
        settings.acceleration = float(self.entries["acceleration"].get())
        settings.velocity = float(self.entries["velocity"].get())
        settings.afs_sel = int(self.entries["afs_sel"].get())

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

    def start_run(self):
        if self.run_thread and self.run_thread.is_alive():
            messagebox.showinfo("FlyStress", "A run is already active.")
            return

        try:
            settings = self.get_settings()
        except Exception as e:
            messagebox.showerror("Settings Error", str(e))
            return

        self.stop_event.clear()
        self.start_btn.config(state="disabled")
        self.run_thread = threading.Thread(target=self.run_sequence, args=(settings,), daemon=True)
        self.run_thread.start()

    def stop_run(self):
        self.status_q.put("Stop requested.")
        self.stop_event.set()
        self.recording_flag.value = REC_STOP

    def run_sequence(self, settings):
        mpu = None
        try:
            self.csv_path, self.plot_path, self.video_path = create_output_paths(settings)
            self.files_var.set(
                f"CSV:   {self.csv_path}\n"
                f"Plot:  {self.plot_path}\n"
                f"Video: {self.video_path}"
            )

            accel_lsb_per_g = ACCEL_LSB_PER_G[settings.afs_sel]
            gyro_lsb_per_dps = GYRO_LSB_PER_DPS[settings.gyro_fs_sel]

            self.camera_process = Process(
                target=camera_worker,
                args=(self.recording_flag, self.video_path, self.status_q, settings),
                daemon=True,
            )
            self.camera_process.start()

            self.status_q.put("Connecting to MPU6050...")
            mpu = MPU6050(bus_id=settings.mpu_bus_id, address=settings.mpu_address)
            mpu.wake()
            mpu.set_accel_range(settings.afs_sel)
            mpu.set_gyro_range(settings.gyro_fs_sel)

            offsets = calibrate_mpu(
                mpu,
                self.status_q,
                samples=settings.calibration_samples,
                sample_rate_hz=settings.sample_rate_hz,
                accel_lsb_per_g=accel_lsb_per_g,
            )

            self.status_q.put("Starting camera through shared memory...")
            self.recording_flag.value = REC_START
            if not wait_for_camera_state(self.recording_flag, REC_RECORDING, timeout_sec=5):
                self.status_q.put("WARNING: Camera did not report recording within 5 seconds.")

            shake_thread = threading.Thread(target=run_shake_test, args=(self.stop_event, self.status_q, settings), daemon=True)
            shake_thread.start()

            t_values, ax_values, ay_values, az_values, temp_values = self.log_mpu_data(
                mpu,
                offsets,
                accel_lsb_per_g,
                gyro_lsb_per_dps,
                settings,
            )

            self.stop_event.set()
            shake_thread.join(timeout=10)

            self.status_q.put("Stopping camera through shared memory...")
            self.recording_flag.value = REC_STOP
            wait_for_camera_state(self.recording_flag, REC_IDLE, timeout_sec=15)

            create_line_plot(self.plot_path, t_values, ax_values, ay_values, az_values, temp_values, self.status_q)
            self.status_q.put("Run complete.")

        except Exception as e:
            self.status_q.put(f"ERROR: {type(e).__name__}: {e}")
            self.recording_flag.value = REC_STOP

        finally:
            if mpu is not None:
                mpu.close()

            time.sleep(0.5)
            self.recording_flag.value = REC_EXIT
            if self.camera_process is not None:
                self.camera_process.join(timeout=5)
                if self.camera_process.is_alive():
                    self.camera_process.terminate()
                    self.camera_process.join(timeout=2)

            self.root.after(0, lambda: self.start_btn.config(state="normal"))

    def log_mpu_data(self, mpu, offsets, accel_lsb_per_g, gyro_lsb_per_dps, settings):
        dt_target = 1.0 / settings.sample_rate_hz
        total_samples = max(1, int(settings.shake_duration * settings.sample_rate_hz))

        t_values = []
        ax_values = []
        ay_values = []
        az_values = []
        temp_values = []

        self.status_q.put(f"Saving MPU data to: {self.csv_path}")

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
                    self.data_q.put(
                        f"t = {t_sec:7.3f} s\n"
                        f"Accel:  ax={ax_g:8.4f} g   ay={ay_g:8.4f} g   az={az_g:8.4f} g\n"
                        f"Gyro:   gx={gx_deg_s:8.4f} deg/s   gy={gy_deg_s:8.4f} deg/s   gz={gz_deg_s:8.4f} deg/s\n"
                        f"Temp:   {temp_c:6.2f} C   {temp_f:6.2f} F\n"
                        f"Rot:    x={x_rot:8.2f} deg   y={y_rot:8.2f} deg"
                    )

                if sample_idx % 100 == 0:
                    csv_file.flush()
                    os.fsync(csv_file.fileno())
                    self.status_q.put(
                        f"sample {sample_idx}/{total_samples} | "
                        f"ax={ax_g:.4f}g ay={ay_g:.4f}g az={az_g:.4f}g temp={temp_c:.2f}C"
                    )

                target_next = t0 + (sample_idx + 1) * dt_target
                sleep_for = target_next - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)

        return t_values, ax_values, ay_values, az_values, temp_values

    def on_close(self):
        self.stop_event.set()
        self.recording_flag.value = REC_EXIT
        if self.camera_process is not None and self.camera_process.is_alive():
            self.camera_process.terminate()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = FlyStressRunWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
