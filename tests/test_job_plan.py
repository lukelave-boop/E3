from dataclasses import FrozenInstanceError

import pytest

import laser_aligner.gcode.job_plan as job_plan_module
from laser_aligner.air_assist import AirAssistCommands, AirAssistMode, AirAssistSettings
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.gcode.job_plan import (
    build_job_plan,
    e3_metadata_line,
    restart_program_from_move,
)
from laser_aligner.machine.service import MachineService

GRBL_AIR = AirAssistCommands(
    mode=AirAssistMode.GRBL_COOLANT,
    protocol="grbl",
    fan_index=None,
    on_commands=("M8",),
    off_commands=("M9",),
)
MARLIN_AIR = AirAssistCommands(
    mode=AirAssistMode.MARLIN_FAN,
    protocol="marlin",
    fan_index=2,
    on_commands=("M106 P2 S255",),
    off_commands=("M107 P2",),
)


def test_job_plan_preserves_exact_move_context_and_timing() -> None:
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            e3_metadata_line("job", {"planner": "nearest path"}),
            e3_metadata_line(
                "planner",
                {"source_order_travel_mm": 12.5, "savings_mm": 2.5},
            ),
            e3_metadata_line(
                "layer",
                {"id": "line-01", "name": "Line 01", "color": "#185CFF"},
            ),
            e3_metadata_line("pass", {"index": 2, "count": 3}),
            e3_metadata_line("path", {"name": "Label R2 C4"}),
            "G0 X10 Y20 F600",
            "M4 S200",
            "G1 X40 Y20 F1200",
            "M5",
        ]
    )

    plan = build_job_plan(
        text,
        power_max=1000,
        start_position=(5.0, 20.0),
    )

    assert len(plan.moves) == 2
    rapid, cut = plan.moves
    assert rapid.rapid and not rapid.laser_on
    assert rapid.distance_mm == pytest.approx(5.0)
    assert rapid.duration_seconds == pytest.approx(0.5)
    assert cut.laser_on and not cut.rapid
    assert cut.power == pytest.approx(200.0)
    assert cut.feed_mm_min == pytest.approx(1200.0)
    assert cut.duration_seconds == pytest.approx(1.5)
    assert cut.layer_id == "line-01"
    assert cut.layer_name == "Line 01"
    assert cut.layer_color == "#185CFF"
    assert (cut.pass_index, cut.pass_count) == (2, 3)
    assert cut.source_name == "Label R2 C4"
    assert plan.total_seconds == pytest.approx(2.0)
    assert plan.maximum_power == pytest.approx(200.0)
    assert plan.powered
    assert plan.planner_mode == "nearest path"
    assert plan.source_order_travel_mm == pytest.approx(12.5)
    assert plan.planner_savings_mm == pytest.approx(2.5)
    with pytest.raises(FrozenInstanceError):
        cut.power = 500.0  # type: ignore[misc]


def test_job_plan_warns_about_but_does_not_hide_powered_rapid() -> None:
    plan = build_job_plan(
        "G90\nM4 S100\nG0 X10 Y10 F1000\nM5\n",
        power_max=1000,
    )

    assert plan.warnings == (
        "Line 3: rapid motion requested while laser is on",
    )
    assert len(plan.moves) == 1
    assert plan.moves[0].rapid
    assert not plan.moves[0].laser_on


def test_job_plan_applies_spot_offset_to_physical_preview_coordinates() -> None:
    plan = build_job_plan(
        "; Laser spot offset (spot = controller + offset): X-28 Y-8\n"
        "G90\nM5\nG0 X38 Y28 F1000\n",
        power_max=1000,
        start_position=(10.0, 20.0),
    )

    move = plan.moves[0]
    assert (move.end_x, move.end_y) == pytest.approx((10.0, 20.0))


def test_job_plan_timing_can_model_acceleration_and_command_latency() -> None:
    constant = build_job_plan(
        "G90\nG0 X10 Y0 F6000\n",
        power_max=1000,
    )
    modeled = build_job_plan(
        "G90\nG0 X10 Y0 F6000\n",
        power_max=1000,
        acceleration_mm_s2=500.0,
        command_delay_ms=25.0,
    )

    assert constant.total_seconds == pytest.approx(0.1)
    assert modeled.total_seconds > constant.total_seconds
    assert modeled.total_seconds == pytest.approx(2 * (10 / 500) ** 0.5 + 0.025)


