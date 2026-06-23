"""
Reading & Displaying MPU6050 Acceleration and Gyroscope angles
Author: Cherese Jordan
Purpose: read from MPU6050 accelerometer sensor and gyroscope scale,
         display output, save data in .csv file.
         
- MPU reading sourced from Jaynath Tadikonda ; https://ch.mathworks.com/matlabcentral/answers/506391-raspberry-pi-with-mpu6050-sensor-data-visualisation-accelerometer-gyroscope
mpu data sheet and reg map download
https://invensense.tdk.com/download-resource/ps-mpu-6000a-00-mpu-6000-and-mpu-6050-datasheet

"""

#!/usr/bin/python
from smbus2 import SMBus
import math

import time
import datetime

import os
import csv
import tkinter as tk

# Register

# the on and off button // these addresses do not change
power_mgmt_1 = 0x6b
power_mgmt_2 = 0x6c

#read byte data from MPU
def read_byte(reg):
    return bus.read_byte_data(address, reg)

#convert byte to word
def read_word(reg):
    h = bus.read_byte_data(address, reg)
    l = bus.read_byte_data(address, reg+1)
    value = (h << 8) + l
    return value

#negate numbers using two's complement
def read_word_2c(reg):
    val = read_word(reg)
    if (val >= 0x8000):
        return -((65535 - val) + 1)
    else:
        return val

#calculate distance
def dist(a,b):
    return math.sqrt((a*a)+(b*b))

#read and store rotations on x,y,z axis
def get_y_rotation(x,y,z):
    radians = math.atan2(x, dist(y,z))
    return -math.degrees(radians)

def get_x_rotation(x,y,z):
    radians = math.atan2(y, dist(x,z))
    return math.degrees(radians)

bus = SMBus(1)
time.sleep(1)
address = 0x68       # via i2cdetect, wakes MPU

# Start the bus to send request for data.
bus.write_byte_data(0x68, 0x6B, 0x00)
bus.write_byte_data(address, power_mgmt_1, 0)

"""
print("Logging MPU6050 data to sensor_output.txt... Press Ctrl+C to stop.")

try:
    # Open the file in append mode ('a')
    with open("sensor_output.txt", "a") as f:
        while True:
            # Read accelerometer data (example registers)
            accel_x = bus.read_word_data(address, 0x3B)
            accel_y = bus.read_word_data(address, 0x3D)
            accel_z = bus.read_word_data(address, 0x3F)

            # Get current timestamp
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

            # Format the data string
            data_string = f"{timestamp}, AccelX: {accel_x}, AccelY: {accel_y}, AccelZ: {accel_z}\n"

            # Write to the file
            f.write(data_string)
            f.flush() # Ensure data is written immediately

            time.sleep(0.1) # Log every 0.1 seconds

except KeyboardInterrupt:
    print("Logging stopped by user.")
except Exception as e:
    print(f"An error occurred: {e}")
    

while True:
        print("Gyroscope")
        print("--------")

        gyroscope_x = read_word_2c(0x43)
        gyroscope_y = read_word_2c(0x45)
        gyroscope_z = read_word_2c(0x47)

        print("gyroscope_x: ", ("%5d" % gyroscope_x), " scaled: ", (gyroscope_x / 131))
        print("gyroscope_y: ", ("%5d" % gyroscope_y), " scaled: ", (gyroscope_y / 131))
        print("gyroscope_z: ", ("%5d" % gyroscope_z), " scaled: ", (gyroscope_z / 131))

        print("Accelerometer")
        print("---------------------")

        acceleration_x = read_word_2c(0x3b)
        acceleration_y = read_word_2c(0x3d)
        acceleration_z = read_word_2c(0x3f)

        acceleration_x_scaled = acceleration_x / 16384.0
        acceleration_y_scaled = acceleration_y / 16384.0
        acceleration_z_scaled = acceleration_z / 16384.0

        print("acceleration_x: ", ("%6d" % acceleration_x), " scaled: ", acceleration_x_scaled)
        print("acceleration_y: ", ("%6d" % acceleration_y), " scaled: ", acceleration_y_scaled)
        print("acceleration_z: ", ("%6d" % acceleration_z), " scaled: ", acceleration_z_scaled)

        print("X Rotation: " , get_x_rotation(acceleration_x_scaled, acceleration_y_scaled, acceleration_z_scaled))
        print("Y Rotation: " , get_y_rotation(acceleration_x_scaled, acceleration_y_scaled, acceleration_z_scaled))
        print("\n\n")
        os.system('cls' if os.name == 'nt' else 'clear')
"""

