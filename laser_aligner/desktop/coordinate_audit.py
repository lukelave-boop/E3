from __future__ import annotations

import json
from typing import Any

from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


class CoordinateAuditPanel(QtWidgets.QWidget):
    """Read-only presentation for the active camera and machine coordinate frames."""

    captureRequested = QtCore.Signal()
    refreshRequested = QtCore.Signal()
    copyRequested = QtCore.Signal()

    def __init__(
        self,
        preview: QtWidgets.QWidget,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)

        left_widget = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_widget)
        left.addWidget(preview, 1)
        click_note = QtWidgets.QLabel(
            "Click the lens-corrected audit image to trace that point through the "
            "display, camera-map, machine, honeycomb, and beam-placement frames."
        )
        click_note.setWordWrap(True)
        click_note.setObjectName("mutedLabel")
        left.addWidget(click_note)
        buttons = QtWidgets.QHBoxLayout()
        self.capture_button = QtWidgets.QPushButton(
            "Home / park and capture audit view"
        )
        capture_tooltip = (
            "Use the existing Home / park capture path, then draw all current "
            "coordinate boundaries. This does not fire the laser or change calibration."
        )
        self.capture_button.setProperty("availableToolTip", capture_tooltip)
        self.capture_button.setToolTip(capture_tooltip)
        self.refresh_button = QtWidgets.QPushButton("Refresh numbers")
        self.copy_button = QtWidgets.QPushButton("Copy audit report")
        self.capture_button.clicked.connect(self.captureRequested.emit)
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        self.copy_button.clicked.connect(self.copyRequested.emit)
        buttons.addWidget(self.capture_button, 1)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.copy_button)
        left.addLayout(buttons)
        layout.addWidget(left_widget, 3)

        right_widget = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_widget)
        intro = QtWidgets.QLabel(
            "Coordinate calculations, refresh, copy, and point inspection are read-only. "
            "The capture button uses the existing laser-off Home / park motion; it does "
            "not change bounds, G-code, calibration, honeycomb placement, or laser power."
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedLabel")
        right.addWidget(intro)
        self.overall_status = QtWidgets.QLabel()
        self.overall_status.setObjectName("coordinateAuditOverallStatus")
        self.overall_status.setWordWrap(True)
        right.addWidget(self.overall_status)
        self.blockers = QtWidgets.QLabel()
        self.blockers.setObjectName("coordinateAuditBlockers")
        self.blockers.setWordWrap(True)
        self.blockers.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        right.addWidget(self.blockers)
        self.next_action = QtWidgets.QLabel()
        self.next_action.setObjectName("coordinateAuditNextAction")
        self.next_action.setWordWrap(True)
        self.next_action.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        right.addWidget(self.next_action)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("coordinateAuditTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(("Frame / field", "Current value"))
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(300)
        self.tree.header().setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        self.tree.header().setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.Stretch,
        )
        right.addWidget(self.tree, 1)

        point_group = QtWidgets.QGroupBox("Clicked-point transform")
        point_layout = QtWidgets.QVBoxLayout(point_group)
        self.point_details = QtWidgets.QPlainTextEdit()
        self.point_details.setObjectName("coordinateAuditPointDetails")
        self.point_details.setReadOnly(True)
        self.point_details.setMaximumHeight(185)
        self.point_details.setPlainText(
            "No point selected. Capture an audit view, then click a physical ruler "
            "mark or boundary."
        )
        point_layout.addWidget(self.point_details)
        right.addWidget(point_group)
        legend = QtWidgets.QLabel(
            "Orange = configured machine/work rectangle · Green = guarded laser-output "
            "authority · Magenta = measured honeycomb · X+/Y+ arrows show frame directions."
        )
        legend.setWordWrap(True)
        legend.setObjectName("mutedLabel")
        right.addWidget(legend)
        layout.addWidget(right_widget, 2)

    @staticmethod
    def format_xy(value: Any, *, axes: tuple[str, ...] = ("X", "Y")) -> str:
        if not isinstance(value, (list, tuple)) or len(value) < len(axes):
            return "Unavailable"
        result: list[str] = []
        for index, axis in enumerate(axes):
            coordinate = value[index]
            if coordinate is None:
                result.append(f"{axis}—")
            else:
                result.append(f"{axis}{float(coordinate):.3f}")
        return "  ".join(result)

    @staticmethod
    def format_polygon(value: Any) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            return "Unavailable"
        points: list[str] = []
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return "Unavailable"
            points.append(f"({float(point[0]):.3f}, {float(point[1]):.3f})")
        return " · ".join(points)

    def _add_group(
        self,
        title: str,
        rows: tuple[tuple[str, str], ...],
    ) -> None:
        group = QtWidgets.QTreeWidgetItem((title, ""))
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        self.tree.addTopLevelItem(group)
        for label, value in rows:
            group.addChild(QtWidgets.QTreeWidgetItem((label, value)))
        group.setExpanded(True)

    def set_status(self, audit: dict[str, Any]) -> None:
        state = str(audit.get("overall_state") or "BLOCKED")
        honeycomb = audit.get("honeycomb") or {}
        expected_span = float(honeycomb.get("expected_span_mm") or 0.0)
        self.overall_status.setText(
            f"{state} · configured physical honeycomb span "
            f"{expected_span:.3f} × {expected_span:.3f} mm"
        )
        blockers = [str(item) for item in audit.get("blockers") or ()]
        self.blockers.setText(
            "No coordinate dependency is blocking support-bound work."
            if not blockers
            else "BLOCKED because:\n• " + "\n• ".join(blockers)
        )
        next_action = str(audit.get("required_next_action") or "").strip()
        self.next_action.setText(
            "Next action: " + next_action if next_action else "Next action unavailable"
        )

        machine = audit.get("machine") or {}
        coordinate_state = machine.get("coordinate_state_reference") or {}
        active_workspace = str(coordinate_state.get("active_workspace") or "Unknown")
        active_offset = coordinate_state.get("active_offset_mm")
        g92_offset = coordinate_state.get("g92_offset_mm")
        work = machine.get("work_area_mm") or ()
        work_text = (
            f"X{float(work[0]):.3f}…{float(work[1]):.3f}  "
            f"Y{float(work[2]):.3f}…{float(work[3]):.3f} mm"
            if isinstance(work, (list, tuple)) and len(work) == 4
            else "Unavailable"
        )
        trusted = machine.get("trusted_position_mm")
        trusted_text = (
            f"X{float(trusted['x']):.3f}  Y{float(trusted['y']):.3f} mm"
            if isinstance(trusted, dict)
            and type(trusted.get("x")) in {int, float}
            and type(trusted.get("y")) in {int, float}
            else "Unavailable until Home / park establishes a trusted reference"
        )

        laser = audit.get("laser") or {}
        camera = audit.get("camera") or {}
        lens = audit.get("lens") or {}
        bed = audit.get("bed_map") or {}
        support = honeycomb
        recorded_size = support.get("recorded_size_mm")
        measured_spans = support.get("measured_spans_mm")

        self.tree.clear()
        self._add_group(
            "Controller / machine",
            (
                ("Connection", "Connected" if machine.get("connected") else "Offline"),
                (
                    "Backend / protocol",
                    f"{machine.get('backend') or 'unknown'} / "
                    f"{machine.get('protocol') or 'unknown'}",
                ),
                (
                    "Home / park reference",
                    "READY"
                    if machine.get("coordinate_reference_ready")
                    else "NOT ESTABLISHED",
                ),
                ("Trusted position", trusted_text),
                ("Active GRBL workspace", active_workspace),
                (
                    "Active workspace offset",
                    self.format_xy(active_offset, axes=("X", "Y", "Z")),
                ),
                ("G92 offset", self.format_xy(g92_offset, axes=("X", "Y", "Z"))),
                ("Configured motion/work rectangle", work_text),
                (
                    "Configured photography pose",
                    self.format_xy(
                        machine.get("photo_position_mm"),
                        axes=("X", "Y", "Z"),
                    )
                    + " mm",
                ),
                (
                    "Realtime MPos / WPos",
                    "Not sampled by this read-only revision; use the controller status "
                    "audit during the physical follow-up",
                ),
            ),
        )
        self._add_group(
            "Laser-output authority",
            (
                ("Authority", str(laser.get("output_authority_kind") or "Unknown")),
                (
                    "Boundary margin",
                    f"{float(laser.get('boundary_margin_mm') or 0.0):.3f} mm",
                ),
                (
                    "Physical beam offset",
                    self.format_xy(laser.get("spot_offset_mm")) + " mm",
                ),
                (
                    "Machine-space output polygon",
                    self.format_polygon(laser.get("output_polygon_machine_mm")),
                ),
            ),
        )
        self._add_group(
            "Camera / lens",
            (
                ("Camera", "Online" if camera.get("connected") else "Offline"),
                (
                    "Negotiated resolution",
                    self.format_xy(camera.get("resolution"), axes=("W", "H"))
                    + " px",
                ),
                (
                    "Configured resolution",
                    self.format_xy(
                        camera.get("configured_resolution"),
                        axes=("W", "H"),
                    )
                    + " px",
                ),
                (
                    "Display rotation",
                    f"{int(camera.get('display_rotation_degrees') or 0)}° clockwise "
                    "(presentation only)",
                ),
                ("Camera readiness", str(camera.get("readiness_state") or "Unknown")),
                ("Lens model", str(lens.get("state") or "MISSING")),
                ("Lens model ID", str(lens.get("model_id") or "Unavailable")),
            ),
        )
        self._add_group(
            "Camera-to-machine bed map",
            (
                ("State", str(bed.get("state") or "MISSING")),
                (
                    "Fit",
                    f"RMS {float(bed.get('rms_error_mm') or 0.0):.4f} mm · "
                    f"max {float(bed.get('max_error_mm') or 0.0):.4f} mm",
                ),
                (
                    "Evidence",
                    f"{int(bed.get('inlier_count') or 0)}/"
                    f"{int(bed.get('point_count') or 0)} inliers",
                ),
                (
                    "Axis mapping",
                    json.dumps(bed.get("axis_mapping") or {}, sort_keys=True),
                ),
            ),
        )
        self._add_group(
            "Honeycomb-local frame",
            (
                ("Reference state", str(support.get("state") or "MISSING")),
                (
                    "Configured physical span",
                    f"{expected_span:.3f} × {expected_span:.3f} mm",
                ),
                (
                    "Saved support size",
                    self.format_xy(recorded_size) + " mm"
                    if recorded_size is not None
                    else "Not recorded",
                ),
                (
                    "Mapped ruler origin",
                    self.format_xy(support.get("origin_machine_mm")) + " mm machine",
                ),
                (
                    "Rotation from machine X",
                    "Unavailable"
                    if support.get("rotation_degrees") is None
                    else f"{float(support['rotation_degrees']):+.4f}°",
                ),
                (
                    "Measured far-edge spans",
                    self.format_xy(measured_spans) + " mm"
                    if measured_spans is not None
                    else "Unavailable",
                ),
                (
                    "Measured machine-space corners",
                    self.format_polygon(support.get("raw_corners_machine_mm")),
                ),
                (
                    "Rigid local-frame corners",
                    self.format_polygon(support.get("rigid_corners_machine_mm")),
                ),
                (
                    "Output authority in honeycomb coordinates",
                    self.format_polygon(support.get("output_polygon_local_mm")),
                ),
            ),
        )

    def set_unavailable(self, message: str) -> None:
        self.overall_status.setText("Coordinate audit unavailable")
        self.blockers.setText(message)
        self.next_action.setText("Next action unavailable")
        self.tree.clear()

    def set_point(self, point: dict[str, Any]) -> None:
        local = point.get("honeycomb_local_mm")
        local_text = "Unavailable" if local is None else self.format_xy(local) + " mm"
        reference_state = str(point.get("honeycomb_reference_state") or "MISSING")
        if reference_state != "CURRENT" and local is not None:
            local_text += f"  ({reference_state}; diagnostic only)"
        lines = (
            "Displayed pixel: "
            + self.format_xy(point.get("display_pixel"), axes=("X", "Y"))
            + " px",
            "Lens-corrected source pixel: "
            + self.format_xy(
                point.get("lens_corrected_source_pixel"),
                axes=("X", "Y"),
            )
            + " px",
            f"Machine coordinate: {self.format_xy(point.get('machine_mm'))} mm",
            f"Honeycomb-local coordinate: {local_text}",
            "Carriage command for beam placement: "
            + self.format_xy(point.get("spot_corrected_carriage_mm"))
            + " mm",
            "Beam point inside configured machine/work rectangle: "
            + ("YES" if point.get("inside_machine_work_area") else "NO"),
            "Carriage command inside configured machine/work rectangle: "
            + ("YES" if point.get("carriage_inside_machine_work_area") else "NO"),
            "Beam point inside base output authority: "
            + ("YES" if point.get("inside_guarded_beam_authority") else "NO"),
            "Carriage command inside base output authority: "
            + ("YES" if point.get("inside_guarded_carriage_authority") else "NO"),
            "Beam + carriage satisfy guarded output authority: "
            + ("YES" if point.get("inside_guarded_laser_output") else "NO"),
            "Inside saved honeycomb: "
            + (
                "UNKNOWN"
                if point.get("inside_honeycomb") is None
                else "YES"
                if point.get("inside_honeycomb")
                else "NO"
            ),
        )
        self.point_details.setPlainText("\n".join(lines))

    def set_point_error(self, message: str) -> None:
        self.point_details.setPlainText(message)


__all__ = ["CoordinateAuditPanel"]
