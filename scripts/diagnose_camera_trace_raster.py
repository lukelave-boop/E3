from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laser_aligner.project.raster_vectorize import (  # noqa: E402
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    RasterVectorizationTiming,
    prepare_pixel_vectorization_mask,
    prepare_pixel_vectorization_source,
    select_pixel_vectorization_auto_threshold,
)
from laser_aligner.vision.camera_raster_normalization import (  # noqa: E402
    CameraRasterNormalizationTiming,
    normalize_camera_trace_frame,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the exact normalized Camera Trace raster and production mask "
            "without running native contour fitting."
        )
    )
    parser.add_argument(
        "frame",
        type=Path,
        help="Local corrected/rectified BGR frame (the script does not rectify raw captures)",
    )
    parser.add_argument(
        "--pixels-per-mm",
        type=float,
        required=True,
        help="Physical scale of the corrected frame",
    )
    parser.add_argument(
        "--polarity",
        choices=("dark", "light"),
        default="dark",
        help="Feature polarity to prepare (default: dark)",
    )
    parser.add_argument(
        "--threshold",
        choices=("auto", "manual"),
        default="auto",
        help="Shared raster threshold mode (default: bounded Auto selection)",
    )
    parser.add_argument(
        "--threshold-value",
        type=int,
        default=128,
        help="Normalized manual threshold byte (default: 128)",
    )
    parser.add_argument(
        "--minimum-area-mm2",
        type=float,
        default=30.0,
        help="Shared component-cleanup minimum area (default: 30)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "trace_diagnostics",
        help="Gitignored output directory",
    )
    return parser


def _write_image(path: Path, image: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Could not write diagnostic image: {path}")


def main() -> int:
    args = _parser().parse_args()
    corrected = cv2.imread(str(args.frame), cv2.IMREAD_COLOR)
    if corrected is None:
        raise SystemExit(f"Could not read a BGR frame from {args.frame}")

    normalization_timing = CameraRasterNormalizationTiming()
    normalization = normalize_camera_trace_frame(
        corrected,
        args.pixels_per_mm,
        timing=normalization_timing,
    )
    artwork = normalization.raster_for(args.polarity)
    normalized = artwork if args.polarity == "dark" else cv2.bitwise_not(artwork)
    source = prepare_pixel_vectorization_source(
        cv2.cvtColor(normalized, cv2.COLOR_GRAY2RGBA)
    )
    width_mm = source.width_px / args.pixels_per_mm
    height_mm = source.height_px / args.pixels_per_mm
    options = RasterVectorizationOptions(
        detection_mode=(
            RasterDetectionMode.AUTO_THRESHOLD
            if args.threshold == "auto"
            else RasterDetectionMode.MANUAL_THRESHOLD
        ),
        threshold=args.threshold_value,
        invert=args.polarity == "light",
        minimum_feature_area_mm2=args.minimum_area_mm2,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.1,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )
    raster_timing = RasterVectorizationTiming()
    auto_threshold_selection = None
    if args.threshold == "auto":
        auto_threshold_selection = select_pixel_vectorization_auto_threshold(
            source,
            options,
            timing=raster_timing,
        )
        options = replace(
            options,
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
            threshold=auto_threshold_selection.threshold,
        )
    prepared = prepare_pixel_vectorization_mask(
        source,
        options,
        displayed_width_mm=width_mm,
        displayed_height_mm=height_mm,
        timing=raster_timing,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    background = np.clip(
        np.rint(normalization.background),
        0.0,
        255.0,
    ).astype(np.uint8)
    _write_image(output_dir / "corrected.png", normalization.corrected_bgr)
    _write_image(output_dir / "background.png", background)
    _write_image(output_dir / "normalized.png", source.composited_grayscale)
    _write_image(output_dir / "mask.png", prepared.contour_mask)

    diagnostics = {
        "frame": str(args.frame.resolve()),
        "output_dir": str(output_dir),
        "polarity": args.polarity,
        "threshold_mode": args.threshold,
        "threshold_used": prepared.threshold_used,
        "auto_threshold_selection": (
            None
            if auto_threshold_selection is None
            else auto_threshold_selection.to_dict()
        ),
        "connected_component_count": prepared.connected_component_count,
        "normalized_shape_px": [source.width_px, source.height_px],
        "contour_mask_shape_px": [
            int(prepared.contour_mask.shape[1]),
            int(prepared.contour_mask.shape[0]),
        ],
        "normalization": asdict(normalization.diagnostics),
        "normalization_timing": normalization_timing.snapshot(),
        "mask_timing": raster_timing.snapshot(),
    }
    (output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
