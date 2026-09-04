from dataclasses import FrozenInstanceError

import pytest

from laser_aligner.air_assist import (
    AIR_ASSIST_DIRECTIVE_PREFIX,
    AirAssistCommands,
    AirAssistMode,
    AirAssistSettings,
    AirAssistTarget,
)
from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import (
    CONTROLLER_DIALECT_REGISTRY,
    GRBL_DIALECT,
    MANUAL_QUERY_COMMANDS,
    MARLIN_DIALECT,
    CommandResponseKind,
    ControllerDialectRegistry,
    EmergencyStopPolicy,
    HomingResponseKind,
    ProbeAttempt,
    is_exact_grbl_locked_error_response,
    parse_grbl_coordinate_state,
    parse_grbl_realtime_status,
    parse_grbl_step_idle_delay,
    resolve_air_assist_commands,
)


def test_registry_is_ordered_frozen_and_uses_exact_probe_policy() -> None:
    assert CONTROLLER_DIALECT_REGISTRY.ids == ("grbl", "marlin")
    assert CONTROLLER_DIALECT_REGISTRY.dialects == (GRBL_DIALECT, MARLIN_DIALECT)
    assert CONTROLLER_DIALECT_REGISTRY.probe_attempts == (
        ProbeAttempt("grbl", "$I", 1.0),
        ProbeAttempt("marlin", "M115", 1.5),
    )
    assert CONTROLLER_DIALECT_REGISTRY.get("grbl") is GRBL_DIALECT
    assert CONTROLLER_DIALECT_REGISTRY.get("marlin") is MARLIN_DIALECT
    with pytest.raises(KeyError):
        CONTROLLER_DIALECT_REGISTRY.get("GRBL")
    with pytest.raises(FrozenInstanceError):
        GRBL_DIALECT.id = "changed"  # type: ignore[misc]


def test_registry_rejects_duplicate_or_unregistered_probe_ids() -> None:
    with pytest.raises(ValueError, match="IDs must be unique"):
        ControllerDialectRegistry(
            dialects=(GRBL_DIALECT, GRBL_DIALECT),
            probe_attempts=(),
        )
    with pytest.raises(ValueError, match="registered dialect"):
        ControllerDialectRegistry(
            dialects=(GRBL_DIALECT,),
            probe_attempts=(ProbeAttempt("missing", "ID", 1.0),),
        )


def test_auto_startup_and_identity_recognition_preserve_current_behavior() -> None:
    assert CONTROLLER_DIALECT_REGISTRY.recognize_startup(
        ["noise", "Grbl 1.1h ['$' for help]"]
    ) is GRBL_DIALECT
    assert CONTROLLER_DIALECT_REGISTRY.recognize_startup(
        ["start", "Marlin 2.1.2"]
    ) is None
    assert GRBL_DIALECT.recognizes_identity(["[VER:1.1h.20240101:build]"])
    assert GRBL_DIALECT.recognizes_identity(["controller=GRBL compatible"])
    assert not GRBL_DIALECT.recognizes_identity(["ok"])
    assert MARLIN_DIALECT.recognizes_identity(["FIRMWARE_NAME:Marlin 2.1.2"])
    assert MARLIN_DIALECT.recognizes_identity(["Marlin controller", "ok"])
    assert not MARLIN_DIALECT.recognizes_identity(["ok"])


def test_manual_query_union_remains_static_and_cross_dialect() -> None:
    assert GRBL_DIALECT.query_commands == frozenset({"$I", "$$", "$G", "$#"})
    assert MARLIN_DIALECT.query_commands == frozenset(
        {"M105", "M114", "M115", "M503"}
    )
    assert MANUAL_QUERY_COMMANDS == frozenset(
        {"$I", "$$", "$G", "$#", "M105", "M114", "M115", "M503"}
    )
    assert CONTROLLER_DIALECT_REGISTRY.manual_query_commands == MANUAL_QUERY_COMMANDS