def test_start_here_rebuilds_guarded_program_at_move_boundary() -> None:
    original = build_job_plan(
        "; Laser spot offset (spot = controller + offset): X-2 Y-3\n"
        "G21\nG90\nM5\nG0 X12 Y13 F1000\nM4 S200\n"
        "G1 X22 Y13 F600\nG1 X22 Y23 F600\nM5\n",
        power_max=1000,
    )

    text, restarted = restart_program_from_move(original, 2)

    assert text.index("M5 ; laser off before positioning") < text.index("G0 X22.000 Y13.000")
    assert "M4 S200" in text
    assert restarted.powered
    assert restarted.moves[0].start_x == pytest.approx(20.0)
    assert restarted.moves[-1].end_y == pytest.approx(20.0)


def test_start_here_records_park_pose_and_previews_exact_spot_approach() -> None:
    original = build_job_plan(
        "; Laser spot offset (spot = controller + offset): X-2 Y-3\n"
        "G21\nG90\nM5\nG0 X12 Y13 F1000\nM4 S200\n"
        "G1 X22 Y13 F600\nG1 X22 Y23 F600\nM5\n",
        power_max=1000,
    )

    text, restarted = restart_program_from_move(
        original,
        2,
        start_position=(50.0, 40.0),
    )

    assert '; @E3_JOB {"start_x":50.0,"start_y":40.0}' in text
    approach, selected = restarted.moves[:2]
    assert approach.rapid and not approach.laser_on
    assert (approach.start_x, approach.start_y) == pytest.approx((48.0, 37.0))
    assert (approach.end_x, approach.end_y) == pytest.approx((20.0, 10.0))
    assert selected.laser_on
    assert (selected.start_x, selected.start_y) == pytest.approx((20.0, 10.0))
    machine = MachineService(
        MachineSettings(backend="serial", allow_motion=True),
        LaserSettings(spot_offset_x_mm=-2.0, spot_offset_y_mm=-3.0),
        hardware_enabled=True,
    )
    preflight = machine.preflight_program(text)
    assert preflight.requires_motion
    assert preflight.requires_laser_authorization


def test_start_here_rejects_nonfinite_controller_start_pose() -> None:
    plan = build_job_plan("G90\nG0 X1 Y1\n", power_max=1000)

    with pytest.raises(ValueError, match="must be finite"):
        restart_program_from_move(plan, 0, start_position=(float("nan"), 0.0))


def test_start_here_rejects_unknown_move() -> None:
    plan = build_job_plan("G90\nG0 X1 Y1\n", power_max=1000)
    with pytest.raises(ValueError, match="outside"):
        restart_program_from_move(plan, 5)

def test_build_job_plan_scans_each_executable_line_once(monkeypatch) -> None:
    text = "\n".join(
        [
            "; comment-only line",
            "G21",
            "G90",
            "M5",
            '; @E3_LAYER {"id":"line-01","name":"Line 01"}',
            "G0 X10 Y0 F600",
            "M4 S200",
            "G1X20Y0F1200",
            "M5",
            "",
        ]
    )
    original_scan_word_state = job_plan_module.scan_word_state
    scanned_lines: list[tuple[str, bool]] = []

    def counted_scan_word_state(
        line: str,
        *,
        comments_stripped: bool = False,
    ):
        scanned_lines.append((line, comments_stripped))
        return original_scan_word_state(
            line,
            comments_stripped=comments_stripped,
        )

    monkeypatch.setattr(
        job_plan_module,
        "scan_word_state",
        counted_scan_word_state,
    )

    plan = job_plan_module.build_job_plan(
        text,
        power_max=1000,
        start_position=(0.0, 0.0),
    )

    assert len(plan.moves) == 2
    assert scanned_lines == [
        ("G21", True),
        ("G90", True),
        ("M5", True),
        ("G0 X10 Y0 F600", True),
        ("M4 S200", True),
        ("G1X20Y0F1200", True),
        ("M5", True),
    ]


