from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def detect_aruco_markers(
    image: np.ndarray,
    dictionary_name: str = "DICT_4X4_50",
) -> list[dict[str, Any]]:
    if not hasattr(cv2, "aruco"):
        return []
    dictionary_id = getattr(cv2.aruco, dictionary_name, None)
    if dictionary_id is None:
        raise ValueError(f"Unknown ArUco dictionary: {dictionary_name}")
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    if hasattr(cv2.aruco, "ArucoDetector"):
        parameters = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        corners, ids, _ = detector.detectMarkers(image)
    else:  # OpenCV < 4.7 compatibility
        parameters = cv2.aruco.DetectorParameters_create()
        corners, ids, _ = cv2.aruco.detectMarkers(image, dictionary, parameters=parameters)
    if ids is None:
        return []

    markers: list[dict[str, Any]] = []
    for marker_id, marker_corners in zip(ids.reshape(-1), corners, strict=True):
        points = np.asarray(marker_corners, dtype=float).reshape(4, 2)
        center = points.mean(axis=0)
        markers.append(
            {
                "id": int(marker_id),
                "center": [float(center[0]), float(center[1])],
                "corners": points.tolist(),
            }
        )
    return markers
