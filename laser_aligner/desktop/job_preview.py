from __future__ import annotations

import bisect
from pathlib import Path

from ..gcode.job_plan import JobPlan, PlannedMove
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _duration(value: float) -> str:
    seconds = max(0, int(round(float(value))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


class JobPreviewCanvas(QtWidgets.QGraphicsView):
    def __init__(
        self,
        plan: JobPlan,
        work_area: tuple[float, float, float, float],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.work_area = tuple(float(value) for value in work_area)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self._show_travel = True
        self._hidden_layers: set[str] = set()
        self._shade_power = False
        self._inverted = False
        self._elapsed = plan.total_seconds
        self._move_ends = [move.end_seconds for move in plan.moves]
        self._rendered_count = 0
        self._paths: dict[tuple[str, str], QtGui.QPainterPath] = {}
        self._items: dict[tuple[str, str], QtWidgets.QGraphicsPathItem] = {}
        self._active_item = QtWidgets.QGraphicsLineItem()
        active_pen = QtGui.QPen(QtGui.QColor("#FFD54F"))
        active_pen.setWidthF(1.1)
        active_pen.setCosmetic(True)
        self._active_item.setPen(active_pen)
        self._active_item.setZValue(30.0)
        self._scene.addItem(self._active_item)
        self._head_item = QtWidgets.QGraphicsEllipseItem(-1.2, -1.2, 2.4, 2.4)
        head_pen = QtGui.QPen(QtGui.QColor("#00D4FF"))
        head_pen.setCosmetic(True)
        head_pen.setWidthF(1.2)
        self._head_item.setPen(head_pen)
        self._head_item.setBrush(QtGui.QColor("#062E37"))
        self._head_item.setZValue(40.0)
        self._scene.addItem(self._head_item)
        self._draw_bed()
        self.set_elapsed(plan.total_seconds)

    def _draw_bed(self) -> None:
        x_min, x_max, y_min, y_max = self.work_area
        margin = max(x_max - x_min, y_max - y_min) * 0.06
        scene_rect = QtCore.QRectF(
            x_min - margin,
            -(y_max + margin),
            x_max - x_min + 2 * margin,
            y_max - y_min + 2 * margin,
        )
        self._scene.setSceneRect(scene_rect)
        bed = QtWidgets.QGraphicsRectItem(
            QtCore.QRectF(x_min, -y_max, x_max - x_min, y_max - y_min)
        )
        pen = QtGui.QPen(QtGui.QColor("#39B878"))
        pen.setWidthF(1.2)
        pen.setCosmetic(True)
        bed.setPen(pen)
        bed.setBrush(QtGui.QColor("#F4F5F2"))
        bed.setZValue(-20.0)
        self._scene.addItem(bed)
        self.setBackgroundBrush(QtGui.QColor("#202326"))

    @staticmethod
    def _key(move: PlannedMove) -> tuple[str, str]:
        if move.rapid:
            return "travel", ""
        if not move.laser_on:
            return "unpowered", move.layer_id or move.layer_name
        return "powered", move.layer_id or move.layer_name

    def _pen(self, key: tuple[str, str], move: PlannedMove) -> QtGui.QPen:
        role, _ = key
        if role == "travel":
            pen = QtGui.QPen(QtGui.QColor("#E45A5A"))
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            pen.setWidthF(0.55)
        elif role == "unpowered":
            pen = QtGui.QPen(QtGui.QColor("#E7B55C"))
            pen.setWidthF(0.55)
        elif self._shade_power:
            ratio = max(0.0, min(1.0, move.power / self.plan.power_max))
            value = int(round(225 - ratio * 190))
            if self._inverted:
                value = 255 - value
            pen = QtGui.QPen(QtGui.QColor(value, value, value))
            pen.setWidthF(0.75)
        else:
            color = move.layer_color if move.layer_color.startswith("#") else "#101010"
            pen = QtGui.QPen(QtGui.QColor(color))
            pen.setWidthF(0.75)
        pen.setCosmetic(True)
        return pen

    def _ensure_item(
        self,
        key: tuple[str, str],
        move: PlannedMove,
    ) -> QtWidgets.QGraphicsPathItem:
        item = self._items.get(key)
        if item is None:
            item = QtWidgets.QGraphicsPathItem()
            item.setPen(self._pen(key, move))
            item.setZValue(10.0 if key[0] == "powered" else 5.0)
            self._scene.addItem(item)
            self._items[key] = item
            self._paths[key] = QtGui.QPainterPath()
        return item

    def _reset_paths(self) -> None:
        for path in self._paths.values():
            path.clear()
        for item in self._items.values():
            item.setPath(QtGui.QPainterPath())
        self._rendered_count = 0

    def _append_through(self, count: int) -> None:
        changed: set[tuple[str, str]] = set()
        for move in self.plan.moves[self._rendered_count : count]:
            key = self._key(move)
            self._ensure_item(key, move)
            path = self._paths[key]
            path.moveTo(move.start_x, -move.start_y)
            path.lineTo(move.end_x, -move.end_y)
            changed.add(key)
        for key in changed:
            self._items[key].setPath(self._paths[key])
        self._rendered_count = count

    def set_elapsed(self, seconds: float) -> None:
        self._elapsed = max(0.0, min(float(seconds), self.plan.total_seconds))
        completed = bisect.bisect_right(self._move_ends, self._elapsed)
        if completed < self._rendered_count:
            self._reset_paths()
        self._append_through(completed)
        for key, item in self._items.items():
            item.setVisible(
                (self._show_travel or key[0] != "travel")
                and (not key[1] or key[1] not in self._hidden_layers)
            )

        active = self.move_at(self._elapsed)
        if active is None:
            self._active_item.hide()
            if self.plan.moves:
                final = self.plan.moves[-1]
                self._head_item.setPos(final.end_x, -final.end_y)
                self._head_item.show()
            else:
                self._head_item.hide()
            return
        fraction = (
            1.0
            if active.duration_seconds <= 1e-9
            else (self._elapsed - active.start_seconds) / active.duration_seconds
        )
        fraction = max(0.0, min(1.0, fraction))
        x = active.start_x + (active.end_x - active.start_x) * fraction
        y = active.start_y + (active.end_y - active.start_y) * fraction
        self._active_item.setLine(active.start_x, -active.start_y, x, -y)
        self._active_item.show()
        self._head_item.setPos(x, -y)
        self._head_item.show()

    def move_at(self, seconds: float) -> PlannedMove | None:
        if not self.plan.moves or seconds >= self.plan.total_seconds:
            return None
        index = bisect.bisect_right(self._move_ends, max(0.0, float(seconds)))
        return self.plan.moves[min(index, len(self.plan.moves) - 1)]

    def set_show_travel(self, enabled: bool) -> None:
        self._show_travel = bool(enabled)
        self.set_elapsed(self._elapsed)

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        if visible:
            self._hidden_layers.discard(str(layer_id))
        else:
            self._hidden_layers.add(str(layer_id))
        self.set_elapsed(self._elapsed)

    def set_power_shading(self, enabled: bool) -> None:
        self._shade_power = bool(enabled)
        for key, item in self._items.items():
            move = next(move for move in self.plan.moves if self._key(move) == key)
            item.setPen(self._pen(key, move))

    def set_inverted(self, enabled: bool) -> None:
        self._inverted = bool(enabled)
        self.setBackgroundBrush(QtGui.QColor("#F0F0F0" if enabled else "#202326"))
        for item in self._scene.items():
            if isinstance(item, QtWidgets.QGraphicsRectItem) and item.zValue() < 0:
                item.setBrush(QtGui.QColor("#17191B" if enabled else "#F4F5F2"))
        self.set_power_shading(self._shade_power)

    def fit_job(self) -> None:
        self.fitInView(self._scene.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        factor = 1.18 if event.angleDelta().y() > 0 else 1.0 / 1.18
        self.scale(factor, factor)
        event.accept()

    def render_image(self, path: Path, width: int = 1600) -> None:
        rect = self._scene.sceneRect()
        height = max(1, int(round(width * rect.height() / max(rect.width(), 1e-9))))
        image = QtGui.QImage(
            width,
            height,
            QtGui.QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(self.backgroundBrush().color())
        painter = QtGui.QPainter(image)
        self._scene.render(painter, QtCore.QRectF(0, 0, width, height), rect)
        painter.end()
        if not image.save(str(path)):
            raise OSError(f"Could not save preview image to {path}")


class JobPreviewDialog(QtWidgets.QDialog):
    startHereRequested = QtCore.Signal(int)

    def __init__(
        self,
        plan: JobPlan,
        work_area: tuple[float, float, float, float],
        job_name: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle(f"Job Preview — {job_name}")
        self.setModal(False)
        self.resize(1120, 760)
        self._elapsed = plan.total_seconds
        self._playback_speed = 1.0
        self._current_move_index: int | None = None
        self._clock = QtCore.QElapsedTimer()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._playback_tick)

        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel(
            f"Exact generated job · {plan.planner_mode} · preview controls do not "
            "alter machine output"
        )
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)
        self.layer_tree = self._build_layer_tree()
        layout.addWidget(self.layer_tree)
        self.canvas = JobPreviewCanvas(plan, work_area, self)
        layout.addWidget(self.canvas, 1)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10_000)
        self.slider.setValue(10_000)
        self.slider.valueChanged.connect(self._slider_changed)
        layout.addWidget(self.slider)

        playback = QtWidgets.QHBoxLayout()
        self.reset_button = QtWidgets.QPushButton("⏮ Start")
        self.play_button = QtWidgets.QPushButton("▶ Play")
        self.speed_combo = QtWidgets.QComboBox()
        for value in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0):
            self.speed_combo.addItem(f"{value:g}×", value)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(1.0))
        self.speed_combo.currentIndexChanged.connect(
            lambda: setattr(self, "_playback_speed", float(self.speed_combo.currentData()))
        )
        self.time_label = QtWidgets.QLabel()
        self.move_label = QtWidgets.QLabel()
        self.move_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        playback.addWidget(self.reset_button)
        playback.addWidget(self.play_button)
        playback.addWidget(QtWidgets.QLabel("Speed"))
        playback.addWidget(self.speed_combo)
        playback.addWidget(self.time_label)
        playback.addWidget(self.move_label, 1)
        layout.addLayout(playback)
        self.reset_button.clicked.connect(lambda: self.set_elapsed(0.0))
        self.play_button.clicked.connect(self._toggle_playback)

        options = QtWidgets.QHBoxLayout()
        self.travel_check = QtWidgets.QCheckBox("Show traversal moves")
        self.travel_check.setChecked(True)
        self.power_check = QtWidgets.QCheckBox("Shade according to power")
        self.invert_check = QtWidgets.QCheckBox("Invert dark material")
        self.legend_check = QtWidgets.QCheckBox("Show legend")
        self.legend_check.setChecked(True)
        self.fit_button = QtWidgets.QPushButton("Fit bed")
        options.addWidget(self.travel_check)
        options.addWidget(self.power_check)
        options.addWidget(self.invert_check)
        options.addWidget(self.legend_check)
        options.addStretch(1)
        options.addWidget(self.fit_button)
        layout.addLayout(options)
        self.travel_check.toggled.connect(self.canvas.set_show_travel)
        self.power_check.toggled.connect(self.canvas.set_power_shading)
        self.invert_check.toggled.connect(self.canvas.set_inverted)
        self.fit_button.clicked.connect(self.canvas.fit_job)
        keyboard_help = QtWidgets.QLabel(
            "Space play/pause · Home/End timeline · Left/Right step one second"
        )
        keyboard_help.setObjectName("mutedLabel")
        layout.addWidget(keyboard_help)

        details = QtWidgets.QHBoxLayout()
        statistics = QtWidgets.QLabel(
            f"Cut {plan.cut_distance_mm:.1f} mm ({_duration(plan.cut_seconds)}) · "
            f"Rapid/off {plan.travel_distance_mm:.1f} mm "
            f"({_duration(plan.travel_seconds)}) · Total {_duration(plan.total_seconds)} · "
            f"Max power {plan.maximum_power / plan.power_max * 100:.1f}% / "
            f"S{plan.maximum_power:g}"
        )
        if plan.source_order_travel_mm is not None and plan.planner_mode != "source order":
            statistics.setText(
                statistics.text()
                + f" · Planner saved {plan.planner_savings_mm:.1f} mm rapid travel"
            )
        statistics.setWordWrap(True)
        self.legend = QtWidgets.QLabel(self._legend_html())
        self.legend_check.toggled.connect(self.legend.setVisible)
        details.addWidget(statistics, 1)
        details.addWidget(self.legend)
        layout.addLayout(details)

        if plan.warnings:
            warning = QtWidgets.QLabel("Warnings: " + " · ".join(plan.warnings))
            warning.setObjectName("warningCard")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        self.save_button = buttons.addButton(
            "Save preview image…",
            QtWidgets.QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.start_here_button = buttons.addButton(
            "Prepare Start Here…",
            QtWidgets.QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.start_here_button.setToolTip(
            "Prepare a new guarded job beginning at the currently reviewed move; "
            "this does not start the machine"
        )
        self.start_here_button.clicked.connect(self._request_start_here)
        self.save_button.clicked.connect(self._save_image)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.set_elapsed(plan.total_seconds)
        QtCore.QTimer.singleShot(0, self.canvas.fit_job)

    def _layer_rows(self) -> list[dict[str, object]]:
        rows: dict[str, dict[str, object]] = {}
        for move in self.plan.moves:
            if not move.layer_id and not move.layer_name:
                continue
            key = move.layer_id or move.layer_name
            row = rows.setdefault(
                key,
                {
                    "id": key,
                    "name": move.layer_name,
                    "color": move.layer_color,
                    "mode": move.layer_mode,
                    "distance": 0.0,
                    "seconds": 0.0,
                    "power": 0.0,
                },
            )
            if move.laser_on:
                row["distance"] = float(row["distance"]) + move.distance_mm
                row["seconds"] = float(row["seconds"]) + move.duration_seconds
                row["power"] = max(float(row["power"]), move.power)
        return list(rows.values())

    def _build_layer_tree(self) -> QtWidgets.QTreeWidget:
        tree = QtWidgets.QTreeWidget()
        tree.setObjectName("previewLayers")
        tree.setHeaderLabels(("Show", "Operation", "Cut", "Time", "Max power"))
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setMaximumHeight(118)
        for row in self._layer_rows():
            item = QtWidgets.QTreeWidgetItem(
                [
                    "",
                    f"{row['name']} · {str(row['mode']).title()}",
                    f"{float(row['distance']):.1f} mm",
                    _duration(float(row["seconds"])),
                    f"{float(row['power']) / self.plan.power_max * 100:.1f}% / "
                    f"S{float(row['power']):g}",
                ]
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, str(row["id"]))
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.CheckState.Checked)
            swatch = QtGui.QPixmap(12, 12)
            swatch.fill(QtGui.QColor(str(row["color"])))
            item.setIcon(1, QtGui.QIcon(swatch))
            tree.addTopLevelItem(item)
        tree.itemChanged.connect(self._layer_visibility_changed)
        for column in range(tree.columnCount()):
            tree.resizeColumnToContents(column)
        return tree

    def _layer_visibility_changed(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        if column != 0:
            return
        layer_id = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        self.canvas.set_layer_visible(
            layer_id,
            item.checkState(0) == QtCore.Qt.CheckState.Checked,
        )

    def _legend_html(self) -> str:
        layers = " · ".join(
            f"<span style='color:{row['color']}'>— {row['name']}</span>"
            for row in self._layer_rows()
        )
        prefix = f"{layers} · " if layers else ""
        return (
            prefix
            + "<span style='color:#E45A5A'>-- Travel</span> · "
            "<span style='color:#E7B55C'>— Laser-off feed</span> · "
            "<span style='color:#00D4FF'>● Head</span>"
        )

    def set_elapsed(self, seconds: float) -> None:
        self._elapsed = max(0.0, min(float(seconds), self.plan.total_seconds))
        ratio = 0.0 if self.plan.total_seconds <= 0 else self._elapsed / self.plan.total_seconds
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(ratio * 10_000)))
        self.slider.blockSignals(False)
        self.canvas.set_elapsed(self._elapsed)
        self.time_label.setText(
            f"{_duration(self._elapsed)} / {_duration(self.plan.total_seconds)}"
        )
        move = self.canvas.move_at(self._elapsed)
        if move is None:
            self._current_move_index = None
            self.move_label.setText("Complete")
        else:
            self._current_move_index = move.index
            power = move.power / self.plan.power_max * 100.0
            role = "RAPID · laser off" if move.rapid else f"POWER {power:.1f}% / S{move.power:g}" if move.laser_on else "FEED · laser off"
            self.move_label.setText(
                f"Move {move.index + 1}/{len(self.plan.moves)} · {move.layer_name} · "
                f"pass {move.pass_index}/{move.pass_count} · {role} · "
                f"F{move.feed_mm_min:g} · X{move.end_x:.3f} Y{move.end_y:.3f}"
            )
        self.start_here_button.setEnabled(self._current_move_index is not None)

    def _slider_changed(self, value: int) -> None:
        self.set_elapsed(self.plan.total_seconds * value / 10_000.0)

    def _toggle_playback(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.play_button.setText("▶ Play")
            return
        if self._elapsed >= self.plan.total_seconds:
            self.set_elapsed(0.0)
        self._clock.restart()
        self._timer.start()
        self.play_button.setText("⏸ Pause")

    def _playback_tick(self) -> None:
        delta = self._clock.restart() / 1000.0 * self._playback_speed
        self.set_elapsed(self._elapsed + delta)
        if self._elapsed >= self.plan.total_seconds:
            self._timer.stop()
            self.play_button.setText("▶ Play")

    def _save_image(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save job preview",
            "job-preview.png",
            "PNG image (*.png)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        try:
            self.canvas.render_image(path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Job Preview", str(exc))

    def _request_start_here(self) -> None:
        if self._current_move_index is not None:
            self.startHereRequested.emit(self._current_move_index)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._timer.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key == QtCore.Qt.Key.Key_Space:
            self._toggle_playback()
        elif key == QtCore.Qt.Key.Key_Home:
            self.set_elapsed(0.0)
        elif key == QtCore.Qt.Key.Key_End:
            self.set_elapsed(self.plan.total_seconds)
        elif key == QtCore.Qt.Key.Key_Left:
            self.set_elapsed(self._elapsed - 1.0)
        elif key == QtCore.Qt.Key.Key_Right:
            self.set_elapsed(self._elapsed + 1.0)
        else:
            super().keyPressEvent(event)
            return
        event.accept()
