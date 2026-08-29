from __future__ import annotations

from typing import Any

from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


class CoordinateAuditPanel(QtWidgets.QWidget):
    """Read-only coordinate evidence and clicked-point inspection panel."""

    _POINT_PROMPT = "Click the captured overlay to inspect a point."

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
        intro = QtWidgets.QLabel(
            "Read-only evidence across display, corrected-camera, machine/beam, "
            "honeycomb-local, and spot-corrected carriage frames. The overlay and "
            "point inspector never grant motion or laser-output authority."
        )
        intro.setWordWrap(True)
        intro.setObjectName("coordinateAuditIntro")
        layout.addWidget(intro)

        actions = QtWidgets.QHBoxLayout()
        self.capture_button = QtWidgets.QPushButton("Home / park and capture audit view")
        self.capture_button.setObjectName("coordinateAuditCapture")
        self.capture_button.setToolTip(
            "The only Coordinate Audit action that commands hardware. It reuses the "
            "laser-off Home / park precision-capture path."
        )
        self.refresh_button = QtWidgets.QPushButton("Refresh audit")
        self.refresh_button.setObjectName("coordinateAuditRefresh")
        self.refresh_button.setToolTip("Read current in-memory status; commands no hardware.")
        self.copy_button = QtWidgets.QPushButton("Copy report")
        self.copy_button.setObjectName("coordinateAuditCopy")
        self.copy_button.setToolTip("Copy current in-memory status; commands no hardware.")
        actions.addWidget(self.capture_button)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.capture_button.clicked.connect(self.captureRequested.emit)
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        self.copy_button.clicked.connect(self.copyRequested.emit)

        self.overall_status = QtWidgets.QLabel("Audit not refreshed")
        self.overall_status.setObjectName("coordinateAuditOverallStatus")
        self.overall_status.setWordWrap(True)
        layout.addWidget(self.overall_status)
        self.blockers = QtWidgets.QLabel()
        self.blockers.setObjectName("coordinateAuditBlockers")
        self.blockers.setWordWrap(True)
        self.blockers.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.blockers)
        self.next_action = QtWidgets.QLabel()
        self.next_action.setObjectName("coordinateAuditNextAction")
        self.next_action.setWordWrap(True)
        layout.addWidget(self.next_action)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setObjectName("coordinateAuditSplitter")
        preview.setMinimumSize(420, 320)
        splitter.addWidget(preview)
        details = QtWidgets.QWidget()
        details_layout = QtWidgets.QVBoxLayout(details)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("coordinateAuditTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(("Evidence", "Value"))
        self.tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.header().setStretchLastSection(True)
        details_layout.addWidget(self.tree, 1)
        point_group = QtWidgets.QGroupBox("Clicked corrected-camera point")
        point_layout = QtWidgets.QVBoxLayout(point_group)
        self.point_details = QtWidgets.QPlainTextEdit()
        self.point_details.setObjectName("coordinateAuditPointDetails")
        self.point_details.setReadOnly(True)
        self.point_details.setMaximumHeight(155)
        self.clear_point()
        point_layout.addWidget(self.point_details)
        details_layout.addWidget(point_group)
        splitter.addWidget(details)
        splitter.setSizes((620, 520))
        layout.addWidget(splitter, 1)

        self.legend = QtWidgets.QLabel(
            "Overlay: orange = configured machine/work boundary · green = guarded "
            "laser-output authority · magenta = accepted honeycomb/support · arrows "
            "show machine and honeycomb-local positive axes."
        )
        self.legend.setObjectName("coordinateAuditLegend")
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)

    @staticmethod
    def _xy(value: Any, axes: tuple[str, ...] = ("X", "Y")) -> str:
        if not isinstance(value, (list, tuple)) or len(value) < len(axes):
            return "Unavailable"
        parts: list[str] = []
        for index, axis in enumerate(axes):
            item = value[index]
            parts.append(f"{axis}—" if item is None else f"{axis}{float(item):.3f}")
        return "  ".join(parts)

    @staticmethod
    def _polygon(value: Any) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            return "Unavailable"
        try:
            return " · ".join(
                f"({float(point[0]):.3f}, {float(point[1]):.3f})" for point in value
            )
        except (IndexError, TypeError, ValueError):
            return "Unavailable"

    def _add_group(self, title: str, rows: tuple[tuple[str, str], ...]) -> None:
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

    def set_unavailable(self, message: str) -> None:
        self.overall_status.setText("BLOCKED · audit status unavailable")
        self.blockers.setText(str(message))
        self.next_action.clear()
        self.tree.clear()

    def set_status(self, audit: dict[str, Any]) -> None:
        state = str(audit.get("overall_state") or "BLOCKED")
        honeycomb = audit.get("honeycomb") or {}
        span = honeycomb.get("expected_span_mm")
        span_text = "not configured" if span is None else f"{float(span):.3f} mm"
        self.overall_status.setText(
            f"{state} · machine-specific physical honeycomb span {span_text}"
        )
        blockers = [str(item) for item in audit.get("blockers") or ()]
        self.blockers.setText(
            "No audit blockers."
            if not blockers
            else "BLOCKED because:\n• " + "\n• ".join(blockers)
        )
        self.next_action.setText(
            "Recommended action: " + str(audit.get("required_next_action") or "Unavailable")
        )

        identity = audit.get("machine_identity") or {}
        machine = audit.get("machine") or {}
        laser = audit.get("laser") or {}
        camera = audit.get("camera") or {}
        lens = audit.get("lens") or {}
        bed = audit.get("bed_map") or {}
        capture = machine.get("capture_pose") or {}
        home_position = capture.get("position_immediately_after_home") or {}
        before_position = capture.get("position_before_capture") or {}
        after_position = capture.get("position_after_capture") or {}
        capture_position = (
            after_position
            if after_position.get("available") is True
            else before_position
        )
        coordinate = capture.get("coordinate_state") or {}
        current_coordinate = machine.get("coordinate_state_reference") or {}
        self.tree.clear()
        self._add_group(
            "Running machine and calibration binding",
            (
                ("Saved machine", f"{identity.get('machine_name')} ({identity.get('machine_id')})"),
                ("Machine profile", str(identity.get("machine_profile_id") or "Unavailable")),
                ("Tool-head profile", str(identity.get("tool_head_profile_id") or "Unavailable")),
                ("Expected camera binding", str(identity.get("expected_camera_profile_id") or "Not configured")),
                ("Expected calibration binding", str(identity.get("expected_calibration_profile_id") or "Not configured")),
                ("Active calibration profile", str(identity.get("active_calibration_profile_id") or "Unavailable")),
                ("Binding", "MATCH" if identity.get("calibration_binding_matches") else "MISMATCH / MISSING"),
            ),
        )
        self._add_group(
            "Capture-time controller pose",
            (
                ("Capture evidence", str(capture.get("state") or "MISSING")),
                ("Trust at capture", "TRUSTED AT CAPTURE" if capture.get("trusted_at_capture") else "NOT TRUSTED AT CAPTURE"),
                ("Home / park", f"home={bool(capture.get('home_completed'))}, parked={bool(capture.get('parked'))}"),
                ("State immediately after Home", str(home_position.get("state") or home_position.get("error") or "Unavailable")),
                ("MPos immediately after Home", self._xy(home_position.get("mpos_mm"), ("X", "Y", "Z")) + " mm"),
                ("WPos immediately after Home", self._xy(home_position.get("wpos_mm"), ("X", "Y", "Z")) + " mm"),
                ("WCO immediately after Home", self._xy(home_position.get("wco_mm"), ("X", "Y", "Z")) + " mm"),
                ("Controller state", str(capture_position.get("state") or "Unavailable")),
                ("MPos", self._xy(capture_position.get("mpos_mm"), ("X", "Y", "Z")) + " mm"),
                ("WPos", self._xy(capture_position.get("wpos_mm"), ("X", "Y", "Z")) + " mm"),
                ("WCO", self._xy(capture_position.get("wco_mm"), ("X", "Y", "Z")) + " mm"),
                ("Workspace", str(coordinate.get("active_workspace") or "Unavailable")),
                ("Workspace offset", self._xy(coordinate.get("active_offset_mm"), ("X", "Y", "Z")) + " mm"),
                ("G92 offset", self._xy(coordinate.get("g92_offset_mm"), ("X", "Y", "Z")) + " mm"),
                ("Commanded photo position", self._xy(capture.get("commanded_photo_position_mm"), ("X", "Y", "Z")) + " mm"),
                ("Commanded/report error", self._xy(capture.get("commanded_position_error_xy_mm")) + " mm"),
                ("Burst stable", str(bool(capture.get("position_stable_during_capture")))),
                ("Before/after maximum delta", "Unavailable" if capture.get("maximum_position_delta_mm") is None else f"{float(capture['maximum_position_delta_mm']):.4f} mm"),
                ("Capture duration", "Unavailable" if capture.get("capture_duration_seconds") is None else f"{float(capture['capture_duration_seconds']):.3f} s"),
                ("Capture started", str(capture.get("capture_started_at") or "Unavailable")),
                ("Capture finished", str(capture.get("capture_finished_at") or "Unavailable")),
                ("Bed-map digest at capture", str(capture.get("bed_mapping_digest") or "Unavailable")),
                ("After cleanup", "CURRENTLY TRUSTED" if capture.get("current_position_trusted_after_cleanup") else "NOT CURRENTLY TRUSTED"),
            ),
        )
        work = machine.get("work_area_mm")
        self._add_group(
            "Current machine / controller",
            (
                ("Connection / protocol", f"{'Connected' if machine.get('connected') else 'Offline'} · {machine.get('backend')} / {machine.get('protocol')}"),
                ("Home / coordinate reference", "CURRENTLY TRUSTED" if machine.get("coordinate_reference_ready") else "NOT CURRENTLY TRUSTED"),
                ("Active workspace", str(current_coordinate.get("active_workspace") or "Unavailable")),
                ("Workspace offset", self._xy(current_coordinate.get("active_offset_mm"), ("X", "Y", "Z")) + " mm"),
                ("G92 offset", self._xy(current_coordinate.get("g92_offset_mm"), ("X", "Y", "Z")) + " mm"),
                ("Configured work rectangle", self._xy(work[:2], ("X min", "X max")) + " · " + self._xy(work[2:], ("Y min", "Y max")) + " mm" if isinstance(work, list) and len(work) == 4 else "Unavailable"),
                ("Photography position", self._xy(machine.get("photo_position_mm"), ("X", "Y", "Z")) + " mm"),
            ),
        )
        self._add_group(
            "Guarded laser-output authority",
            (
                ("Authority", str(laser.get("output_authority_kind") or "Unavailable")),
                ("Boundary margin", f"{float(laser.get('boundary_margin_mm') or 0.0):.3f} mm"),
                ("Laser spot offset", self._xy(laser.get("spot_offset_mm")) + " mm"),
                ("Beam authority", self._polygon(laser.get("output_polygon_machine_mm"))),
                ("Carriage authority", self._polygon(laser.get("carriage_authority_polygon_machine_mm"))),
            ),
        )
        bed_validity = bed.get("validity") or {}
        self._add_group(
            "Camera, lens, bed map, and support",
            (
                ("Camera", f"{camera.get('device') or 'Unavailable'} · {camera.get('width') or 0}×{camera.get('height') or 0}"),
                ("Lens", "CURRENT" if isinstance(lens.get("model"), dict) else "MISSING"),
                ("Bed map", str(bed_validity.get("state") or "MISSING")),
                ("Honeycomb support", str(honeycomb.get("state") or "MISSING")),
                ("Configured physical span", span_text),
                ("Recorded size", self._xy(honeycomb.get("recorded_size_mm"), ("width", "height")) + " mm"),
                ("Support origin", self._xy(honeycomb.get("origin_machine_mm")) + " mm"),
                ("Support rotation", "Unavailable" if honeycomb.get("rotation_degrees") is None else f"{float(honeycomb['rotation_degrees']):.3f}°"),
            ),
        )

    def set_point_error(self, message: str) -> None:
        self.point_details.setPlainText(str(message))

    def clear_point(self) -> None:
        self.point_details.setPlainText(self._POINT_PROMPT)

    def set_point(self, point: dict[str, Any]) -> None:
        def containment(value: Any) -> str:
            return "Unavailable" if value is None else "INSIDE" if value else "OUTSIDE"

        self.point_details.setPlainText(
            "\n".join(
                (
                    f"Display pixel: {self._xy(point.get('display_pixel'), ('u', 'v'))}",
                    f"Corrected/source pixel: {self._xy(point.get('lens_corrected_source_pixel'), ('u', 'v'))}",
                    f"Machine / desired beam: {self._xy(point.get('machine_mm'))} mm",
                    f"Honeycomb local: {self._xy(point.get('honeycomb_local_mm'))} mm",
                    f"Spot-corrected carriage: {self._xy(point.get('spot_corrected_carriage_mm'))} mm",
                    f"Machine work area: {containment(point.get('inside_machine_work_area'))}",
                    f"Guarded beam authority: {containment(point.get('inside_guarded_beam_authority'))}",
                    f"Guarded carriage authority: {containment(point.get('inside_guarded_carriage_authority'))}",
                    f"Honeycomb/support: {containment(point.get('inside_honeycomb'))}",
                )
            )
        )