def test_dialect_command_and_capability_facts_are_exact() -> None:
    assert GRBL_DIALECT.identity_query_command == "$I"
    assert GRBL_DIALECT.homing.command == "$H"
    assert GRBL_DIALECT.homing.timeout_floor_seconds == 120.0
    assert GRBL_DIALECT.homing.realtime_status_query == b"?"
    assert GRBL_DIALECT.homing.status_query_interval_seconds == 0.2
    assert GRBL_DIALECT.homing.accepts_active_to_idle_without_ack
    assert GRBL_DIALECT.motion_barrier_command == "G4 P0.01"
    assert GRBL_DIALECT.motor_release_command == "$MD"
    assert GRBL_DIALECT.coordinate_state_query_commands == ("$G", "$#")
    assert GRBL_DIALECT.realtime_status_query == b"?"
    assert GRBL_DIALECT.emergency_stop.raw_command == b"!\x18"
    assert GRBL_DIALECT.emergency_stop.line_command is None
    assert GRBL_DIALECT.command_errors_are_consumed

    assert MARLIN_DIALECT.identity_query_command == "M115"
    assert MARLIN_DIALECT.homing.command == "G28"
    assert MARLIN_DIALECT.homing.realtime_status_query is None
    assert not MARLIN_DIALECT.homing.accepts_active_to_idle_without_ack
    assert MARLIN_DIALECT.motion_barrier_command == "M400"
    assert MARLIN_DIALECT.motor_release_command == "M84"
    assert MARLIN_DIALECT.coordinate_state_query_commands == ()
    assert MARLIN_DIALECT.realtime_status_query is None
    assert MARLIN_DIALECT.emergency_stop.line_command == "M112"
    assert MARLIN_DIALECT.emergency_stop.raw_command is None
    assert not MARLIN_DIALECT.command_errors_are_consumed


def test_grbl_session_policy_is_command_data_not_execution() -> None:
    policy = GRBL_DIALECT.grbl_session
    assert policy is not None
    assert policy.settings_query_command == "$$"
    assert policy.format_step_idle_delay(250) == "$1=250"
    assert policy.format_step_idle_delay(255) == "$1=255"
    assert policy.unlock_command == "$X"
    assert policy.motor_disable_command == "$MD"
    assert policy.motor_sleep_command == "$SLP"
    assert policy.soft_reset_command == b"\x18"
    assert policy.sleep_before_reset_seconds == 0.1
    assert policy.reset_startup_delay_min_seconds == 0.1
    assert policy.reset_startup_delay_max_seconds == 5.0
    assert MARLIN_DIALECT.grbl_session is None


def test_dialects_have_no_transport_or_execution_authority() -> None:
    forbidden = {
        "open",
        "close",
        "write_raw",
        "write_line",
        "read_line",
        "drain",
        "arm",
        "authorize",
        "start_job",
        "request_stop",
    }

    for dialect in (GRBL_DIALECT, MARLIN_DIALECT):
        assert forbidden.isdisjoint(dir(dialect))


def test_air_assist_mapping_resolves_exact_immutable_controller_commands() -> None:
    grbl = resolve_air_assist_commands(
        AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT),
        protocol="grbl",
    )
    marlin = resolve_air_assist_commands(
        AirAssistSettings(mode=AirAssistMode.MARLIN_FAN, fan_index=4),
        protocol="marlin",
    )

    assert grbl == AirAssistCommands(
        mode=AirAssistMode.GRBL_COOLANT,
        protocol="grbl",
        fan_index=None,
        on_commands=("M8",),
        off_commands=("M9",),
    )
    assert marlin == AirAssistCommands(
        mode=AirAssistMode.MARLIN_FAN,
        protocol="marlin",
        fan_index=4,
        on_commands=("M106 P4 S255",),
        off_commands=("M107 P4",),
    )
    with pytest.raises(FrozenInstanceError):
        marlin.fan_index = 5  # type: ignore[misc]

    assert grbl.program_lines(True) == ("M8",)
    assert grbl.program_lines(False) == ("M9",)
    assert grbl.kind_for_program_line(" m8 (coolant) ; enabled") == "on"
    assert grbl.kind_for_program_line("  m9   ; disabled") == "off"
    assert marlin.kind_for_program_line("m106   p4 s255 ; enabled") == "on"
    assert marlin.kind_for_program_line("M107 (off) P4") == "off"
    assert grbl.kind_for_program_line("G0 X1") is None


