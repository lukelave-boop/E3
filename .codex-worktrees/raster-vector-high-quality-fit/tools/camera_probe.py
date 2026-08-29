#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import cv2

from laser_aligner.camera.controls import list_controls
from laser_aligner.camera.service import list_video_devices


def main() -> int:
    parser = argparse.ArgumentParser(description="List Linux cameras and optionally capture one test frame")
    parser.add_argument("--device", help="Camera device path, such as /dev/video0 or /dev/v4l/by-id/...")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--output", type=Path, default=Path("camera-probe.jpg"))
    args = parser.parse_args()

    devices = list_video_devices()
    if not devices:
        print("No /dev/video* devices found.")
    else:
        print("Video devices:")
        for device in devices:
            suffix = f" -> {device['by_id']}" if device["by_id"] else ""
            print(f"  {device['path']}{suffix}")

    device = args.device or (devices[0]["by_id"] or devices[0]["path"] if devices else None)
    if not device:
        return 1

    if shutil.which("v4l2-ctl"):
        print("\nFormats:")
        subprocess.run(["v4l2-ctl", "-d", device, "--list-formats-ext"], check=False)
    controls = list_controls(device)
    print("\nControls:")
    for name, description in controls.items():
        print(f"  {name}: {description}")

    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not capture.isOpened():
        capture = cv2.VideoCapture(device)
    if not capture.isOpened():
        print(f"\nCould not open {device}")
        return 2
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    frame = None
    for _ in range(20):
        ok, candidate = capture.read()
        if ok:
            frame = candidate
    capture.release()
    if frame is None:
        print("\nCamera opened, but no frame was captured.")
        return 3
    cv2.imwrite(str(args.output), frame, [cv2.IMWRITE_JPEG_QUALITY, 96])
    print(f"\nSaved {frame.shape[1]} x {frame.shape[0]} frame to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