def test_job_plan_records_exact_air_events_without_treating_fan_duty_as_laser_power() -> None:
    text = "\n".join(
        [
            "G90",
            e3_metadata_line(
                "layer",
                {
                    "id": "raster-01",
                    "name": "Raster 01",
                    "air_assist": True,
                },
            ),
            "M4 S100",
            "G1 X10 Y0 F600",
            "M106 P2 S255",
            "G1 X20 Y0 F600",
            "M5",
            "M107 P2",
        ]
    )

    plan = build_job_plan(
        text,
        power_max=1000,
        air_assist_commands=MARLIN_AIR,
    )

    assert [move.power for move in plan.moves] == [100.0, 100.0]
    assert all(move.air_assist for move in plan.moves)
    assert [event.command for event in plan.air_assist_events] == [
        "M106 P2 S255",
        "M107 P2",
    ]
    assert [event.enabled for event in plan.air_assist_events] == [True, False]
    assert plan.air_assist_commands == MARLIN_AIR


@pytest.mark.parametrize(
    ("protocol", "settings", "commands", "off_line", "on_line"),
    [
        (
            "grbl",
            AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT),
            GRBL_AIR,
            "m9",
            "m8",
        ),
        (
            "marlin",
            AirAssistSettings(mode=AirAssistMode.MARLIN_FAN, fan_index=2),
            MARLIN_AIR,
            "m107   p2",
            "m106   p2   s255",
        ),
    ],
)
def test_job_plan_records_case_and_whitespace_equivalent_accepted_air_commands(
    protocol: str,
    settings: AirAssistSettings,
    commands: AirAssistCommands,
    off_line: str,
    on_line: str,
) -> None:
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            off_line,
            "G0 X0 Y0 F600",
            on_line,
            "M4 S100",
            "G1 X1 Y0 F600",
            "M5",
            off_line,
            "M5",
        ]
    )

    validated = MachineService(
        MachineSettings(
            protocol=protocol,
            allow_motion=True,
            air_assist=settings,
        ),
        LaserSettings(power_max=1000),
        hardware_enabled=True,
    ).preflight_program(text)
    plan = build_job_plan(text, power_max=1000, air_assist_commands=commands)

    assert validated.requires_laser_authorization
    assert [event.command for event in plan.air_assist_events] == [
        off_line,
        on_line,
        off_line,
    ]
    assert [event.enabled for event in plan.air_assist_events] == [False, True, False]


def test_job_plan_rejects_untyped_air_command_input() -> None:
    with pytest.raises(TypeError, match="must be AirAssistCommands or None"):
        build_job_plan(
            "G90\nM5\n",
            power_max=1000,
            air_assist_commands=("M8", "M9"),  # type: ignore[arg-type]
        )


def test_start_here_replays_air_after_selected_travel_and_preserves_fail_off() -> None:
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            "M9",
            e3_metadata_line(
                "layer",
                {"id": "line-01", "name": "Line 01", "air_assist": True},
            ),
            e3_metadata_line("path", {"name": "First"}),
            "G0 X10 Y0 F1200",
            "M8",
            "M4 S100",
            "G1 X20 Y0 F600",
            "M5",
            e3_metadata_line("path", {"name": "Second"}),
            "G0 X30 Y0 F1200",
            "M4 S100",
            "G1 X40 Y0 F600",
            "M5",
            "M9",
            "M5",
        ]
    )
    plan = build_job_plan(text, power_max=1000, air_assist_commands=GRBL_AIR)
    selected_travel = next(
        move.index
        for move in plan.moves
        if move.rapid and move.source_name == "Second"
    )

    restarted_text, restarted = restart_program_from_move(plan, selected_travel)
    executable = [
        line.partition(";")[0].strip()
        for line in restarted_text.splitlines()
        if line.partition(";")[0].strip()
    ]

    selected_travel_line = next(
        index
        for index, line in enumerate(executable)
        if line.startswith("G0 X30.000 Y0.000")
    )
    assert selected_travel_line < executable.index("M8") < executable.index("M4 S100")
    assert executable.count("M8") == 1
    assert executable.count("M9") == 2
    assert executable[-3:] == ["M5", "M9", "M5"]
    assert [event.command for event in restarted.air_assist_events] == ["M9", "M8", "M9"]


