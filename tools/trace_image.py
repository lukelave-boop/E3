#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# This repository-local tool supports direct execution without an installed
# package, so its project imports intentionally follow the sys.path bootstrap.
# ruff: noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from laser_aligner.config import WorkArea
from laser_aligner.vision.object_trace import TraceOptions, detect_objects


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run E3 multi-object tracing on a saved rectified bed image"
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, default=Path("trace-preview.png"))
    parser.add_argument("--json", type=Path, default=Path("trace-result.json"))
    parser.add_argument("--width-mm", type=float, default=190.0)
    parser.add_argument("--height-mm", type=float, default=190.0)
    parser.add_argument("--ppm", type=float, default=4.0)
    parser.add_argument("--mode", choices=("auto", "color", "contrast"), default="auto")
    parser.add_argument("--hue", type=float, default=None)
    parser.add_argument("--hue-tolerance", type=float, default=14.0)
    parser.add_argument("--min-saturation", type=int, default=45)
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--no-inference", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not read {args.image}")
    expected_width = max(1, int(round(args.width_mm * args.ppm)))
    expected_height = max(1, int(round(args.height_mm * args.ppm)))
    if image.shape[1] != expected_width or image.shape[0] != expected_height:
        image = cv2.resize(image, (expected_width, expected_height), interpolation=cv2.INTER_AREA)

    work_area = WorkArea(0.0, args.width_mm, 0.0, args.height_mm)
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode=args.mode,
            target_hue=args.hue,
            hue_tolerance=args.hue_tolerance,
            min_saturation=args.min_saturation,
            regular_grid=not args.no_grid,
            infer_missing=not args.no_inference,
        ),
        work_area,
        args.ppm,
    )

    preview = image.copy()
    for detection in result.detections:
        points = np.asarray(
            [
                [
                    (x - work_area.x_min) * args.ppm,
                    (work_area.y_max - y) * args.ppm,
                ]
                for x, y in detection.contour_mm
            ],
            dtype=np.int32,
        )
        color = (55, 230, 80) if detection.source == "direct" else (20, 190, 245)
        cv2.polylines(preview, [points], True, color, 2, cv2.LINE_AA)
        x = int(round((detection.center_mm[0] - work_area.x_min) * args.ppm))
        y = int(round((work_area.y_max - detection.center_mm[1]) * args.ppm))
        cv2.putText(
            preview,
            str(detection.index),
            (x + 4, y - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), preview)
    args.json.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(result.message)
    print(args.output)
    print(args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
