import serial
import time

# =========================
# USER SETTINGS
# =========================

SERIAL_PORT = "/dev/ttyUSB0"   # or /dev/ttyACM0
BAUDRATE = 115200

# Motion parameters
SHAKE_DISTANCE = 4       # mm (total peak-to-peak Y travel)
VELOCITY = 220          # mm/s
ACCELERATION = 12500        # mm/s^2
JERK = 20                 # mm/s
DURATION = 20           # seconds to shake

# Positioning
CENTER_Y = 25       # safe middle of Y travel

# Z safety
RAISE_Z_FIRST = True
Z_LIFT = 50                # mm
Z_FEEDRATE = 600           # mm/min

HOME_FIRST = True

# =========================
# FUNCTIONS
# =========================

def send(cmd, delay=0.05):
    print(">>", cmd)
    ser.write((cmd + "\n").encode())
    ser.flush()
    time.sleep(delay)

# =========================
# CONNECT
# =========================

print("Connecting to printer...")
ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=2)

# Marlin resets on USB connect
time.sleep(3)

send("M110 N0")     # reset line numbers
send("M155 S0")     # disable temperature auto-report

# =========================
# HOMING & SAFETY
# =========================

if HOME_FIRST:
    send("G28")     # home all axes (safest)

send("G90")         # absolute positioning

"""ADD YOUR COORDINATES HERE  """

if RAISE_Z_FIRST:
    send(f"G1 Z{Z_LIFT} F{Z_FEEDRATE}")
    send("M400")    # wait until Z move finishes
    time.sleep(3)

# =========================
# MOTION LIMITS
# =========================

send(f"M204 P{ACCELERATION} T{ACCELERATION}")  # acceleration
send(f"M205 X{JERK} Y{JERK}")                   # jerk

# Move bed to center position
send(f"G1 Y{CENTER_Y} F3000")
send("M400")

# =========================
# SHAKE LOOP
# =========================

feedrate = VELOCITY * 60        # mm/s → mm/min
half = SHAKE_DISTANCE / 2

print("🔥 BED SHAKING STARTED 🔥")
start_time = time.time()

try:
    while time.time() - start_time < DURATION:
        send(f"G1 Y{CENTER_Y + half} F{feedrate}")
        send(f"G1 Y{CENTER_Y - half} F{feedrate}")

except KeyboardInterrupt:
    print("\n🛑 Interrupted by user")

finally:
    print("Stopping motion...")
    send("M400")                                # wait for moves
    send(f"G1 Y{CENTER_Y} F3000")               # return to center
    ser.close()
    print("Done. Connection closed.")
