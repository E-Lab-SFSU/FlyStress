#!/usr/bin/env python3
"""
FlyStress GUI
 Author: Cherese Jordan
 Purpose: Easy interface to use multiple programs
 - execute shake test (created by Keith Curry)
 - read and store data from MPU-6050 accelerometer
 - create line plot graphs that display the amount of forced used during a shake test
 - shorten .csv files to focus on relevant data (extract tool)
 - create boxplot to show min force, max force, average force
 - record videos that can then be analyzed using tracking too (created by Devin Kelly) 
 
 For File_Search_CJ.py devices, install: customtkinter, matplotlib, pyserial, smbus2, v4l2, ffmmpeg
 
Original device, In terminal : cd Desktop
               source flystress-env/bin/activate
               python3 FlyStress-v4.py

"""

import csv
import glob
import os
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

try:
    from smbus2 import SMBus
    SMBUS_AVAILABLE = True
except ImportError:
    SMBUS_AVAILABLE = False


REG_PWR_MGMT_1 = 0x6B   # power button/register
REG_ACCEL_CONFIG = 0x1C     # register that stores/controls the range
REG_ACCEL_XOUT_H = 0x3B     # output register

AFS_SEL_TO_LSB_PER_G = {0: 16384, 1: 8192, 2: 4096, 3: 2048} # library saving select options for desired force
AFS_SEL_TO_RANGE_G = {0: 2, 1: 4, 2: 8, 3: 16}  # range used
G_TO_VEL = 9806.65  # conversion factor for g to velocity   


# converting MPU output to 16 bit value
def to_int16(value: int) -> int:
    value &= 0xFFFF
    return value - 65536 if value & 0x8000 else value

# determine correct MPU range
def estimate_max_dynamic_accel(distance_mm, velocity_mm_s, accel_mm_s2, jerk_mm_s):
    half_stroke = distance_mm / 2.0
    if half_stroke <= 0 or accel_mm_s2 <= 0:
        return 0.0
    _accel_dist = (velocity_mm_s ** 2) / (2.0 * accel_mm_s2)
    return accel_mm_s2

# chosing the MPU range
def choose_mpu6050_range(total_peak_g):
    for rng in (2, 4, 8, 16):
        if total_peak_g <= rng:
            return rng
    return 16

# setting a default range depending on the settings for shake test
def choose_default(recommended_range):
    mapping = {2: 0, 4: 1, 8: 2, 16: 3}
    return mapping[recommended_range]

# create a unique file name for raw data from MPU
def find_next_filename(prefix="raw-data-", suffix=".csv", directory="."):
    pattern = os.path.join(directory, f"{prefix}*{suffix}")
    existing = glob.glob(pattern)
    max_idx = -1
    for path in existing:
        base = os.path.basename(path)
        try:
            idx = int(base[len(prefix):-len(suffix)])
            max_idx = max(max_idx, idx)
        except Exception:
            pass
    return os.path.join(directory, f"{prefix}{max_idx + 1}{suffix}")

# create unique file name for box plots
def find_next_plot_filename(prefix="box_g_", suffix=".png", directory="."):
    pattern = os.path.join(directory, f"{prefix}*{suffix}")
    existing = glob.glob(pattern)
    max_idx = -1
    for path in existing:
        base = os.path.basename(path)
        try:
            idx = int(base[len(prefix):-len(suffix)])
            max_idx = max(max_idx, idx)
        except Exception:
            pass
    return os.path.join(directory, f"{prefix}{max_idx + 1}{suffix}")

# connecting to the printer
def auto_detect_serial_port():
    candidates = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*"):
        candidates.extend(sorted(glob.glob(pattern)))

    if SERIAL_AVAILABLE:
        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            for port in ports:
                if port not in candidates:
                    candidates.append(port)
        except Exception:
            pass

    return candidates[0] if candidates else "/dev/ttyUSB0"

# MPU : reading data, saving data, MPU calibration, 
class MPU6050:
    def __init__(self, bus_id=1, address=0x68):
        if not SMBUS_AVAILABLE:
            raise RuntimeError("smbus2 not available. Install it with: pip3 install smbus2")
        self.bus = SMBus(bus_id)
        self.address = address

    def close(self):
        try:
            self.bus.close()
        except Exception:
            pass
    
    # convert MPU output to legible numbers (data lol)
    def write_byte(self, reg: int, val: int):
        self.bus.write_byte_data(self.address, reg, val & 0xFF)

    def read_block(self, reg: int, length: int):
        return list(self.bus.read_i2c_block_data(self.address, reg, length))

    def wake(self):
        self.write_byte(REG_PWR_MGMT_1, 0x00)
        time.sleep(0.05)

    def set_accel_range(self, afs_sel: int):
        afs_sel &= 0x03
        self.write_byte(REG_ACCEL_CONFIG, afs_sel << 3)
        time.sleep(0.02)

    def read_accel_raw(self):
        data = self.read_block(REG_ACCEL_XOUT_H, 6)
        ax = to_int16((data[0] << 8) | data[1])
        ay = to_int16((data[2] << 8) | data[3])
        az = to_int16((data[4] << 8) | data[5])
        return ax, ay, az

