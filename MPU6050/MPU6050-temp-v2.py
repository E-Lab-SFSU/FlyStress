#!/usr/bin/env python3
"""
MPU6050 Temperature Logger (PyQt6)
Purpose: Read only the MPU6050 internal temperature sensor,
         display it in a small window, and save readings to a CSV file.

Run on Raspberry Pi:
    python3 MPU6050-temp-v2.py

Requires:
    pip install PyQt6 smbus2

Stop by closing the display window.
"""

from smbus2 import SMBus
import time
import datetime
import csv
import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout, QMessageBox

# MPU6050 I2C settings
BUS_ID = 1
ADDRESS = 0x68

# MPU6050 registers
PWR_MGMT_1 = 0x6B
TEMP_OUT_H = 0x41

# Logging settings
SAMPLE_DELAY_MS = 100  # 100 ms = 10 readings per second

# read a single byte from the MPU6050
def to_int16(value: int) -> int:
    """Convert unsigned 16-bit value to signed 16-bit value."""
    value &= 0xFFFF
    return value - 65536 if value & 0x8000 else value

# read two bytes from the MPU6050 and combine them into one 16-bit value
def read_word(bus: SMBus, address: int, reg: int) -> int:
    """Read two bytes from the MPU6050 and combine them into one 16-bit value."""
    high = bus.read_byte_data(address, reg)
    low = bus.read_byte_data(address, reg + 1)
    return (high << 8) | low

# use raw 16-bit value to calculate temperature in Celsius and Fahrenheit
def read_temperature(bus: SMBus, address: int):
    """Read raw MPU6050 temperature and convert to Celsius and Fahrenheit."""
    raw_temp = to_int16(read_word(bus, address, TEMP_OUT_H))

    # MPU6050 temperature conversion from datasheet/register map:
    # Temperature in C = raw / 340 + 36.53
    temp_c = (raw_temp / 340.0) + 36.53
    temp_f = (temp_c * 9.0 / 5.0) + 32.0

    return raw_temp, temp_c, temp_f

# Window displaying live temperature readings and logging to CSV
class TemperatureLoggerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Temperature Log")
        self.resize(520, 300)

        self.bus = None
        self.csv_file = None
        self.writer = None
        self.csv_filename = None
        self.timer = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(20, 20, 20, 20)

        self.title_label = QLabel("MPU6050 Temperature")
        self.title_label.setFont(QFont("Verdana", 22, QFont.Weight.Bold))

        self.c_label = QLabel("Celsius: --")
        self.c_label.setFont(QFont("Verdana", 20))

        self.f_label = QLabel("Fahrenheit: --")
        self.f_label.setFont(QFont("Verdana", 18))

        self.raw_label = QLabel("Raw: --")
        self.raw_label.setFont(QFont("Verdana", 14))

        self.status_label = QLabel("Starting...")
        self.status_label.setFont(QFont("Verdana", 11))

        self.file_label = QLabel("CSV: --")
        self.file_label.setFont(QFont("Verdana", 10))
        self.file_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.c_label)
        layout.addWidget(self.f_label)
        layout.addWidget(self.raw_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.file_label)

        try:
            self.setup_mpu()
            self.setup_csv()
            self.status_label.setText("Logging temperature data...")

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.update_temperature)
            self.timer.start(SAMPLE_DELAY_MS)
        except Exception as e:
            QMessageBox.critical(self, "MPU6050 Error", str(e))
            self.status_label.setText(f"Error: {e}")

    def setup_mpu(self):
        self.bus = SMBus(BUS_ID)
        time.sleep(1)

        # Wake the MPU6050. It powers up in sleep mode.
        self.bus.write_byte_data(ADDRESS, PWR_MGMT_1, 0x00)
        time.sleep(0.1)

    def setup_csv(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_filename = f"mpu_temperature_{timestamp}.csv"

        self.csv_file = open(self.csv_filename, "w", newline="")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(["timestamp", "temp_raw", "temp_c", "temp_f"])
        self.csv_file.flush()

        self.file_label.setText(f"CSV: {os.path.abspath(self.csv_filename)}")

    def update_temperature(self):
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            raw_temp, temp_c, temp_f = read_temperature(self.bus, ADDRESS)

            self.writer.writerow([
                timestamp,
                raw_temp,
                f"{temp_c:.4f}",
                f"{temp_f:.4f}",
            ])
            self.csv_file.flush()

            self.raw_label.setText(f"Raw: {raw_temp}")
            self.c_label.setText(f"Celsius: {temp_c:.2f} °C")
            self.f_label.setText(f"Fahrenheit: {temp_f:.2f} °F")
            self.status_label.setText("Logging temperature data...")

        except Exception as e:
            self.status_label.setText(f"Error: {e}")

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
    window = TemperatureLoggerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()