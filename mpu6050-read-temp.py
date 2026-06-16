#!/usr/bin/env python3
"""
MPU6050 Temperature Logger
Purpose: Read only the MPU6050 internal temperature sensor,
         display it in a small window, and save readings to a CSV file.

Run on Raspberry Pi:
    python3 mpu6050_temperature_logger.py

Stop by closing the display window.
"""

from smbus2 import SMBus
import time
import datetime
import csv
import os
import tkinter as tk
from tkinter import messagebox

# MPU6050 I2C settings
BUS_ID = 1
ADDRESS = 0x68

# MPU6050 registers
PWR_MGMT_1 = 0x6B
TEMP_OUT_H = 0x41

# Logging settings
SAMPLE_DELAY_MS = 100  # 100 ms = 10 readings per second


def to_int16(value: int) -> int:
    """Convert unsigned 16-bit value to signed 16-bit value."""
    value &= 0xFFFF
    return value - 65536 if value & 0x8000 else value


def read_word(bus: SMBus, address: int, reg: int) -> int:
    """Read two bytes from the MPU6050 and combine them into one 16-bit value."""
    high = bus.read_byte_data(address, reg)
    low = bus.read_byte_data(address, reg + 1)
    return (high << 8) | low


def read_temperature(bus: SMBus, address: int):
    """Read raw MPU6050 temperature and convert to Celsius and Fahrenheit."""
    raw_temp = to_int16(read_word(bus, address, TEMP_OUT_H))

    # MPU6050 temperature conversion from datasheet/register map:
    # Temperature in C = raw / 340 + 36.53
    temp_c = (raw_temp / 340.0) + 36.53
    temp_f = (temp_c * 9.0 / 5.0) + 32.0

    return raw_temp, temp_c, temp_f


class TemperatureLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MPU6050 Temperature Logger")
        self.root.geometry("520x300")

        self.bus = None
        self.csv_file = None
        self.writer = None
        self.csv_filename = None

        self.status_var = tk.StringVar(value="Starting...")
        self.raw_var = tk.StringVar(value="Raw: --")
        self.c_var = tk.StringVar(value="Celsius: --")
        self.f_var = tk.StringVar(value="Fahrenheit: --")
        self.file_var = tk.StringVar(value="CSV: --")

        title = tk.Label(root, text="MPU6050 Temperature", font=("Verdana", 22, "bold"))
        title.pack(pady=(20, 10))

        tk.Label(root, textvariable=self.c_var, font=("Verdana", 20)).pack(pady=5)
        tk.Label(root, textvariable=self.f_var, font=("Verdana", 18)).pack(pady=5)
        tk.Label(root, textvariable=self.raw_var, font=("Verdana", 14)).pack(pady=5)
        tk.Label(root, textvariable=self.status_var, font=("Verdana", 11)).pack(pady=(15, 2))
        tk.Label(root, textvariable=self.file_var, font=("Verdana", 10)).pack(pady=2)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        try:
            self.setup_mpu()
            self.setup_csv()
            self.status_var.set("Logging temperature data...")
            self.update_temperature()
        except Exception as e:
            messagebox.showerror("MPU6050 Error", str(e))
            self.status_var.set(f"Error: {e}")

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

        self.file_var.set(f"CSV: {os.path.abspath(self.csv_filename)}")

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

            self.raw_var.set(f"Raw: {raw_temp}")
            self.c_var.set(f"Celsius: {temp_c:.2f} °C")
            self.f_var.set(f"Fahrenheit: {temp_f:.2f} °F")
            self.status_var.set("Logging temperature data...")

        except Exception as e:
            self.status_var.set(f"Error: {e}")

        self.root.after(SAMPLE_DELAY_MS, self.update_temperature)

    def on_close(self):
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

        self.root.destroy()


def main():
    root = tk.Tk()
    TemperatureLoggerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