def test_start_here_turns_air_off_before_zero_power_or_non_assist_layer() -> None:
    text = "\n".join(
        [
            "G90",
            "M5",
            "M9",
            e3_metadata_line(
                "layer",
                {"id": "assist", "name": "Assist", "air_assist": True},
            ),
            "G0 X10 Y0",
            "M8",
            "M4 S100",
            "G1 X20 Y0",
            "M5",
            e3_metadata_line(
                "layer",
                {"id": "zero", "name": "Zero", "air_assist": False},
            ),
            "G1 X30 Y0",
            e3_metadata_line(
                "layer",
                {"id": "plain", "name": "Plain", "air_assist": False},
            ),
            "M4 S100",
            "G1 X40 Y0",
            "M5",
            "M9",
            "M5",
        ]
    )
    plan = build_job_plan(text, power_max=1000, air_assist_commands=GRBL_AIR)

    restarted_text, _restarted = restart_program_from_move(plan, 0)

    assist_on = restarted_text.index("\nM8\n")
    zero_layer = restarted_text.index('"id":"zero"')
    transition_off = restarted_text.index("\nM9\n", assist_on)
    assert assist_on < transition_off < zero_layer
    assert restarted_text.count("\nM8\n") == 1


def test_start_here_configured_without_air_request_still_starts_and_ends_off() -> None:
    plan = build_job_plan(
        "G90\nM5\nM9\nG0 X10 Y0\nM4 S100\nG1 X20 Y0\nM5\nM9\nM5\n",
        power_max=1000,
        air_assist_commands=GRBL_AIR,
    )

    text, _restarted = restart_program_from_move(plan, 0)
    executable = [
        line.partition(";")[0].strip()
        for line in text.splitlines()
        if line.partition(";")[0].strip()
    ]

    assert "M8" not in executable
    assert executable[3] == "M9"
    assert executable[-3:] == ["M5", "M9", "M5"]


def test_start_here_fails_closed_when_air_metadata_has_no_resolved_mapping() -> None:
    plan = build_job_plan(
        e3_metadata_line(
            "layer",
            {"id": "assist", "name": "Assist", "air_assist": True},
        )
        + "\nG90\nG0 X10 Y0\nM4 S100\nG1 X20 Y0\nM5\n",
        power_max=1000,
    )

    with pytest.raises(SafetyError, match="exact resolved machine command mapping"):
        restart_program_from_move(plan, 0)


def test_start_here_resets_modal_power_before_coincident_assisted_layer() -> None:
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            "M9",
            e3_metadata_line(
                "layer",
                {"id": "plain", "name": "Plain", "air_assist": False},
            ),
            "G0 X10 Y0 F1200",
            "M4 S100",
            "G1 X20 Y0 F600",
            "M5",
            e3_metadata_line(
                "layer",
                {"id": "assist", "name": "Assist", "air_assist": True},
            ),
            # This coincident positioning command is intentionally absent from
            # JobPlan.moves, reproducing the cross-layer modal-state boundary.
            "G0 X20 Y0 F1200",
            "M8",
            "M4 S100",
            "G1 X30 Y0 F600",
            "M5",
            "M9",
            "M5",
        ]
    )
    plan = build_job_plan(text, power_max=1000, air_assist_commands=GRBL_AIR)

    restarted_text, restarted = restart_program_from_move(plan, 0)

    first_cut = restarted_text.index("G1 X20.000 Y0.000")
    transition_off = restarted_text.index("\nM5\n", first_cut)
    air_on = restarted_text.index("\nM8\n", transition_off)
    assisted_power = restarted_text.index("\nM4 S100\n", air_on)
    assert first_cut < transition_off < air_on < assisted_power
    assert restarted.powered

    validated = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            air_assist=AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT),
        ),
        LaserSettings(power_max=1000),
        hardware_enabled=True,
    ).preflight_program(restarted_text)
    assert validated.requires_laser_authorization