def test_secondary_marlin_mapping_is_digest_bound_and_never_emits_primary_gcode(
) -> None:
    endpoint = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
    commands = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port=endpoint,
            baudrate=115200,
        ),
        protocol="grbl",
    )

    assert commands == AirAssistCommands(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        protocol="marlin",
        fan_index=None,
        on_commands=("M106 S255",),
        off_commands=("M106 S0",),
        target=AirAssistTarget.PI_SECONDARY,
        port=endpoint,
        baudrate=115200,
    )
    assert commands.target is AirAssistTarget.PI_SECONDARY
    assert len(commands.mapping_digest) == 64
    int(commands.mapping_digest, 16)
    expected_on = (
        f"{AIR_ASSIST_DIRECTIVE_PREFIX} {commands.mapping_digest} ON"
    )
    expected_off = (
        f"{AIR_ASSIST_DIRECTIVE_PREFIX} {commands.mapping_digest} OFF"
    )
    assert commands.program_lines(True) == (expected_on,)
    assert commands.program_lines(False) == (expected_off,)
    assert "M106" not in expected_on
    assert "M107" not in expected_off
    assert commands.kind_for_program_line(f"  {expected_on}  ") == "on"
    assert commands.kind_for_program_line(expected_off) == "off"
    assert commands.kind_for_program_line("G1 X1") is None

    changed_endpoint = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port=f"{endpoint}-other",
            baudrate=115200,
        ),
        protocol="grbl",
    )
    changed_baudrate = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port=endpoint,
            baudrate=250000,
        ),
        protocol="grbl",
    )
    assert changed_endpoint is not None
    assert changed_baudrate is not None
    assert changed_endpoint.mapping_digest != commands.mapping_digest
    assert changed_baudrate.mapping_digest != commands.mapping_digest

    for malformed in (
        f"{AIR_ASSIST_DIRECTIVE_PREFIX} {'0' * 64} ON",
        f"{AIR_ASSIST_DIRECTIVE_PREFIX}  {commands.mapping_digest} ON",
        f"{AIR_ASSIST_DIRECTIVE_PREFIX} {commands.mapping_digest} MAYBE",
        f"{AIR_ASSIST_DIRECTIVE_PREFIX} {commands.mapping_digest} ON extra",
        f"{AIR_ASSIST_DIRECTIVE_PREFIX}ED {commands.mapping_digest} ON",
    ):
        with pytest.raises(ValueError, match="exactly match"):
            commands.kind_for_program_line(malformed)


@pytest.mark.parametrize("protocol", ["grbl", "marlin"])
def test_secondary_marlin_mapping_accepts_only_explicit_primary_protocols(
    protocol: str,
) -> None:
    commands = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port="/dev/serial/by-id/secondary",
        ),
        protocol=protocol,
    )

    assert commands is not None
    assert commands.protocol == "marlin"
    assert commands.target is AirAssistTarget.PI_SECONDARY

    with pytest.raises(ValueError, match="explicit grbl or marlin"):
        resolve_air_assist_commands(
            AirAssistSettings(
                mode=AirAssistMode.SECONDARY_MARLIN_FAN,
                port="/dev/serial/by-id/secondary",
            ),
            protocol="auto",
        )


def test_disabled_air_assist_resolves_none_even_for_auto_protocol() -> None:
    assert (
        resolve_air_assist_commands(AirAssistSettings(), protocol="auto") is None
    )


def test_air_assist_mapping_rejects_ambiguous_or_forged_commands() -> None:
    with pytest.raises(ValueError, match="requires machine.protocol grbl"):
        resolve_air_assist_commands(
            AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT),
            protocol="auto",
        )
    with pytest.raises(ValueError, match="exact trusted"):
        AirAssistCommands(
            mode=AirAssistMode.GRBL_COOLANT,
            protocol="grbl",
            fan_index=None,
            on_commands=("M7",),
            off_commands=("M9",),
        )


def test_emergency_stop_policy_requires_exactly_one_command_form() -> None:
    with pytest.raises(ValueError, match="exactly one command"):
        EmergencyStopPolicy()
    with pytest.raises(ValueError, match="exactly one command"):
        EmergencyStopPolicy(raw_command=b"!", line_command="M112")


