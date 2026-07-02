#!/usr/bin/env python3
"""
Reading & Displaying MPU6050 Acceleration and Gyroscope angles (PyQt6)
Author: Cherese Jordan 
Purpose: read from MPU6050 accelerometer sensor and gyroscope scale,
         display output, save data in .csv file.

- MPU reading sourced from Jaynath Tadikonda ; https://ch.mathworks.com/matlabcentral/answers/506391-raspberry-pi-with-mpu6050-sensor-data-visualisation-accelerometer-gyroscope
mpu data sheet and reg map download
https://invensense.tdk.com/download-resource/ps-mpu-6000a-00-mpu-6000-and-mpu-6050-datasheet

Requires:
    pip install PyQt6 smbus2

    Run:
    python3 MPU6050-acc-v2.py
"""

from smbus2 import SMBus
import math
import time
from datetime import datetime
import os
import csv
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QTextEdit, QMessageBox

# Registers -- these addresses do not change
power_mgmt_1 = 0x6B
power_mgmt_2 = 0x6C

BUS_ID = 1
ADDRESS = 0x68

SAMPLE_DELAY_MS = 100 


# ---- MPU6050 helpers ----
# connect to the MPU6050 and wake it up,
# then read the raw accelerometer and gyroscope values, and convert to g's and deg/s


#read a single byte from the MPU6050
def read_byte(bus, address, reg):
    return bus.read_byte_data(address, reg)

# convert to work with 2's complement values
def read_word(bus, address, reg):
    h = bus.read_byte_data(address, reg)
    l = bus.read_byte_data(address, reg + 1)
    return (h << 8) + l


def read_word_2c(bus, address, reg):
    val = read_word(bus, address, reg)
    if val >= 0x8000:
        return -((65535 - val) + 1)
    else:
        return val

# calculate the distance between two points in 2D space
def dist(a, b):
    return math.sqrt((a * a) + (b * b))

# calculate the rotation around the X & Y-axis (pitch) in degrees
def get_y_rotation(x, y, z):
    radians = math.atan2(x, dist(y, z))
    return -math.degrees(radians)


def get_x_rotation(x, y, z):
    radians = math.atan2(y, dist(x, z))
    return math.degrees(radians)

# Window class for displaying MPU6050 data
class MPUStreamWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Acceleration and Gyroscope Data")
        self.resize(600, 500)

        self.bus = None
        self.csv_file = None
        self.writer = None
        self.csv_filename = None
        self.timer = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)

        self.display = QTextEdit()
        self.display.setReadOnly(True)
        mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(12)
        self.display.setFont(mono)
        layout.addWidget(self.display)

        try:
            self.setup_mpu()
            self.setup_csv()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.update_data)
            self.timer.start(SAMPLE_DELAY_MS)
        except Exception as e:
            QMessageBox.critical(self, "Sensor Error", str(e))
            self.display.setPlainText(f"Error: {e}")

    def setup_mpu(self):
        self.bus = SMBus(BUS_ID)
        time.sleep(1)

        # Start the bus to send request for data / wake the MPU6050.
        self.bus.write_byte_data(ADDRESS, power_mgmt_1, 0)

    # set up structure for data files
    def setup_csv(self):
        self.csv_filename = datetime.now().strftime("mpu_data_%Y%m%d_%H%M%S.csv")
        self.csv_file = open(self.csv_filename, "w", newline="")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow([
            "timestamp", "accel_x_raw", "accel_y_raw", "accel_z_raw",
            "accel_x_g", "accel_y_g", "accel_z_g", "gyro_x_raw", "gyro_y_raw", "gyro_z_raw",
            "gyro_x_deg_s", "gyro_y_deg_s", "gyro_z_deg_s", "x_rotation", "y_rotation",
        ])

    # Update the data from the MPU6050 and write to CSV
    def update_data(self):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

            gyro_x = read_word_2c(self.bus, ADDRESS, 0x43)
            gyro_y = read_word_2c(self.bus, ADDRESS, 0x45)
            gyro_z = read_word_2c(self.bus, ADDRESS, 0x47)

            gyro_x_scaled = gyro_x / 131.0
            gyro_y_scaled = gyro_y / 131.0
            gyro_z_scaled = gyro_z / 131.0

            accel_x = read_word_2c(self.bus, ADDRESS, 0x3B)
            accel_y = read_word_2c(self.bus, ADDRESS, 0x3D)
            accel_z = read_word_2c(self.bus, ADDRESS, 0x3F)

            accel_x_scaled = accel_x / 16384.0
            accel_y_scaled = accel_y / 16384.0
            accel_z_scaled = accel_z / 16384.0

            x_rot = get_x_rotation(accel_x_scaled, accel_y_scaled, accel_z_scaled)
            y_rot = get_y_rotation(accel_x_scaled, accel_y_scaled, accel_z_scaled)

            self.writer.writerow([
                timestamp,
                accel_x, accel_y, accel_z,
                accel_x_scaled, accel_y_scaled, accel_z_scaled,
                gyro_x, gyro_y, gyro_z,
                gyro_x_scaled, gyro_y_scaled, gyro_z_scaled,
                x_rot, y_rot,
            ])
            self.csv_file.flush()

            lines = [
                f"Saving to: {self.csv_filename}",
                "",
                "Accelerometer",
                f"X raw: {accel_x:6d}  X g: {accel_x_scaled:.4f}",
                f"Y raw: {accel_y:6d}  Y g: {accel_y_scaled:.4f}",
                f"Z raw: {accel_z:6d}  Z g: {accel_z_scaled:.4f}",
                "",
                "Gyroscope",
                f"X raw: {gyro_x:6d}  X deg/s: {gyro_x_scaled:.4f}",
                f"Y raw: {gyro_y:6d}  Y deg/s: {gyro_y_scaled:.4f}",
                f"Z raw: {gyro_z:6d}  Z deg/s: {gyro_z_scaled:.4f}",
                "",
                f"X Rotation: {x_rot:.2f}",
                f"Y Rotation: {y_rot:.2f}",
            ]
            self.display.setPlainText("\n".join(lines))

        except Exception as e:
            self.display.setPlainText(f"Error: {e}")

    # Clean up resources on close
    def closeEvent(self, event):
        if self.timer is not None:
            self.timer.stop()

        try:
            if self.csv_file is not None:
                self.csv_file.flush()
                self.csv_file.close()
        except Exception:
            pass

        try:
            if self.bus is not None:
                self.bus.close()
        except Exception:
            pass

        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MPUStreamWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()