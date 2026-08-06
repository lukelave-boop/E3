#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
fid_path = ROOT / "laser_aligner/vision/fiducials.py"
app_path = ROOT / "laser_aligner/app.py"

fid = fid_path.read_text(encoding="utf-8")
app = app_path.read_text(encoding="utf-8")

helper_code = '\ndef detect_crosshairs_near(\n    image: np.ndarray,\n    expected_points: list[dict[str, Any]],\n    search_radius_px: int = 55,\n) -> dict[str, Any]:\n    """Refine approximate crosshair locations without needing a plate boundary."""\n    if image is None or image.size == 0:\n        return {"detected": False, "reason": "Empty image", "points": []}\n    if len(expected_points) != 25:\n        return {\n            "detected": False,\n            "reason": f"Need 25 expected locations; received {len(expected_points)}",\n            "points": [],\n        }\n\n    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()\n    response = _cross_response(gray)\n    height, width = gray.shape[:2]\n    refined = []\n    scores = []\n\n    for target in expected_points:\n        expected_x = float(target["image_x"])\n        expected_y = float(target["image_y"])\n        x0 = max(0, int(round(expected_x)) - search_radius_px)\n        x1 = min(width, int(round(expected_x)) + search_radius_px + 1)\n        y0 = max(0, int(round(expected_y)) - search_radius_px)\n        y1 = min(height, int(round(expected_y)) + search_radius_px + 1)\n        roi = response[y0:y1, x0:x1]\n        if roi.size == 0:\n            return {\n                "detected": False,\n                "reason": f"Search area for fiducial {target[\'id\']} is outside the image",\n                "points": refined,\n            }\n\n        _, peak, _, location = cv2.minMaxLoc(roi)\n        px = float(x0 + location[0])\n        py = float(y0 + location[1])\n\n        radius = 7\n        lx0 = max(0, int(px) - radius)\n        lx1 = min(width, int(px) + radius + 1)\n        ly0 = max(0, int(py) - radius)\n        ly1 = min(height, int(py) + radius + 1)\n        local = response[ly0:ly1, lx0:lx1]\n        local = np.maximum(local - float(np.percentile(local, 35)), 0)\n        total = float(local.sum())\n        if total > 0:\n            yy, xx = np.mgrid[ly0:ly1, lx0:lx1]\n            px = float((xx * local).sum() / total)\n            py = float((yy * local).sum() / total)\n\n        scores.append(float(peak))\n        refined.append(\n            {\n                "id": int(target["id"]),\n                "image_x": px,\n                "image_y": py,\n                "machine_x": float(target["machine_x"]),\n                "machine_y": float(target["machine_y"]),\n                "label": f"Auto fiducial {int(target[\'id\'])}",\n                "score": float(peak),\n                "seed_x": expected_x,\n                "seed_y": expected_y,\n                "shift_px": float(np.hypot(px - expected_x, py - expected_y)),\n            }\n        )\n\n    score_array = np.asarray(scores, dtype=np.float64)\n    median = float(np.median(score_array))\n    minimum = float(np.min(score_array))\n    max_shift = max(point["shift_px"] for point in refined)\n\n    confidence = "high"\n    if median <= 0 or minimum < median * 0.22 or max_shift > search_radius_px * 0.9:\n        confidence = "low"\n    elif minimum < median * 0.42 or max_shift > search_radius_px * 0.65:\n        confidence = "medium"\n\n    refined.sort(key=lambda point: point["id"])\n    return {\n        "detected": True,\n        "points": refined,\n        "confidence": confidence,\n        "orientation": "Seeded from current mapping; refined to nearby burned crosses",\n        "maximum_seed_shift_px": float(max_shift),\n    }\n'
if "def detect_crosshairs_near(" not in fid:
    fid += "\n" + helper_code
    fid_path.write_text(fid, encoding="utf-8")

old_import = "from .vision.fiducials import detect_aruco_markers, detect_crosshair_grid"
new_import = "from .vision.fiducials import detect_aruco_markers, detect_crosshair_grid, detect_crosshairs_near"
if old_import in app:
    app = app.replace(old_import, new_import, 1)
elif "detect_crosshairs_near" not in app:
    raise SystemExit("Expected fiducial import was not found.")

old_method = """    def detect_bed_cross_grid(self) -> dict[str, Any]:
        return detect_crosshair_grid(self.bed_reference())
"""
new_method = '    def detect_bed_cross_grid(self) -> dict[str, Any]:\n        image = self.bed_reference()\n        coordinates = (20.0, 65.0, 110.0, 155.0, 200.0)\n\n        if self.bed.calibration is None:\n            return {\n                "detected": False,\n                "reason": (\n                    "A rough existing bed mapping is required for boundary-independent "\n                    "detection. Keep the current manual mapping, capture the burned grid, "\n                    "then run detection."\n                ),\n                "points": [],\n            }\n\n        expected_points: list[dict[str, Any]] = []\n        identifier = 1\n        for machine_y in coordinates:\n            for machine_x in coordinates:\n                image_x, image_y = self.bed.mm_to_image(machine_x, machine_y)\n                expected_points.append(\n                    {\n                        "id": identifier,\n                        "image_x": image_x,\n                        "image_y": image_y,\n                        "machine_x": machine_x,\n                        "machine_y": machine_y,\n                    }\n                )\n                identifier += 1\n\n        return detect_crosshairs_near(image, expected_points, search_radius_px=65)\n'

if old_method not in app:
    raise SystemExit("Expected detect_bed_cross_grid method was not found.")

app = app.replace(old_method, new_method, 1)
app_path.write_text(app, encoding="utf-8")

print("Updated automatic detection to boundary-independent seeded cross search.")
print("Keep the current rough mapping until detected points are accepted.")
