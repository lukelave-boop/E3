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
        layout = QtWidgets.QVBoxLayout(self)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.splitter.setObjectName("coordinateAuditSplitter")
        self.splitter.setChildrenCollapsible(False)
        self._splitter_wide = False
        layout.addWidget(self.splitter, 1)

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
        self.splitter.addWidget(left_widget)

        right_widget = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_widget)
        intro = QtWidgets.QLabel(
            "Numbers and point inspection are read-only. Capture uses the existing "
            "laser-off Home / park path and records the controller pose at camera time."
        )
        intro.setToolTip(
            "Capture does not change bounds, G-code, calibration, honeycomb placement, "
            "or laser power."
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
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        header = self.tree.header()
        header.setMinimumSectionSize(120)
        header.setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.Interactive,
        )
        header.setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.Stretch,
        )
        header.resizeSection(0, 250)
        right.addWidget(self.tree, 1)

        self.point_group = QtWidgets.QGroupBox("Clicked-point transform")
        point_layout = QtWidgets.QVBoxLayout(self.point_group)
        self.point_details = QtWidgets.QPlainTextEdit()
        self.point_details.setObjectName("coordinateAuditPointDetails")
        self.point_details.setReadOnly(True)
        self.point_details.setMaximumHeight(185)
        self.point_details.setPlainText(
            "No point selected. Capture an audit view, then click a physical ruler "
            "mark or boundary."
        )
        point_layout.addWidget(self.point_details)
        self.point_group.hide()
        right.addWidget(self.point_group)
        legend = QtWidgets.QLabel(
            "Orange = configured machine/work rectangle · Green = guarded laser-output "
            "authority · Magenta = measured honeycomb · X+/Y+ arrows show frame directions."
        )
        legend.setWordWrap(True)
        legend.setObjectName("mutedLabel")
        right.addWidget(legend)
        self.splitter.addWidget(right_widget)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([430, 430])

    def _update_splitter_orientation(self) -> None:
        wide = self.width() >= 1080
        if wide == self._splitter_wide:
            return
        self._splitter_wide = wide
        self.splitter.setOrientation(
            QtCore.Qt.Orientation.Horizontal
            if wide
            else QtCore.Qt.Orientation.Vertical
        )
        self.splitter.setSizes([760, 620] if wide else [430, 430])

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._update_splitter_orientation()

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
            item = QtWidgets.QTreeWidgetItem((label, value))
            item.setToolTip(0, label)
            item.setToolTip(1, value)
            group.addChild(item)
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
        current_coordinate_state = machine.get("coordinate_state_reference") or {}
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
            else "Not trusted after motor release or before Home / park"
        )

        capture = machine.get("capture_pose") or {}
        home_position = capture.get("position_immediately_after_home") or {}
        before = capture.get("position_before_capture") or {}
        after = capture.get("position_after_capture") or {}
        position = after if after.get("available") is True else before
        capture_coordinate_state = capture.get("coordinate_state") or {}
        capture_state = str(capture.get("state") or "MISSING")
        capture_status = {
            "CURRENT": "TRUSTED AT CAPTURE",
            "STALE": "STALE — bed map changed after capture",
            "UNTRUSTED": "UNTRUSTED — position could not be verified",
            "MISSING": "NOT CAPTURED",
        }.get(capture_state, capture_state)
        home_state = (
            str(home_position.get("state") or "Unavailable")
            if home_position.get("available") is True
            else "Unavailable: " + str(home_position.get("error") or "not sampled")
        )
        delta = capture.get("maximum_position_delta_mm")
        stable_text = (
            "PASS"
            if capture.get("position_stable_during_capture") is True
            else "UNVERIFIED"
        )
        if type(delta) in {int, float}:
            stable_text += f" · maximum before/after Δ {float(delta):.4f} mm"
        command_error_xy = capture.get("commanded_position_error_xy_mm")
        command_error = capture.get("commanded_position_error_mm")
        command_error_text = "Unavailable"
        if (
            isinstance(command_error_xy, (list, tuple))
            and len(command_error_xy) >= 2
            and type(command_error_xy[0]) in {int, float}
            and type(command_error_xy[1]) in {int, float}
        ):
            command_error_text = (
                f"ΔX{float(command_error_xy[0]):+.4f}  "
                f"ΔY{float(command_error_xy[1]):+.4f} mm"
            )
            if type(command_error) in {int, float}:
                command_error_text += f" · radial {float(command_error):.4f} mm"
        motor_text = (
            "Released after capture; current XY is intentionally untrusted"
            if capture.get("motors_released_after_capture") is True
            else "Not released by the capture cleanup"
        )
        sampling_errors = [str(item) for item in capture.get("sampling_errors") or ()]
        sampling_text = "None" if not sampling_errors else "; ".join(sampling_errors)

        laser = audit.get("laser") or {}
        camera = audit.get("camera") or {}
        lens = audit.get("lens") or {}
        bed = audit.get("bed_map") or {}
        support = honeycomb
        recorded_size = support.get("recorded_size_mm")
        measured_spans = support.get("measured_spans_mm")

        self.tree.clear()
        self._add_group(
            "Capture-time controller pose",
            (
                ("Audit capture", capture_status),
                (
                    "Home / park completed",
                    "YES" if capture.get("home_completed") else "NO",
                ),
                ("Controller state immediately after Home", home_state),
                (
                    "MPos immediately after Home",
                    self.format_xy(
                        home_position.get("mpos_mm"),
                        axes=("X", "Y", "Z"),
                    )
                    + " mm",
                ),
                (
                    "WPos immediately after Home",
                    self.format_xy(
                        home_position.get("wpos_mm"),
                        axes=("X", "Y", "Z"),
                    )
                    + " mm",
                ),
                (
                    "WCO immediately after Home",
                    self.format_xy(
                        home_position.get("wco_mm"),
                        axes=("X", "Y", "Z"),
                    )
                    + " mm · "
                    + str(home_position.get("wco_source") or "source unavailable"),
                ),
                (
                    "Commanded photography pose",
                    self.format_xy(
                        capture.get("commanded_photo_position_mm")
                        or machine.get("photo_position_mm"),
                        axes=("X", "Y", "Z"),
                    )
                    + " mm",
                ),
                (
                    "Controller state at capture",
                    str(position.get("state") or "Unavailable"),
                ),
                (
                    "MPos at capture",
                    self.format_xy(position.get("mpos_mm"), axes=("X", "Y", "Z"))
                    + " mm",
                ),
                (
                    "WPos at capture",
                    self.format_xy(position.get("wpos_mm"), axes=("X", "Y", "Z"))
                    + " mm",
                ),
                (
                    "WCO at capture",
                    self.format_xy(position.get("wco_mm"), axes=("X", "Y", "Z"))
                    + " mm · "
                    + str(position.get("wco_source") or "source unavailable"),
                ),
                (
                    "Active workspace at capture",
                    str(capture_coordinate_state.get("active_workspace") or "Unavailable"),
                ),
                (
                    "Workspace offset at capture",
                    self.format_xy(
                        capture_coordinate_state.get("active_offset_mm"),
                        axes=("X", "Y", "Z"),
                    ),
                ),
                (
                    "G92 offset at capture",
                    self.format_xy(
                        capture_coordinate_state.get("g92_offset_mm"),
                        axes=("X", "Y", "Z"),
                    ),
                ),
                ("Pose stability during camera burst", stable_text),
                ("Commanded-vs-reported photo pose", command_error_text),
                ("Sampling errors", sampling_text),
                ("After capture", motor_text),
            ),
        )
        self._add_group(
            "Current controller / machine state",
            (
                ("Connection", "Connected" if machine.get("connected") else "Offline"),
                (
                    "Backend / protocol",
                    f"{machine.get('backend') or 'unknown'} / "
                    f"{machine.get('protocol') or 'unknown'}",
                ),
                (
                    "Current Home / park reference",
                    "READY"
                    if machine.get("coordinate_reference_ready")
                    else "NOT CURRENTLY TRUSTED",
                ),
                ("Current trusted position", trusted_text),
                (
                    "Current active GRBL workspace",
                    str(current_coordinate_state.get("active_workspace") or "Unavailable"),
                ),
                ("Configured motion/work rectangle", work_text),
                (
                    "Configured photography pose",
                    self.format_xy(
                        machine.get("photo_position_mm"),
                        axes=("X", "Y", "Z"),
                    )
                    + " mm",
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

    def clear_point(self) -> None:
        self.point_details.setPlainText(
            "No point selected. Capture an audit view, then click a physical ruler "
            "mark or boundary."
        )
        self.point_group.hide()

    def set_point(self, point: dict[str, Any]) -> None:
        self.point_group.show()
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
        self.point_group.show()
        self.point_details.setPlainText(message)


__all__ = ["CoordinateAuditPanel"]