csv_filename = datetime.now().strftime("mpu_data_%Y%m%d_%H%M%S.csv")

csv_file = open(csv_filename, "w", newline="")
writer = csv.writer(csv_file)

writer.writerow(["timestamp", "accel_x_raw", "accel_y_raw", "accel_z_raw", 
"accel_x_g", "accel_y_g", "accel_z_g", "gyro_x_raw", "gyro_y_raw", "gyro_z_raw", 
"gyro_x_deg_s", "gyro_y_deg_s", "gyro_z_deg_s","x_rotation", "y_rotation" 
])

root = tk.Tk()
root.title("MPU Live Data Stream")
root.geometry("600x400")

display = tk.Text(root, font=("Courier", 12), width=70, height=20)
display.pack(padx=10, pady=10)

def update_data():
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

        gyro_x = read_word_2c(0x43)
        gyro_y = read_word_2c(0x45)
        gyro_z = read_word_2c(0x47)

        gyro_x_scaled = gyro_x / 131.0
        gyro_y_scaled = gyro_y / 131.0
        gyro_z_scaled = gyro_z / 131.0

        accel_x = read_word_2c(0x3B)
        accel_y = read_word_2c(0x3D)
        accel_z = read_word_2c(0x3F)

        accel_x_scaled = accel_x / 16384.0
        accel_y_scaled = accel_y / 16384.0
        accel_z_scaled = accel_z / 16384.0

        x_rot = get_x_rotation(accel_x_scaled, accel_y_scaled, accel_z_scaled)
        y_rot = get_y_rotation(accel_x_scaled, accel_y_scaled, accel_z_scaled)

        writer.writerow([
        timestamp,
        accel_x, accel_y, accel_z,
        accel_x_scaled, accel_y_scaled, accel_z_scaled,
        gyro_x, gyro_y, gyro_z,
        gyro_x_scaled, gyro_y_scaled, gyro_z_scaled,
        x_rot, y_rot
        ])
        csv_file.flush()

        display.delete("1.0", tk.END)
        display.insert(tk.END, f"Saving to: {csv_filename}\n\n")
        display.insert(tk.END, "Accelerometer\n")
        display.insert(tk.END, f"X raw: {accel_x:6d} X g: {accel_x_scaled:.4f}\n")
        display.insert(tk.END, f"Y raw: {accel_y:6d} Y g: {accel_y_scaled:.4f}\n")
        display.insert(tk.END, f"Z raw: {accel_z:6d} Z g: {accel_z_scaled:.4f}\n\n")

        display.insert(tk.END, "Gyroscope\n")
        display.insert(tk.END, f"X raw: {gyro_x:6d} X deg/s: {gyro_x_scaled:.4f}\n")
        display.insert(tk.END, f"Y raw: {gyro_y:6d} Y deg/s: {gyro_y_scaled:.4f}\n")
        display.insert(tk.END, f"Z raw: {gyro_z:6d} Z deg/s: {gyro_z_scaled:.4f}\n\n")

        display.insert(tk.END, f"X Rotation: {x_rot:.2f}\n")
        display.insert(tk.END, f"Y Rotation: {y_rot:.2f}\n")

    except Exception as e:
        display.delete("1.0", tk.END)
        display.insert(tk.END, f"Error: {e}")

    root.after(100, update_data)

def on_close():
    csv_file.close()
    bus.close()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
update_data()
root.mainloop()