# calibration
def calibrate_accel(mpu, samples, lsb_per_g, hz):
    dt = 1.0 / hz
    sum_x = sum_y = sum_z = 0
    expected_x = 0
    expected_y = 0
    expected_z = lsb_per_g

    for _ in range(samples):
        ax, ay, az = mpu.read_accel_raw()
        sum_x += ax
        sum_y += ay
        sum_z += az
        time.sleep(dt)

    mean_x = sum_x / samples
    mean_y = sum_y / samples
    mean_z = sum_z / samples

    off_x = mean_x - expected_x
    off_y = mean_y - expected_y
    off_z = mean_z - expected_z
    return off_x, off_y, off_z, (mean_x, mean_y, mean_z)

# staggers communication to printer using g-code to prevent overload or crash
def send_gcode(ser, cmd, delay=0.05):
    ser.write((cmd + "\n").encode())
    ser.flush()
    time.sleep(delay)

# main shake test function that runs in a separate thread to keep GUI responsive
def run_shake_test(serial_port, baudrate, shake_distance, velocity, acceleration, jerk,
                   duration, center_y, raise_z_first, z_lift, z_feedrate,
                   home_first, stop_event, status_cb=None):
    ser = None
    try:
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial not available. Install it with: pip3 install pyserial")

        if status_cb:
            status_cb(f"Connecting to printer on {serial_port} ...")
        ser = serial.Serial(serial_port, baudrate, timeout=2)
        time.sleep(3)

        send_gcode(ser, "M110 N0")
        send_gcode(ser, "M155 S0")

        if home_first:
            send_gcode(ser, "G28")

        send_gcode(ser, "G90")

        if raise_z_first:
            send_gcode(ser, f"G1 Z{z_lift} F{z_feedrate}")
            send_gcode(ser, "M400")
            time.sleep(1)

        send_gcode(ser, f"M204 P{acceleration} T{acceleration}")
        send_gcode(ser, f"M205 X{jerk} Y{jerk}")
        send_gcode(ser, f"G1 Y{center_y} F3000")
        send_gcode(ser, "M400")

        feedrate = velocity * 60.0
        half = shake_distance / 2.0
        start_time = time.time()

        if status_cb:
            status_cb("Shake test running.")

        while (time.time() - start_time < duration) and (not stop_event.is_set()):
            send_gcode(ser, f"G1 Y{center_y + half} F{feedrate}")
            if stop_event.is_set():
                break
            send_gcode(ser, f"G1 Y{center_y - half} F{feedrate}")

        if status_cb:
            status_cb("Shake motion complete.")

    except Exception as e:
        if status_cb:
            status_cb(f"Shake test error: {e}")
    finally:
        try:
            if ser is not None:
                send_gcode(ser, "M400")
                send_gcode(ser, f"G1 Y{center_y} F3000")
                ser.close()
        except Exception as e:
            if status_cb:
                status_cb(f"Serial cleanup error: {e}")



# parsing thru csv files
def read_csv_with_metadata(path):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))

    header_idx = None
    for i, row in enumerate(rows):
        if row and "t_sec" in row:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find a CSV header row containing t_sec.")

    headers = rows[header_idx]
    data_rows = []
    for row in rows[header_idx + 1:]:
        if not row or all(not cell.strip() for cell in row):
            continue
        padded = row + [""] * (len(headers) - len(row))
        data_rows.append(dict(zip(headers, padded[:len(headers)])))
    return headers, data_rows

# parsing thru csv file to get data from a specific time frame
def extract_rows_by_nearest_time(data_rows, start_time, end_time):
    indexed = []
    for i, row in enumerate(data_rows):
        try:
            t = float(row["t_sec"])
            indexed.append((i, t))
        except Exception:
            continue

    if not indexed:
        raise ValueError("No valid t_sec values were found in the CSV.")

    start_idx = None
    end_idx = None

    for idx, t in indexed:
        if start_idx is None and t >= start_time:
            start_idx = idx
        if end_idx is None and t >= end_time:
            end_idx = idx
            break

    if start_idx is None:
        start_idx = indexed[-1][0]
    if end_idx is None:
        end_idx = indexed[-1][0]
    if end_idx < start_idx:
        end_idx = start_idx

    return data_rows[start_idx:end_idx + 1]


# default shake test settings set at 'default'

@dataclass
class StartupDefaults:
    distance: float = 4.0
    velocity: float = 200.0
    acceleration: float = 2000.0
    jerk: float = 20.0
    duration: float = 20.0
    hz: float = 100.0
    calib_samples: int = 300
    plot_window: float = 5.0
    serial_port: str = auto_detect_serial_port()
    baudrate: int = 115200
    center_y: float = 25.0
    z_lift: float = 50.0
    z_feedrate: float = 600.0
    afs_sel: int = 1

