# FlyStress
Collaboration with Riggs Lab

*description of FlyStress project*

# FlyStress-v4*.py
GUI used for Fly Stress experiments.

**Features:** 
- Perform Shake Test (ST) by moving the Ender 3 printer bed along y / y+z axis rapidly.
- Records from USB camera, [Arducam](https://www.amazon.com/Arducam-Shutter-Android-Devices-Raspberry/dp/B0FXWWF55X), and used [Guvcview](https://guvcview.sourceforge.net/) to record.
- [MPU6050](https://docs.sunfounder.com/projects/umsk/en/latest/05_raspberry_pi/pi_lesson05_mpu6050.html) accelerometer sensor attached to printer bed, data is read & saved into csv files during ST.
- Create a line plot graph using csv files to display/compare the amount of force used during a ST.
- Create box plot and save min, max and average force used in ST.
- Shorten csv files by extracting specfic time frames.

# MPU reading
Separate programs specifically for reading data from MPU6050 sensor.

**mpu6050-read-acc.py**
Reads accelerometer + gyroscope data and displays live feed in a small window. Saves data in csv file.

**mpu6050-read-temp.py**
Reads temperature data and displays live feed in a small window. Saves data in csv file.

# ST + MPU + Recording
**shakeT-and-MPUacc.py**
Performs shake test and reads + displays data from MPU. Saves data in csv file.

**FS-SDR-S.py**
Performs ST and records when a camera is attached to the pi. Can change ST settings. Data saved in csv file + displayed in small window.
