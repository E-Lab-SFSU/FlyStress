#!/usr/bin/env python3
"""
FlyStress shared-memory camera + MPU6050 + Ender 3 shake test logger

What this script does:
1. Creates a shared-memory recording flag.
2. Starts a separate camera worker process that watches that flag.
3. Calibrates the MPU6050 while everything is still.
4. Signals the camera worker to begin recording from the attached USB camera.
5. Starts the Ender 3 Y-axis shake test.
6. Records MPU6050 accelerometer, gyroscope, and temperature data to CSV.
7. Signals the camera worker to stop recording.
8. Creates a line plot PNG from the recorded MPU data.

Run on Raspberry Pi:
    python3 flystress_shared_memory_camera.py

Requires:
    sudo apt install ffmpeg
    pip install smbus2 pyserial matplotlib

Before running, verify camera:
    ffmpeg -f v4l2 -i /dev/video0 -t 5 test.mp4
"""

import csv
import math
import os
import subprocess
import threading
import time
from datetime import datetime
from multiprocessing import Process, Value

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import serial
from smbus2 import SMBus

# =========================
# USER SETTINGS
# =========================

# Printer serial settings
SERIAL_PORT = "/dev/ttyUSB0"   # Try /dev/ttyACM0 if needed
BAUDRATE = 115200

# Shake motion settings
SHAKE_DISTANCE = 4.0       # mm, total peak-to-peak Y travel
VELOCITY = 220.0           # mm/s
ACCELERATION = 2000.0      # mm/s^2. Ender 3-safe default.
JERK = 20.0                # mm/s
DURATION = 20.0            # seconds of MPU logging and shake command loop

# Positioning
CENTER_Y = 25.0
HOME_FIRST = True
RAISE_Z_FIRST = True
Z_LIFT = 50.0              # mm
Z_FEEDRATE = 600.0         # mm/min

# MPU6050 settings
MPU_BUS_ID = 1
MPU_ADDRESS = 0x68         # usually 0x68. If AD0 is high, use 0x69.
SAMPLE_RATE_HZ = 100.0
CALIBRATION_SAMPLES = 300
ACCEL_AFS_SEL = 1          # 0=+-2g, 1=+-4g, 2=+-8g, 3=+-16g
GYRO_FS_SEL = 0            # 0=+-250 dps, 1=+-500, 2=+-1000, 3=+-2000

# Camera settings for USB UVC camera
CAMERA_DEVICE = "/dev/video0"
CAMERA_FRAMERATE = 30
# Leave CAMERA_SIZE empty to let ffmpeg auto-select, or set like "640x480".
CAMERA_SIZE = ""

# Output folder
OUTPUT_DIR = os.path.expanduser("~/Desktop/FlyStressData")

# Shared-memory recording flag values
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
        afs_sel &= 0x03
        self.write_byte(REG_ACCEL_CONFIG, afs_sel << 3)
        time.sleep(0.02)

    def set_gyro_range(self, fs_sel):
        fs_sel &= 0x03
        self.write_byte(REG_GYRO_CONFIG, fs_sel << 3)
        time.sleep(0.02)

    def read_accel_raw(self):
        data = self.read_block(REG_ACCEL_XOUT_H, 6)
        ax = to_int16((data[0] << 8) | data[1])
        ay = to_int16((data[2] << 8) | data[3])
        az = to_int16((data[4] << 8) | data[5])
        return ax, ay, az

    def read_gyro_raw(self):
        data = self.read_block(REG_GYRO_XOUT_H, 6)
        gx = to_int16((data[0] << 8) | data[1])
        gy = to_int16((data[2] << 8) | data[3])
        gz = to_int16((data[4] << 8) | data[5])
        return gx, gy, gz

    def read_temperature(self):
        raw_temp = self.read_word_2c(REG_TEMP_OUT_H)
        temp_c = (raw_temp / 340.0) + 36.53
        temp_f = (temp_c * 9.0 / 5.0) + 32.0
        return raw_temp, temp_c, temp_f


# =========================
# SHARED-MEMORY CAMERA WORKER
# =========================


def camera_worker(recording_flag, video_path):
    """Separate process that watches shared memory and controls ffmpeg."""
    ffmpeg_proc = None
    print("Camera worker ready. Waiting for shared-memory START flag...")

    while True:
        flag = recording_flag.value

        if flag == REC_START and ffmpeg_proc is None:
            cmd = [
                "ffmpeg",
                "-f", "v4l2",
                "-framerate", str(CAMERA_FRAMERATE),
            ]

            if CAMERA_SIZE:
                cmd.extend(["-video_size", CAMERA_SIZE])

            cmd.extend([
                "-i", CAMERA_DEVICE,
                "-y",
                video_path,
            ])

            print("Starting camera recording:", " ".join(cmd))
            ffmpeg_proc = subprocess.Popen(cmd)
            recording_flag.value = REC_RECORDING

        elif flag == REC_STOP and ffmpeg_proc is not None:
            print("Stopping camera recording...")
            try:
                ffmpeg_proc.terminate()
                ffmpeg_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
                ffmpeg_proc.wait(timeout=5)
            finally:
                ffmpeg_proc = None
                recording_flag.value = REC_IDLE
                print(f"Saved video to: {video_path}")

        elif flag == REC_EXIT:
            if ffmpeg_proc is not None:
                print("Camera worker exiting; stopping active recording...")
                try:
                    ffmpeg_proc.terminate()
                    ffmpeg_proc.wait(timeout=10)
                except Exception:
                    try:
                        ffmpeg_proc.kill()
                    except Exception:
                        pass
            print("Camera worker exited.")
            break

        time.sleep(0.05)