# creating the FlyStress GUI 
class FlyStressApp:
    BG_COLOR = "#b0a9bd"
    TEXT_COLOR = "#3a2759"
    BUTTON_COLOR = "#61527a"
    BUTTON_HOVER_COLOR = "#4f4165"

    FONT = "Courier"
    HEADER_FONT = ("Courier", 32, "bold")
    NORMAL_FONT = ("Courier", 15)
    BUTTON_FONT = ("Courier", 16, "bold")
    SMALL_FONT = ("Courier", 12)

    def __init__(self, root, defaults: StartupDefaults):
        self.root = root
        self.root.title("FlyStress (ᴗ˳ᴗ)ᶻ𝗓 ")
        self.root.geometry("1400x900")
        self.root.configure(bg=self.BG_COLOR)

        self.defaults = defaults
        self.stop_event = threading.Event()
        self.sampling_thread = None
        self.shake_thread = None
        self.camera_process = None
        self.status_queue = queue.Queue()

        self.t_buf = deque()
        self.ax_buf = deque()
        self.ay_buf = deque()
        self.az_buf = deque()
        self.ax_g_all = []
        self.ay_g_all = []
        self.az_g_all = []
        self.last_csv_path = None

        self.entries = {}
        self.status_var = tk.StringVar(value="Ready.")

        self.fig = None
        self.ax = None
        self.canvas = None
        self.line_x = None
        self.line_y = None
        self.line_z = None

        self.show_home_page()
        self._schedule_gui_updates()

    def clear_page(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def style_root(self):
        self.root.configure(bg=self.BG_COLOR)

    def make_frame(self, parent):
        return tk.Frame(parent, bg=self.BG_COLOR)

    def make_label_frame(self, parent, text):
        return tk.LabelFrame(
            parent,
            text=text,
            bg=self.BG_COLOR,
            fg=self.TEXT_COLOR,
            font=self.BUTTON_FONT,
            labelanchor="n",
            padx=10,
            pady=10
        )

    def make_label(self, parent, text, font=None, justify="center"):
        return tk.Label(
            parent,
            text=text,
            bg=self.BG_COLOR,
            fg=self.TEXT_COLOR,
            font=font or self.NORMAL_FONT,
            justify=justify
        )

    def make_entry(self, parent, width=18):
        return tk.Entry(
            parent,
            width=width,
            bg="white",
            fg=self.TEXT_COLOR,
            font=self.NORMAL_FONT,
            insertbackground=self.TEXT_COLOR,
            relief="flat"
        )

    def make_button(self, parent, text, command, width=200, height=50):
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=height,
            corner_radius=20,
            fg_color=self.BUTTON_COLOR,
            hover_color=self.BUTTON_HOVER_COLOR,
            text_color=self.TEXT_COLOR,
            font=(self.FONT, 16, "bold")
        )

    def show_home_page(self):
        self.clear_page()
        self.style_root()

        header = self.make_frame(self.root)
        header.pack(fill="x", pady=(40, 10))

        self.make_label(
            header,
            "FlyStress (ᴗ˳ᴗ)ᶻ𝗓",
            self.HEADER_FONT
        ).pack()

        menu = self.make_frame(self.root)
        menu.pack(expand=True)

        self.make_button(menu, "Shake Test", self.shake_test_page).pack(pady=20)
        self.make_button(menu, "Tools", self.tools_page).pack(pady=20)
        self.make_button(menu, "Info Page", self.info_page).pack(pady=20)
        self.make_button(menu, "Exit", self.on_close).pack(pady=20)

    def make_page_header(self, title):
        header = self.make_frame(self.root)
        header.pack(fill="x", padx=20, pady=20)

        self.make_button(
            header,
            "Back",
            self.show_home_page,
            width=120,
            height=45
        ).pack(side="left", padx=20)

        self.make_label(
            header,
            title,
            self.HEADER_FONT
        ).pack(expand=True)

    def shake_test_page(self):
        self.clear_page()
        self.style_root()
        self.make_page_header("Shake Test")

        self.entries = {}

        main_frame = self.make_frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        settings_frame = self.make_label_frame(main_frame, "Settings")
        settings_frame.pack(side="left", fill="y", padx=(0, 20), pady=10)

        fields = [
        
            ("distance", "Distance (mm)", self.defaults.distance),
            ("velocity", "Velocity (mm/s)", self.defaults.velocity),
            ("acceleration", "Acceleration (mm/s²)", self.defaults.acceleration),
            ("jerk", "Jerk (mm/s)", self.defaults.jerk),
            ("duration", "Duration (s)", self.defaults.duration),
            ("afs_sel", "MPU Range", self.defaults.afs_sel),
        ]

        for r, (key, label, value) in enumerate(fields):
            self.make_label(
                settings_frame,
                label,
                self.NORMAL_FONT,
                justify="left"
            ).grid(row=r, column=0, sticky="w", padx=5, pady=4)

            entry = self.make_entry(settings_frame)
            entry.insert(0, str(value))
            entry.grid(row=r, column=1, sticky="w", padx=5, pady=4)
            self.entries[key] = entry

        key_text = "MPU Range Key: 2g = 0 , 4g = 1,\n 8g = 2 , 16g = 3" \
        "\n\n a(x,y,z)_g - force in g on x,y,z axis" 
        self.make_label(settings_frame, key_text, self.SMALL_FONT).grid(
            row=len(fields),
            column=0,
            columnspan=2,
            pady=10
        )

        btns = self.make_frame(settings_frame)
        btns.grid(row=len(fields) + 1, column=0, columnspan=2, pady=10)

        self.make_button(btns, "Start Shake Test", self.start_test, width=180, height=45).grid(row=0, column=0, padx=5, pady=5)
        self.make_button(btns, "Stop Test", self.stop_test, width=180, height=45).grid(row=0, column=1, padx=5, pady=5)
        self.make_button(btns, "Open Camera", self.start_camera, width=180, height=45).grid(row=1, column=0, padx=5, pady=5)
        self.make_button(btns, "Record + Test", self.start_recording_and_test, width=180, height=45).grid(row=1, column=1, padx=5, pady=5)
        self.make_button(btns, "Refresh Port", self.refresh_serial_port, width=180, height=45).grid(row=2, column=0, columnspan=2, padx=5, pady=5)

        right_frame = self.make_frame(main_frame)
        right_frame.pack(side="left", fill="both", expand=True)

        status_frame = self.make_frame(right_frame)
        status_frame.pack(fill="x", pady=(0, 10))

        self.status_var = tk.StringVar(value="Ready.")

        self.make_label(status_frame, "Status:", self.BUTTON_FONT).pack(side="left", padx=(0, 8))
        self.make_label(status_frame, "", self.NORMAL_FONT).pack(side="left")

        status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=self.BG_COLOR,
            fg=self.TEXT_COLOR,
            font=self.NORMAL_FONT
        )
        status_label.pack(side="left", fill="x", expand=True)

        plot_frame = self.make_label_frame(right_frame, "Live Acceleration")
        plot_frame.pack(fill="both", expand=True)

        self.fig, self.ax = plt.subplots(figsize=(10, 5), dpi=100)
        self.fig.patch.set_facecolor(self.BG_COLOR)
        self.ax.set_facecolor(self.BG_COLOR)

        self.line_x, = self.ax.plot([], [], label="ax_g")
        self.line_y, = self.ax.plot([], [], label="ay_g")
        self.line_z, = self.ax.plot([], [], label="az_g")

        self.ax.set_title("Live Acceleration", fontname=self.FONT, fontsize=18, fontweight="bold", color=self.TEXT_COLOR)
        self.ax.set_xlabel("Seconds", fontname=self.FONT, color=self.TEXT_COLOR)
        self.ax.set_ylabel("Acceleration (g)", fontname=self.FONT, color=self.TEXT_COLOR)
        self.ax.tick_params(colors=self.TEXT_COLOR)
        self.ax.grid(True)
        self.ax.legend(loc="upper right")
        self.ax.set_xlim(0, 5)
        self.ax.set_ylim(-2, 2)

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.draw()

    def tools_page(self):
        self.clear_page()
        self.style_root()
        self.make_page_header("Tools")

        tools_frame = self.make_label_frame(self.root, "CSV / Tools")
        tools_frame.pack(pady=20)

        row1 = self.make_frame(tools_frame)
        row1.pack(fill="x", padx=8, pady=8)

        self.make_button(row1, "Create Line Graph", self.load_csv_plot, width=180, height=45).pack(side="left", padx=8)
        self.make_button(row1, "Create Box Plot", self.make_boxplot, width=180, height=45).pack(side="left", padx=8)

        row2 = self.make_frame(tools_frame)
        row2.pack(fill="x", padx=8, pady=8)

        self.make_label(row2, "Start Time").pack(side="left", padx=4)

        self.start_time_entry = self.make_entry(row2, width=10)
        self.start_time_entry.insert(0, "10")
        self.start_time_entry.pack(side="left", padx=4)

        self.make_label(row2, "End Time").pack(side="left", padx=4)

        self.end_time_entry = self.make_entry(row2, width=10)
        self.end_time_entry.insert(0, "15")
        self.end_time_entry.pack(side="left", padx=4)

        self.make_button(row2, "Extract Time Range", self.extract_time_range, width=190, height=45).pack(side="left", padx=8)

        self.status_var = tk.StringVar(value="Ready.")

        status_frame = self.make_frame(self.root)
        status_frame.pack(fill="x", padx=10, pady=(10, 5))

        self.make_label(status_frame, "Status:", self.BUTTON_FONT).pack(side="left", padx=(0, 8))

        status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=self.BG_COLOR,
            fg=self.TEXT_COLOR,
            font=self.NORMAL_FONT
        )
        status_label.pack(side="left", fill="x", expand=True)

        display_frame = self.make_label_frame(self.root, "Plot Display")
        display_frame.pack(fill="both", expand=True, padx=200, pady=20)

        self.make_label(
            display_frame,
            "DISPLAY BOX PLOT / CSV PLOT HERE.",
            ("Ink Free", 22, "bold")
        ).pack(fill="both", expand=True, padx=50, pady=50)


    def info_page(self):
        self.clear_page()
        self.style_root()
        self.make_page_header("Info")

        text_frame = self.make_frame(self.root)
        text_frame.pack(expand=True)

        self.make_label(
            text_frame,
            "Understanding FlyStress:\n\n",
            ("Ink Free", 28, "bold")
        ).pack(pady=10)

        self.make_label(
            text_frame,
            "The shake test begins with an 11 second calibration phase. Use the 'extract time frame' tool to exclude this data from analysis.\n\n"
            "The accelerometer (MPU 6050) range will need to be adjusted given the amount of force used. When changing the settings,\n the range will be changed to the estimated best fit, but you can override this.\nIf the range is too low, the data will clip at the max g-force and show a flat line. If the range is too high, the data will be very\nlow resolution and look choppy.\n\n"
            "The live plot graph showcases the amount of force being used.\n\n" \
            "Extracting a time frame from a .csv file will create a File_Search_CJ.py file.\n\n",
            self.NORMAL_FONT
        ).pack()

    def _schedule_gui_updates(self):
        self._drain_status_queue()
        self._refresh_live_plot()
        self.root.after(100, self._schedule_gui_updates)

    def _drain_status_queue(self):
        if not hasattr(self, "status_var"):
            return

        while not self.status_queue.empty():
            self.status_var.set(self.status_queue.get_nowait())

    def set_status(self, text):
        self.status_queue.put(text)

    def refresh_serial_port(self):
        if "serial_port" not in self.entries:
            self.set_status("Go to the Shake Test page first.")
            return

        self.entries["serial_port"].delete(0, tk.END)
        self.entries["serial_port"].insert(0, auto_detect_serial_port())
        self.set_status("Serial port guess refreshed.")

    def get_settings(self):
        
        vals = {
        # hidden settings
        "serial_port": self.defaults.serial_port,
        "baudrate": self.defaults.baudrate,
        "hz": self.defaults.hz,
        "calib_samples": self.defaults.calib_samples,
        "plot_window": self.defaults.plot_window,
        "center_y": self.defaults.center_y,
        "z_lift": self.defaults.z_lift,
        "z_feedrate": self.defaults.z_feedrate,

        # visible GUI settings
        "distance": float(self.entries["distance"].get()),
        "velocity": float(self.entries["velocity"].get()),
        "acceleration": float(self.entries["acceleration"].get()),
        "jerk": float(self.entries["jerk"].get()),
        "duration": float(self.entries["duration"].get()),
        "afs_sel": int(self.entries["afs_sel"].get()),
        }

        if vals["serial_port"] == "":
            raise ValueError("Serial Port cannot be empty.")

        if vals["baudrate"] <= 0:
            raise ValueError("Baudrate must be greater than 0.")

        if vals["distance"] <= 0:
            raise ValueError("Distance must be greater than 0.")

        if vals["velocity"] <= 0:
            raise ValueError("Velocity must be greater than 0.")

        if vals["acceleration"] <= 0:
            raise ValueError("Acceleration must be greater than 0.")

        if vals["duration"] <= 0:
            raise ValueError("Duration must be greater than 0.")

        if vals["hz"] <= 0:
            raise ValueError("Sampling Rate must be greater than 0.")

        if vals["calib_samples"] <= 0:
            raise ValueError("Calibration Samples must be greater than 0.")

        if vals["plot_window"] <= 0:
            raise ValueError("Plot Window must be greater than 0.")

        if vals["afs_sel"] not in (0, 1, 2, 3):
            raise ValueError("MPU Range / AFS_SEL must be 0, 1, 2, or 3.")

        return vals

    def reset_buffers(self):
        self.t_buf.clear()
        self.ax_buf.clear()
        self.ay_buf.clear()
        self.az_buf.clear()
        self.ax_g_all.clear()
        self.ay_g_all.clear()
        self.az_g_all.clear()

    def start_camera(self):
        try:
            self.camera_process = subprocess.Popen(["guvcview"])
            self.set_status("guvcview launched.")
        except Exception as e:
            messagebox.showerror("Camera Error", f"Could not start guvcview:\n{e}")
            self.set_status(f"Camera error: {e}")

    def start_recording_and_test(self):
        self.start_recording()
        self.root.after(10000, self.start_test)

    
    def start_recording(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        video_dir = os.path.expanduser("~/Desktop/FlyStressVideos")
        os.makedirs(video_dir, exist_ok=True)

        outfile = os.path.join(video_dir, f"video_{timestamp}.mp4")

        self.camera_process = subprocess.Popen([
            "ffmpeg",
            "-f", "v4l2",
            "-i", "/dev/video0",
            "-t", str(int(self.defaults.duration)),
            "-y",
            outfile
        ])

        self.set_status(f"Recording video: {outfile}")
    
    
    def stop_recording(self):
        print("Ending recording...")
        if self.camera_process is not None:
            try:
                self.camera_process.terminate()
                self.camera_process.wait(timeout=5)
            except Exception:
                try:
                    self.camera_process.kill()
                except Exception:
                    pass
            self.camera_process = None
            self.set_status("Recording stopped.")

    def start_test(self):
        if self.sampling_thread and self.sampling_thread.is_alive():
            messagebox.showinfo("FlyStress", "A test is already running.")
            return

        try:
            settings = self.get_settings()
        except Exception as e:
            messagebox.showerror("Settings Error", str(e))
            return

        dynamic_accel_mm_s2 = estimate_max_dynamic_accel(
            settings["distance"],
            settings["velocity"],
            settings["acceleration"],
            settings["jerk"]
        )

        total_peak_g = 1.0 + (dynamic_accel_mm_s2 / G_TO_VEL)
        recommended_range = choose_mpu6050_range(total_peak_g)
        recommended_afs_sel = choose_default(recommended_range)

        if settings["afs_sel"] != recommended_afs_sel:
            messagebox.showwarning(
                "MPU Range Recommendation",
                f"Recommended MPU Range is {recommended_afs_sel} (±{recommended_range}g), "
                f"but GUI is set to {settings['afs_sel']}.\n"
                "You can continue, but clipping or reduced resolution may occur."
            )

        self.stop_event.clear()
        self.reset_buffers()
        self.last_csv_path = find_next_filename(directory=".")
        self.set_status(f"Preparing test. Output CSV: {self.last_csv_path}")

        self.sampling_thread = threading.Thread(
            target=self._run_sampling_and_logging,
            args=(settings,),
            daemon=True
        )
        self.sampling_thread.start()

    def stop_test(self):
        self.stop_event.set()

        if self.camera_process:
            try:
                self.camera_process.terminate()
            except Exception:
                pass

        self.set_status("Stop requested.")

    def _run_sampling_and_logging(self, settings):
        print("sampling thread begins...")
        mpu = None
        csv_file = None

        try:
            if not SMBUS_AVAILABLE:
                raise RuntimeError("smbus2 is not installed or not available.")

            mpu = MPU6050(bus_id=1, address=0x68)

            lsb_per_g = AFS_SEL_TO_LSB_PER_G[settings["afs_sel"]]
            dt_target = 1.0 / settings["hz"]
            total_samples = max(1, int(settings["duration"] * settings["hz"]))

            self.set_status("Waking MPU6050 ...")
            mpu.wake()
            mpu.set_accel_range(settings["afs_sel"])

            self.set_status("Calibrating MPU6050. Keep it still ...")

            off_x, off_y, off_z, means = calibrate_accel(
                mpu,
                samples=settings["calib_samples"],
                lsb_per_g=lsb_per_g,
                hz=settings["hz"]
            )

            self.set_status(
                f"Calibration done. Mean raw: ax={means[0]:.1f}, "
                f"ay={means[1]:.1f}, az={means[2]:.1f}"
            )

            csv_file = open(self.last_csv_path, "w", newline="")
            writer = csv.writer(csv_file)

            writer.writerow(["# Combined MPU-6050 shake test log"])
            writer.writerow(["# start_time", datetime.now().isoformat(timespec="seconds")])
            writer.writerow(["# accel_afs_sel", settings["afs_sel"]])
            writer.writerow(["# accel_range_g", AFS_SEL_TO_RANGE_G[settings["afs_sel"]]])
            writer.writerow(["# lsb_per_g", lsb_per_g])
            writer.writerow(["# offsets_counts", f"{off_x:.4f}", f"{off_y:.4f}", f"{off_z:.4f}"])
            writer.writerow(["# shake_distance_mm", settings["distance"]])
            writer.writerow(["# velocity_mm_s", settings["velocity"]])
            writer.writerow(["# acceleration_mm_s2", settings["acceleration"]])
            writer.writerow(["# jerk_mm_s", settings["jerk"]])
            writer.writerow(["# duration_s", settings["duration"]])
            writer.writerow(["Key: a(x,y,z)_raw - raw data from accelerometer on _ axis"])
            writer.writerow(["a(x,y,z)_corr - corrected value after calibration"])
            writer.writerow(["a(x,y,z)_g - force in g on x,y,z axis, t_sec - time in seconds"])
            writer.writerow([])

            writer.writerow([
                "t_sec", "ax_raw", "ay_raw", "az_raw",
                "ax_corr", "ay_corr", "az_corr",
                "ax_g", "ay_g", "az_g"
            ])

            self.shake_thread = threading.Thread(
                target=run_shake_test,
                args=(
                    settings["serial_port"],
                    settings["baudrate"],
                    settings["distance"],
                    settings["velocity"],
                    settings["acceleration"],
                    settings["jerk"],
                    settings["duration"],
                    settings["center_y"],
                    True,
                    settings["z_lift"],
                    settings["z_feedrate"],
                    True,
                    self.stop_event,
                    self.set_status
                ),
                daemon=True
            )

            self.shake_thread.start()

            t0 = time.perf_counter()

            for sample_idx in range(total_samples):
                if self.stop_event.is_set():
                    break

                now = time.perf_counter()
                t_sec = now - t0

                ax_raw, ay_raw, az_raw = mpu.read_accel_raw()

                ax_corr = ax_raw - off_x
                ay_corr = ay_raw - off_y
                az_corr = az_raw - off_z

                ax_g = ax_corr / lsb_per_g
                ay_g = ay_corr / lsb_per_g
                az_g = az_corr / lsb_per_g

                writer.writerow([
                    f"{t_sec:.6f}",
                    int(ax_raw),
                    int(ay_raw),
                    int(az_raw),
                    f"{ax_corr:.4f}",
                    f"{ay_corr:.4f}",
                    f"{az_corr:.4f}",
                    f"{ax_g:.6f}",
                    f"{ay_g:.6f}",
                    f"{az_g:.6f}"
                ])

                self.t_buf.append(t_sec)
                self.ax_buf.append(ax_g)
                self.ay_buf.append(ay_g)
                self.az_buf.append(az_g)

                self.ax_g_all.append(ax_g)
                self.ay_g_all.append(ay_g)
                self.az_g_all.append(az_g)

                while self.t_buf and (t_sec - self.t_buf[0]) > settings["plot_window"]:
                    self.t_buf.popleft()
                    self.ax_buf.popleft()
                    self.ay_buf.popleft()
                    self.az_buf.popleft()

                if sample_idx % 200 == 0:
                    csv_file.flush()
                    os.fsync(csv_file.fileno())

                target_next = t0 + (sample_idx + 1) * dt_target
                sleep_for = target_next - time.perf_counter()

                if sleep_for > 0:
                    time.sleep(sleep_for)

            self.stop_event.set()

            if self.shake_thread and self.shake_thread.is_alive():
                self.shake_thread.join(timeout=5)

            self.set_status(f"Test complete. Saved CSV: {self.last_csv_path}")

            if self.ax_g_all and self.ay_g_all and self.az_g_all:
                box_plot_path = find_next_plot_filename(directory=".")

                fig2, ax2 = plt.subplots(figsize=(10, 7), dpi=150)
                fig2.patch.set_facecolor(self.BG_COLOR)
                ax2.set_facecolor(self.BG_COLOR)

                ax2.boxplot(
                    [self.ax_g_all, self.ay_g_all, self.az_g_all],
                    labels=["ax_g", "ay_g", "az_g"]
                )

                ax2.set_title(
                    "Box Plot of Acceleration (g)",
                    fontname=self.FONT,
                    fontsize=18,
                    fontweight="bold",
                    color=self.TEXT_COLOR
                )
                ax2.set_ylabel("Acceleration (g)", fontname=self.FONT, color=self.TEXT_COLOR)
                ax2.tick_params(colors=self.TEXT_COLOR)
                ax2.grid(True, axis="y")

                fig2.tight_layout()
                fig2.savefig(box_plot_path, dpi=300)
                plt.close(fig2)

                self.set_status(
                    f"Test complete. CSV: {self.last_csv_path} | Box plot: {box_plot_path}"
                )

        except Exception as e:
            error_msg = str(e)
            print("ERROR:", error_msg)
            self.set_status(f"Test error: {error_msg}")
            self.root.after(0, lambda msg=error_msg: messagebox.showerror("Error occured.", msg))

        finally:
            self.stop_event.set()

            self.stop_recording()

            try:
                if csv_file is not None:
                    csv_file.flush()
                    csv_file.close()
            except Exception:
                pass

            try:
                if mpu is not None:
                    mpu.close()
            except Exception:
                pass

    def _refresh_live_plot(self):
        if self.line_x is None or self.canvas is None:
            return

        if not self.t_buf:
            return

        try:
            t_vals = list(self.t_buf)
            ax_vals = list(self.ax_buf)
            ay_vals = list(self.ay_buf)
            az_vals = list(self.az_buf)

            self.line_x.set_data(t_vals, ax_vals)
            self.line_y.set_data(t_vals, ay_vals)
            self.line_z.set_data(t_vals, az_vals)

            plot_window = 5.0

            if "plot_window" in self.entries:
                plot_window = float(self.entries["plot_window"].get() or 5)

            self.ax.set_xlim(
                max(0, t_vals[0]),
                max(plot_window, t_vals[-1])
            )

            all_vals = ax_vals + ay_vals + az_vals
            ymin, ymax = min(all_vals), max(all_vals)
            pad = max(0.1, 0.15 * (ymax - ymin + 1e-6))

            self.ax.set_ylim(ymin - pad, ymax + pad)
            self.canvas.draw_idle()

        except Exception:
            pass

    def load_csv_plot(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])

        if not path:
            return

        try:
            _, rows = read_csv_with_metadata(path)

            t, ax_g, ay_g, az_g = [], [], [], []

            for row in rows:
                try:
                    t.append(float(row["t_sec"]))
                    ax_g.append(float(row["ax_g"]))
                    ay_g.append(float(row["ay_g"]))
                    az_g.append(float(row["az_g"]))
                except Exception:
                    continue

            if not t:
                raise ValueError("No valid accelerometer rows were found.")

            fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
            fig.patch.set_facecolor(self.BG_COLOR)
            ax.set_facecolor(self.BG_COLOR)

            ax.plot(t, ax_g, label="ax_g")
            ax.plot(t, ay_g, label="ay_g")
            ax.plot(t, az_g, label="az_g")

            ax.set_title("CSV Plot", fontname=self.FONT, fontsize=18, fontweight="bold", color=self.TEXT_COLOR)
            ax.set_xlabel("Seconds", fontname=self.FONT, color=self.TEXT_COLOR)
            ax.set_ylabel("Acceleration (g)", fontname=self.FONT, color=self.TEXT_COLOR)
            ax.tick_params(colors=self.TEXT_COLOR)
            ax.grid(True)
            ax.legend()

            plt.show()
            self.set_status(f"Loaded CSV plot: {os.path.basename(path)}")

        except Exception as e:
            messagebox.showerror("CSV Plot Error", str(e))

    def make_boxplot(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])

        if not path:
            return

        try:
            _, rows = read_csv_with_metadata(path)

            ax_g, ay_g, az_g = [], [], []

            for row in rows:
                try:
                    ax_g.append(float(row["ax_g"]))
                    ay_g.append(float(row["ay_g"]))
                    az_g.append(float(row["az_g"]))
                except Exception:
                    continue

            if not (ax_g and ay_g and az_g):
                raise ValueError("CSV must contain ax_g, ay_g, and az_g data.")

            fig, ax = plt.subplots(figsize=(10, 7), dpi=120)
            fig.patch.set_facecolor(self.BG_COLOR)
            ax.set_facecolor(self.BG_COLOR)

            ax.boxplot([ax_g, ay_g, az_g], labels=["ax_g", "ay_g", "az_g"])

            ax.set_title("Box Plot of Acceleration (g)", fontname=self.FONT, fontsize=18, fontweight="bold", color=self.TEXT_COLOR)
            ax.set_ylabel("Acceleration (g)", fontname=self.FONT, color=self.TEXT_COLOR)
            ax.tick_params(colors=self.TEXT_COLOR)
            ax.grid(True, axis="y")

            fig.tight_layout()
            plt.show()

            self.set_status(f"Created box plot from: {os.path.basename(path)}")

        except Exception as e:
            messagebox.showerror("Box Plot Error", str(e))

    def extract_time_range(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])

        if not path:
            return

        try:
            start_time = float(self.start_time_entry.get())
            end_time = float(self.end_time_entry.get())

            if end_time < start_time:
                raise ValueError("End time must be greater than or equal to start time.")

            headers, rows = read_csv_with_metadata(path)
            extracted = extract_rows_by_nearest_time(rows, start_time, end_time)

            if not extracted:
                raise ValueError("No rows matched the requested time range.")

            out_path = os.path.splitext(path)[0] + f"_extract_{start_time:g}_to_{end_time:g}.csv"

            with open(out_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(extracted)

            self.set_status(f"Extracted {len(extracted)} rows to {out_path}")

            messagebox.showinfo(
                "Extract Complete",
                f"Saved extracted CSV:\n{out_path}"
            )

        except Exception as e:
            messagebox.showerror("Extract Error", str(e))

    def on_close(self):
        self.stop_event.set()

        try:
            if self.camera_process is not None:
                self.camera_process.terminate()
        except Exception:
            pass

        try:
            if self.fig is not None:
                plt.close(self.fig)
        except Exception:
            pass

        self.root.destroy()


def main():
    defaults = StartupDefaults()
    root = tk.Tk()
    app = FlyStressApp(root, defaults)
    root.mainloop()


if __name__ == "__main__":
    main()