@pytest.mark.parametrize(
    ("response", "expected_grbl", "expected_marlin"),
    [
        ("ok", CommandResponseKind.ACKNOWLEDGEMENT, CommandResponseKind.ACKNOWLEDGEMENT),
        ("OK accepted", CommandResponseKind.MALFORMED, CommandResponseKind.ACKNOWLEDGEMENT),
        ("error:9", CommandResponseKind.ERROR, CommandResponseKind.ERROR),
        ("Error:Printer halted", CommandResponseKind.MALFORMED, CommandResponseKind.ERROR),
        ("ALARM:1", CommandResponseKind.ALARM, CommandResponseKind.ALARM),
        ("busy: processing", CommandResponseKind.CONTINUE, CommandResponseKind.CONTINUE),
        ("<Alarm|MPos:0,0,0>", CommandResponseKind.REALTIME_STATUS, CommandResponseKind.REALTIME_STATUS),
        ("[VER:1.1h]", CommandResponseKind.CONTINUE, CommandResponseKind.CONTINUE),
        (" ok", CommandResponseKind.MALFORMED, CommandResponseKind.MALFORMED),
        ("ok garbage", CommandResponseKind.MALFORMED, CommandResponseKind.ACKNOWLEDGEMENT),
        ("error-corrupt", CommandResponseKind.MALFORMED, CommandResponseKind.MALFORMED),
        ("alarm-no-code", CommandResponseKind.MALFORMED, CommandResponseKind.MALFORMED),
        ("Grbl 1.1h ['$' for help]", CommandResponseKind.STARTUP, CommandResponseKind.STARTUP),
        ("<Idle|MPos:0,0,0", CommandResponseKind.MALFORMED, CommandResponseKind.MALFORMED),
    ],
)
def test_command_response_classification_is_exact(
    response: str,
    expected_grbl: CommandResponseKind,
    expected_marlin: CommandResponseKind,
) -> None:
    assert GRBL_DIALECT.classify_command_response(response) is expected_grbl
    assert MARLIN_DIALECT.classify_command_response(response) is expected_marlin


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("ok", HomingResponseKind.ACKNOWLEDGEMENT),
        (" ok ", HomingResponseKind.MALFORMED),
        ("error:9", HomingResponseKind.REJECTION),
        ("ALARM:1", HomingResponseKind.REJECTION),
        ("<Alarm|MPos:0,0,0>", HomingResponseKind.REJECTION),
        ("<Home|MPos:1,1,0>", HomingResponseKind.ACTIVE),
        ("<Homing|MPos:1,1,0>", HomingResponseKind.ACTIVE),
        ("<Run:0|MPos:1,1,0>", HomingResponseKind.ACTIVE),
        ("<Idle|MPos:0,0,0>", HomingResponseKind.IDLE),
        ("Grbl 1.1h ['$' for help]", HomingResponseKind.STARTUP),
        ("<Idle|MPos:0,0,0", HomingResponseKind.MALFORMED),
        ("status", HomingResponseKind.CONTINUE),
    ],
)
def test_grbl_homing_response_classification_is_exact(
    response: str,
    expected: HomingResponseKind,
) -> None:
    assert GRBL_DIALECT.classify_homing_response(response) is expected


def test_marlin_does_not_infer_grbl_homing_state_evidence() -> None:
    assert (
        MARLIN_DIALECT.classify_homing_response("<Run|MPos:1,1,0>")
        is HomingResponseKind.CONTINUE
    )
    assert (
        MARLIN_DIALECT.classify_homing_response("<Idle|MPos:0,0,0>")
        is HomingResponseKind.IDLE
    )
    assert (
        MARLIN_DIALECT.classify_homing_response("Error:Printer halted")
        is HomingResponseKind.REJECTION
    )


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        (["$0=10", "$1=250", "$2=0", "ok"], 250),
        (["$1 = 250 (step idle delay, msec)", "ok"], 250),
        (["$1=250.000", "ok"], 250),
        (["$10=1", "$11=0.010", "ok"], None),
        (["$1=250.5", "ok"], None),
    ],
)
def test_grbl_step_idle_parser_preserves_report_variants(
    responses: list[str],
    expected: int | None,
) -> None:
    assert parse_grbl_step_idle_delay(responses) == expected


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("error:9", True),
        (" ERROR:9 ", True),
        ("error:90", False),
        ("alarm:9", False),
        ("error:9 extra", False),
    ],
)
def test_exact_grbl_locked_error_recognition(response: str, expected: bool) -> None:
    assert is_exact_grbl_locked_error_response(response) is expected