# =========================
# MPU CALIBRATION
# =========================


def calibrate_mpu(mpu, samples, sample_rate_hz, accel_lsb_per_g):
    print(f"Calibrating MPU6050 with {samples} samples. Keep the MPU still...")
    dt = 1.0 / sample_rate_hz

    sum_ax = sum_ay = sum_az = 0.0
    sum_gx = sum_gy = sum_gz = 0.0

    for _ in range(samples):
        ax, ay, az = mpu.read_accel_raw()
        gx, gy, gz = mpu.read_gyro_raw()
        sum_ax += ax
        sum_ay += ay
        sum_az += az
        sum_gx += gx
        sum_gy += gy
        sum_gz += gz
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

    print(
        "Calibration complete. Raw means: "
        f"ax={mean_ax:.1f}, ay={mean_ay:.1f}, az={mean_az:.1f}, "
        f"gx={mean_gx:.1f}, gy={mean_gy:.1f}, gz={mean_gz:.1f}"
    )
    return offsets


# =========================
# PRINTER / SHAKE TEST
# =========================


def send_gcode(ser, cmd, delay=0.05):
    print(">>", cmd)
    ser.write((cmd + "\n").encode())
    ser.flush()
    time.sleep(delay)


def run_shake_test(stop_event):
    ser = None
    try:
        print(f"Connecting to printer on {SERIAL_PORT}...")
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=2)
        time.sleep(3)

        send_gcode(ser, "M110 N0")
        send_gcode(ser, "M155 S0")

        if HOME_FIRST:
            send_gcode(ser, "G28")

        send_gcode(ser, "G90")

        if RAISE_Z_FIRST:
            send_gcode(ser, f"G1 Z{Z_LIFT} F{Z_FEEDRATE}")
            send_gcode(ser, "M400")
            time.sleep(3)

        send_gcode(ser, f"M204 P{ACCELERATION} T{ACCELERATION}")
        send_gcode(ser, f"M205 X{JERK} Y{JERK}")
        send_gcode(ser, f"G1 Y{CENTER_Y} F3000")
        send_gcode(ser, "M400")

        feedrate = VELOCITY * 60.0
        half = SHAKE_DISTANCE / 2.0

        print("BED SHAKING STARTED")
        start_time = time.time()

        while (time.time() - start_time < DURATION) and not stop_event.is_set():
            send_gcode(ser, f"G1 Y{CENTER_Y + half} F{feedrate}")
            if stop_event.is_set():
                break
            send_gcode(ser, f"G1 Y{CENTER_Y - half} F{feedrate}")

        print("Shake motion complete.")

    except Exception as e:
        print(f"Shake test error: {e}")
        stop_event.set()

    finally:
        try:
            if ser is not None:
                print("Stopping motion and returning to center...")
                send_gcode(ser, "M400")
                send_gcode(ser, f"G1 Y{CENTER_Y} F3000")
                ser.close()
                print("Printer connection closed.")
        except Exception as e:
            print(f"Printer cleanup error: {e}")


# =========================
# OUTPUT / CSV / PLOT
# =========================


def create_output_paths():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(OUTPUT_DIR, f"flystress_shake_mpu_{timestamp}.csv")
    plot_path = os.path.join(OUTPUT_DIR, f"flystress_shake_mpu_{timestamp}.png")
    video_path = os.path.join(OUTPUT_DIR, f"flystress_video_{timestamp}.mp4")
    return csv_path, plot_path, video_path


def write_metadata(writer, offsets, accel_lsb_per_g, gyro_lsb_per_dps, video_path):
    writer.writerow(["# FlyStress shared-memory camera + shake + MPU6050 log"])
    writer.writerow(["# start_time", datetime.now().isoformat(timespec="seconds")])
    writer.writerow(["# video_path", video_path])
    writer.writerow(["# camera_device", CAMERA_DEVICE])
    writer.writerow(["# serial_port", SERIAL_PORT])
    writer.writerow(["# baudrate", BAUDRATE])
    writer.writerow(["# shake_distance_mm", SHAKE_DISTANCE])
    writer.writerow(["# velocity_mm_s", VELOCITY])
    writer.writerow(["# acceleration_mm_s2", ACCELERATION])
    writer.writerow(["# jerk_mm_s", JERK])
    writer.writerow(["# duration_s", DURATION])
    writer.writerow(["# center_y", CENTER_Y])
    writer.writerow(["# sample_rate_hz", SAMPLE_RATE_HZ])
    writer.writerow(["# calibration_samples", CALIBRATION_SAMPLES])
    writer.writerow(["# accel_afs_sel", ACCEL_AFS_SEL])
    writer.writerow(["# accel_range_g", ACCEL_RANGE_G[ACCEL_AFS_SEL]])
    writer.writerow(["# accel_lsb_per_g", accel_lsb_per_g])
    writer.writerow(["# gyro_fs_sel", GYRO_FS_SEL])
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


