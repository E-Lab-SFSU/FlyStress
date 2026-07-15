# still_frame.py
# Captures X frames per second using OpenCV and saves lightweight images + CSV index.

import cv2
import time
import csv
from pathlib import Path
from datetime import datetime


class CaptureSettings:
    camera_index = 0
    fps = 1.0                 # adjustable: frames per second
    duration_sec = 60         # experiment length
    output_dir = "experiment_frames"

    save_grayscale = True     # saves less memory
    image_format = "jpg"      # "jpg" is smaller, "png" is lossless
    jpeg_quality = 85         # lower = smaller files, 70-90 is reasonable

    width = 1280              # adjust for your camera
    height = 720


def capture_frames(settings: CaptureSettings):
    output_path = Path(settings.output_dir)
    frames_path = output_path / "frames"
    frames_path.mkdir(parents=True, exist_ok=True)

    csv_path = output_path / "frame_index.csv"

    cap = cv2.VideoCapture(settings.camera_index)

    if not cap.isOpened():
        raise RuntimeError("Could not open camera.")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)

    interval = 1.0 / settings.fps
    start_time = time.time()
    next_capture_time = start_time
    frame_number = 0

    print("Starting capture...")
    print(f"FPS: {settings.fps}")
    print(f"Duration: {settings.duration_sec} seconds")
    print(f"Output folder: {output_path}")

    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "frame_number",
            "timestamp_unix",
            "elapsed_sec",
            "filename"
        ])

        while True:
            now = time.time()
            elapsed = now - start_time

            if elapsed >= settings.duration_sec:
                break

            ret, frame = cap.read()
            if not ret:
                print("Frame read failed.")
                continue

            if now >= next_capture_time:
                if settings.save_grayscale:
                    frame_to_save = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                else:
                    frame_to_save = frame

                filename = f"frame_{frame_number:06d}.{settings.image_format}"
                file_path = frames_path / filename

                if settings.image_format.lower() == "jpg":
                    cv2.imwrite(
                        str(file_path),
                        frame_to_save,
                        [cv2.IMWRITE_JPEG_QUALITY, settings.jpeg_quality]
                    )
                else:
                    cv2.imwrite(str(file_path), frame_to_save)

                writer.writerow([
                    frame_number,
                    now,
                    elapsed,
                    filename
                ])

                frame_number += 1
                next_capture_time += interval

            cv2.imshow("Live Camera Preview", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("Capture stopped by user.")
                break

    cap.release()
    cv2.destroyAllWindows()

    print(f"Capture complete. Saved {frame_number} frames.")
    print(f"CSV index saved to: {csv_path}")


if __name__ == "__main__":
    settings = CaptureSettings()

    # Example adjustments
    settings.fps = 1.0
    settings.duration_sec = 300
    settings.output_dir = datetime.now().strftime("experiment_%Y%m%d_%H%M%S")

    capture_frames(settings)