def test_grbl_coordinate_state_parser_reports_active_workspace_and_offsets() -> None:
    state = parse_grbl_coordinate_state(
        ["noise", "[GC:G1 G55 G17 G21 G90 G94 M5]", "ok"],
        [
            "[G54:0.000,0.000,0.000]",
            "[G55:1.250,-2.500,3.750]",
            "[G92:0.100,0.200,0.300]",
            "ok",
        ],
    )

    assert state == {
        "active_workspace": "G55",
        "active_offset_mm": [1.25, -2.5, 3.75],
        "g92_offset_mm": [0.1, 0.2, 0.3],
    }


def test_grbl_coordinate_state_parser_rejects_missing_and_nonfinite_facts() -> None:
    with pytest.raises(
        MachineError,
        match="active G54-G59 work-coordinate system",
    ):
        parse_grbl_coordinate_state(["[GC:G1 G21 G90]"], ["[G92:0,0,0]"])
    with pytest.raises(MachineError, match="active work offset and G92 offset"):
        parse_grbl_coordinate_state(["[GC:G1 G54 G90]"], ["[G54:0,0,0]"])

    enormous = "1" + ("0" * 400)
    with pytest.raises(MachineError, match="coordinate offsets must be finite"):
        parse_grbl_coordinate_state(
            ["[GC:G1 G54 G90]"],
            [f"[G54:{enormous},0,0]", "[G92:0,0,0]"],
        )


def test_grbl_realtime_status_parser_preserves_reported_and_derived_vectors() -> None:
    complete = parse_grbl_realtime_status(
        "<Idle|MPos:10.000,20.000,3.000|WPos:9.000,18.000,3.000|WCO:1.000,2.000,0.000>"
    )
    derived_wpos = parse_grbl_realtime_status("<Idle|MPos:10,20,3|WCO:1,2,0>")
    derived_wco = parse_grbl_realtime_status("<Idle|MPos:10,20,3|WPos:9,18,3>")
    derived_from_offsets = parse_grbl_realtime_status(
        "<Idle|WPos:9,18,3>",
        coordinate_state={
            "active_offset_mm": [0.75, 1.5, 0.0],
            "g92_offset_mm": [0.25, 0.5, 0.0],
        },
    )

    assert complete["mpos_mm"] == [10.0, 20.0, 3.0]
    assert complete["wpos_mm"] == [9.0, 18.0, 3.0]
    assert complete["wco_mm"] == [1.0, 2.0, 0.0]
    assert complete["wco_source"] == "reported"
    assert complete["xy_complete"] is True
    assert derived_wpos["wpos_mm"] == [9.0, 18.0, 3.0]
    assert derived_wpos["derived_fields"] == ["WPos"]
    assert derived_wco["wco_mm"] == [1.0, 2.0, 0.0]
    assert derived_wco["derived_fields"] == ["WCO"]
    assert derived_from_offsets["wco_mm"] == [1.0, 2.0, None]
    assert derived_from_offsets["mpos_mm"] == [10.0, 20.0, None]
    assert derived_from_offsets["derived_fields"] == ["WCO", "MPos"]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ("not-a-status-frame", "Invalid GRBL realtime status frame"),
        ("<|MPos:0,0,0>", "did not report a machine state"),
        ("<Idle|MPos:bad,2,0>", "invalid coordinate vector"),
        ("<Idle|MPos:nan,2,0>", "coordinates must be finite"),
        ("<Idle|MPos:1>", "at least X and Y"),
        ("<Idle|FS:0,0>", "did not report MPos or WPos"),
    ],
)
def test_grbl_realtime_status_parser_rejects_malformed_or_nonfinite_frames(
    response: str,
    message: str,
) -> None:
    with pytest.raises(MachineError, match=message):
        parse_grbl_realtime_status(response)