def log_mpu_during_shake(mpu, offsets, csv_path, accel_lsb_per_g, gyro_lsb_per_dps, stop_event, video_path):
    dt_target = 1.0 / SAMPLE_RATE_HZ
    total_samples = max(1, int(DURATION * SAMPLE_RATE_HZ))

    t_values = []
    ax_values = []
    ay_values = []
    az_values = []
    temp_values = []

    print(f"Saving MPU data to: {csv_path}")

    with open(csv_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        write_metadata(writer, offsets, accel_lsb_per_g, gyro_lsb_per_dps, video_path)
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
            if stop_event.is_set():
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

            if sample_idx % 100 == 0:
                csv_file.flush()
                os.fsync(csv_file.fileno())
                print(
                    f"t={t_sec:7.3f}s  "
                    f"ax={ax_g:8.4f}g  ay={ay_g:8.4f}g  az={az_g:8.4f}g  "
                    f"temp={temp_c:6.2f}C"
                )

            target_next = t0 + (sample_idx + 1) * dt_target
            sleep_for = target_next - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)

    return t_values, ax_values, ay_values, az_values, temp_values


def create_line_plot(plot_path, t_values, ax_values, ay_values, az_values, temp_values):
    if not t_values:
        print("No samples were collected, so no plot was created.")
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
    print(f"Saved line plot to: {plot_path}")


# =========================
# MAIN
# =========================


def wait_for_camera_state(recording_flag, desired_state, timeout_sec):
    start = time.time()
    while time.time() - start < timeout_sec:
        if recording_flag.value == desired_state:
            return True
        time.sleep(0.05)
    return False


def main():
    mpu = None
    stop_event = threading.Event()
    recording_flag = Value("i", REC_IDLE)

    csv_path, plot_path, video_path = create_output_paths()
    accel_lsb_per_g = ACCEL_LSB_PER_G[ACCEL_AFS_SEL]
    gyro_lsb_per_dps = GYRO_LSB_PER_DPS[GYRO_FS_SEL]

    camera_process = Process(target=camera_worker, args=(recording_flag, video_path), daemon=True)
    camera_process.start()

    try:
        print("Connecting to MPU6050...")
        mpu = MPU6050(bus_id=MPU_BUS_ID, address=MPU_ADDRESS)
        mpu.wake()
        mpu.set_accel_range(ACCEL_AFS_SEL)
        mpu.set_gyro_range(GYRO_FS_SEL)

        offsets = calibrate_mpu(
            mpu,
            samples=CALIBRATION_SAMPLES,
            sample_rate_hz=SAMPLE_RATE_HZ,
            accel_lsb_per_g=accel_lsb_per_g,
        )

        print("Signaling camera START through shared memory...")
        recording_flag.value = REC_START
        if not wait_for_camera_state(recording_flag, REC_RECORDING, timeout_sec=5):
            print("WARNING: camera did not report REC_RECORDING within 5 seconds.")

        shake_thread = threading.Thread(target=run_shake_test, args=(stop_event,), daemon=True)
        shake_thread.start()

        t_values, ax_values, ay_values, az_values, temp_values = log_mpu_during_shake(
            mpu,
            offsets,
            csv_path,
            accel_lsb_per_g,
            gyro_lsb_per_dps,
            stop_event,
            video_path,
        )

        stop_event.set()
        shake_thread.join(timeout=10)

        print("Signaling camera STOP through shared memory...")
        recording_flag.value = REC_STOP
        wait_for_camera_state(recording_flag, REC_IDLE, timeout_sec=15)

        create_line_plot(plot_path, t_values, ax_values, ay_values, az_values, temp_values)

        print("Done.")
        print(f"CSV:   {csv_path}")
        print(f"Plot:  {plot_path}")
        print(f"Video: {video_path}")

    except KeyboardInterrupt:
        print("Interrupted by user.")
        stop_event.set()
        recording_flag.value = REC_STOP

    except Exception as e:
        print(f"ERROR: {e}")
        stop_event.set()
        recording_flag.value = REC_STOP

    finally:
        if mpu is not None:
            mpu.close()

        time.sleep(0.5)
        recording_flag.value = REC_EXIT
        camera_process.join(timeout=5)
        if camera_process.is_alive():
            camera_process.terminate()
            camera_process.join(timeout=2)


if __name__ == "__main__":
    main()
