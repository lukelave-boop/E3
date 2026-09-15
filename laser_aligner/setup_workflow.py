"""UI-neutral setup evidence. Recorded observations never authorize motion."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, read_json

PRECISION_TARGET_MM = 0.1
REGIONS = (
    "lower-left", "lower-center", "lower-right", "middle-left", "center",
    "middle-right", "upper-left", "upper-center", "upper-right",
)
SURVEY_REGIONS = ("lower-left", "lower-right", "upper-right", "upper-left", "center")


@dataclass(frozen=True)
class SetupStep:
    id: str
    title: str
    action: str
    instructions: str
    prerequisites: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()


SETUP_STEPS = (
    SetupStep("mechanics", "Restrain the setup", "camera",
              "Secure camera, cable, honeycomb and workpiece; lock focus and camera controls.",
              (), ("current camera readiness", "operator restraint inspection")),
    SetupStep("lens", "Check lens calibration", "lens",
              "Review a current-resolution lens solve with distributed sharp captures.",
              (), ("accepted current-resolution lens model",)),
    SetupStep("bed", "Establish the support map", "bed",
              "Use the guarded keyed map and save the observed honeycomb frame.",
              ("lens",), ("current support map",)),
    SetupStep("datum", "Survey bed leveling and datum", "datum",
              "Reference the border, then measure a rigid known-thickness target at four corners and center.",
              ("bed",), ("current five-point survey", "explicit saved datum association")),
    SetupStep("gauge", "Measure, return and teach focus", "focus",
              "Measure a solid patch, return the laser to the measured spot, then teach the 7 mm gauge.",
              ("datum",), ("compatible saved gauge calibration",)),
    SetupStep("precision", "Assess one-height repeatability", "precision",
              "Use a fixed holdout target: ten captures without motion, then ten Home / park captures.",
              ("bed", "gauge"), ("current measured-plane identity", "10 still and 10 parked captures")),
    SetupStep("heights", "Collect height calibration", "heights",
              "Acquire lower and upper planes plus an independent middle plane without replacing the support map.",
              ("bed", "gauge"), ("current numeric accepted height model",)),
    SetupStep("qualification", "Record independent XY checks", "qualification",
              "Measure placement error across the approved area and heights; include measurement uncertainty.",
              ("bed", "gauge", "precision"), ("independent XY errors and uncertainty", "current setup identity")),
)


@dataclass(frozen=True)
class StepStatus:
    step_id: str
    state: str
    reason: str
    next_action: str


def evaluate_setup_steps(
    *, readiness: Mapping[str, bool], evidence: Mapping[str, Any],
    current_binding: Mapping[str, Any] | None = None,
    assessment_binding: str | None = None, survey_binding: str | None = None,
    saved_datum_mm: float | None = None,
) -> dict[str, StepStatus]:
    """Evaluate observations without IO; checklist marks never supply authority.

    Readiness is a caller-supplied snapshot of existing guarded service status.
    A completed camera step only establishes camera readiness; restraint remains
    an operator inspection and no status returned here authorizes hardware.
    """
    intrinsic = {
        "mechanics": ("review" if readiness.get("camera") else "ready",
                      "Camera ready; inspect physical restraints." if readiness.get("camera") else
                      "Connect the configured camera and verify its locked controls; inspect restraints."),
        "lens": ("complete" if readiness.get("lens") else "ready", "Accepted lens model is current." if
                 readiness.get("lens") else "Review or solve the lens at the current resolution."),
        "bed": ("complete" if readiness.get("bed") else "ready", "Support map is current." if
                readiness.get("bed") else "Acquire and accept a current support map."),
        "datum": ("ready", "Measure five positions and explicitly save the survey-associated datum."),
        "gauge": ("complete" if readiness.get("gauge") else "ready", "Saved gauge calibration is compatible." if
                  readiness.get("gauge") else "Refresh focus status and teach the 7 mm gauge."),
        "precision": ("ready", "Collect ten still and ten Home / park captures at one measured height."),
        "heights": ("complete" if readiness.get("heights") else "ready", "Height model passes the numeric gate." if
                    readiness.get("heights") else "Acquire lower, upper and independent middle-height evidence."),
        "qualification": ("ready", "Record independent placement errors, target coordinates and uncertainty."),
    }
    try:
        if evidence.get("bed_survey") is not None:
            survey = BedSurvey.from_dict(evidence["bed_survey"])
            if survey.binding != survey_binding:
                intrinsic["datum"] = ("stale", "Border reference changed or is unavailable; repeat the survey.")
            elif survey.saved_datum and survey.saved_datum["value_mm"] != saved_datum_mm:
                intrinsic["datum"] = ("stale", "Saved datum changed; review and explicitly save the survey datum again.")
            elif survey.summary()["complete"] and survey.saved_datum:
                intrinsic["datum"] = ("complete", "Five positions and the current saved datum are linked.")
        if evidence.get("repeatability") is not None:
            assessment = RepeatabilityAssessment.from_dict(evidence["repeatability"])
            if assessment.summary()["complete"]:
                recorded = json.loads(assessment.binding)
                current = None if assessment_binding is None else json.loads(assessment_binding)
                def rig_fields(binding):
                    fields = {key: value for key, value in binding.items() if key not in {
                        "measured_surface", "probe_measurement", "surface_model", "height_binding",
                    }}
                    height_binding = binding.get("height_binding") or {}
                    for key in ("mount_revision", "datum_id"):
                        fields[key] = fields.get(key, height_binding.get(key))
                    return fields
                old_rig = rig_fields(recorded)
                new_rig = {} if current is None else rig_fields(current)
                # A baseline predating the first named height generation remains
                # useful only as diagnostic history. A known generation changing
                # is a stale rig, even if the saved camera matrices are unchanged.
                pre_generation = any(old_rig[key] is None and new_rig.get(key) is not None
                                     for key in ("mount_revision", "datum_id"))
                for key in ("mount_revision", "datum_id"):
                    if old_rig[key] is None:
                        new_rig[key] = None
                if old_rig != new_rig:
                    intrinsic["precision"] = ("stale", "Camera, support, mount or datum changed; repeat the baseline assessment.")
                else:
                    measured = recorded.get("measured_surface") or recorded.get("probe_measurement") or {}
                    height = measured.get("surface_elevation_mm")
                    plane = "" if height is None else f" at recorded elevation {height:g} mm"
                    diagnostic = pre_generation or recorded.get("surface_model") != current.get("surface_model")
                    intrinsic["precision"] = ("complete", f"10 still + 10 parked captures{plane}; " + (
                        "earlier model baseline is diagnostic only." if diagnostic else "variation is not placement accuracy."))
            elif assessment.binding != assessment_binding:
                intrinsic["precision"] = ("stale", "Setup or measured surface changed during collection; start a new assessment.")
        if evidence.get("qualification") is not None:
            qualification = PrecisionQualification.from_dict(evidence["qualification"])
            if current_binding is None or qualification.binding != binding_json(current_binding):
                intrinsic["qualification"] = ("stale", "Setup identity changed; independent checks must be repeated.")
            else:
                review = qualification.review(current_binding)
                intrinsic["qualification"] = ("complete", "Independent checks qualify only their saved area and heights.") if review["passed"] else (
                    "ready", "; ".join(review["reasons"]))
    except (KeyError, TypeError, ValueError) as exc:
        # Invalid persisted evidence cannot make any evidence-dependent step complete.
        for key in ("datum", "precision", "qualification"):
            intrinsic[key] = ("stale", f"Saved evidence needs review: {exc}")
    statuses: dict[str, StepStatus] = {}
    for step in SETUP_STEPS:
        state, reason = intrinsic[step.id]
        unmet = [key for key in step.prerequisites if statuses[key].state != "complete"]
        action = step.action
        if unmet and state != "stale":
            state = "blocked"
            reason = "First complete: " + ", ".join(key for key in unmet) + ". " + reason
            action = next(item.action for item in SETUP_STEPS if item.id == unmet[0])
        statuses[step.id] = StepStatus(step.id, state, reason, action)
    return statuses


def _finite(value: Any, label: str) -> float:
    if isinstance(value, (bool, str)) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def binding_json(binding: Mapping[str, Any]) -> str:
    if not isinstance(binding, Mapping) or not binding:
        raise ValueError("Current setup identity is required")
    return json.dumps(dict(binding), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class SurveyObservation:
    region: str
    measurement_id: str
    top_elevation_mm: float
    target_thickness_mm: float
    carriage_xy_mm: tuple[float, float]
    observed_at: float

    def __post_init__(self) -> None:
        if (self.region not in SURVEY_REGIONS or not isinstance(self.measurement_id, str)
                or not self.measurement_id.strip() or len(self.measurement_id) > 160):
            raise ValueError("Choose a survey position and a current probe measurement")
        _finite(self.top_elevation_mm, "Top elevation")
        if _finite(self.target_thickness_mm, "Target thickness") <= 0:
            raise ValueError("Enter the positive measured thickness of the rigid survey target")
        if len(self.carriage_xy_mm) != 2:
            raise ValueError("Survey needs the actual measured carriage XY")
        for value in self.carriage_xy_mm:
            _finite(value, "Measured carriage coordinate")
        object.__setattr__(self, "carriage_xy_mm", tuple(self.carriage_xy_mm))
        if _finite(self.observed_at, "Measurement time") <= 0:
            raise ValueError("Survey needs the measurement time")

    @property
    def bed_elevation_mm(self) -> float:
        return self.top_elevation_mm - self.target_thickness_mm


@dataclass
class BedSurvey:
    binding: str
    observations: dict[str, SurveyObservation] = field(default_factory=dict)
    saved_datum: dict[str, float] | None = None

    def record(self, observation: SurveyObservation, current_binding: str) -> None:
        if current_binding != self.binding:
            raise ValueError("Border reference changed; start a new bed survey")
        if any(item.measurement_id == observation.measurement_id
               for region, item in self.observations.items() if region != observation.region):
            raise ValueError("Measure each survey position separately")
        if any(math.dist(item.carriage_xy_mm, observation.carriage_xy_mm) < .5
               for region, item in self.observations.items() if region != observation.region):
            raise ValueError("Survey positions must be physically distinct, not repeated at the same XY")
        self.observations[observation.region] = observation
        self.saved_datum = None

    def summary(self) -> dict[str, Any]:
        values = [item.bed_elevation_mm for item in self.observations.values()]
        complete = set(self.observations) == set(SURVEY_REGIONS)
        coverage_valid = False
        if complete:
            corners = [self.observations[key].carriage_xy_mm for key in SURVEY_REGIONS[:4]]
            xmin, xmax = min(xy[0] for xy in corners), max(xy[0] for xy in corners)
            ymin, ymax = min(xy[1] for xy in corners), max(xy[1] for xy in corners)
            expected = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax),
                        ((xmin + xmax) / 2, (ymin + ymax) / 2))
            coverage_valid = xmax - xmin > 1 and ymax - ymin > 1 and all(
                abs(self.observations[region].carriage_xy_mm[0] - xy[0]) <= (xmax - xmin) * .25
                and abs(self.observations[region].carriage_xy_mm[1] - xy[1]) <= (ymax - ymin) * .25
                for region, xy in zip(SURVEY_REGIONS, expected, strict=True)
            )
        return {
            "complete": complete and coverage_valid, "count": len(values), "coverage_valid": coverage_valid,
            "mean_mm": sum(values) / len(values) if values else None,
            "span_mm": max(values) - min(values) if values else None,
            "elevations_mm": {key: item.bed_elevation_mm for key, item in self.observations.items()},
        }

    def associate_saved_datum(self, value_mm: float, saved_at: float, current_binding: str) -> None:
        if self.binding != current_binding or not self.summary()["complete"]:
            raise ValueError("Only a complete survey with the current reference can be associated with a saved datum")
        value, timestamp = _finite(value_mm, "Saved datum"), _finite(saved_at, "Save time")
        if timestamp <= 0:
            raise ValueError("Saved datum needs its save time")
        self.saved_datum = {"value_mm": value, "saved_at": timestamp}

    def to_dict(self) -> dict[str, Any]:
        return {"binding": json.loads(self.binding),
                "observations": [{**asdict(item), "carriage_xy_mm": list(item.carriage_xy_mm)}
                                 for item in self.observations.values()],
                "saved_datum": self.saved_datum}

    @classmethod
    def from_dict(cls, raw: Any) -> BedSurvey:
        if not isinstance(raw, dict) or set(raw) != {"binding", "observations", "saved_datum"}:
            raise ValueError("Malformed bed survey evidence")
        if not isinstance(raw["observations"], list) or len(raw["observations"]) > 5:
            raise ValueError("Malformed bed survey observations")
        result = cls(binding_json(raw["binding"]))
        try:
            for item in raw["observations"]:
                observation = SurveyObservation(**item)
                if observation.region in result.observations:
                    raise ValueError("Duplicate bed survey position")
                result.record(observation, result.binding)
            saved = raw["saved_datum"]
            if saved is not None:
                if not isinstance(saved, dict) or set(saved) != {"value_mm", "saved_at"}:
                    raise ValueError("Malformed saved survey datum")
                result.associate_saved_datum(saved["value_mm"], saved["saved_at"], result.binding)
        except TypeError as exc:
            raise ValueError("Malformed bed survey observation") from exc
        return result


@dataclass
class RepeatabilityAssessment:
    """Ten explicit captures in each phase, with one unchanged point identity."""

    binding: str
    still: list[dict[str, tuple[float, float]]] = field(default_factory=list)
    parked: list[dict[str, tuple[float, float]]] = field(default_factory=list)

    def record(self, phase: str, points: Mapping[str, tuple[float, float]],
               current_binding: str) -> None:
        if phase not in {"still", "parked"}:
            raise ValueError("Unknown repeatability phase")
        if current_binding != self.binding:
            raise ValueError("Camera, map, surface or setup identity changed; restart the assessment")
        if phase == "parked" and len(self.still) != 10:
            raise ValueError("Complete ten captures without movement first")
        samples = self.still if phase == "still" else self.parked
        if len(samples) >= 10:
            raise ValueError("This phase already contains ten captures")
        if not points:
            raise ValueError("Capture must contain all confidently detected holdouts")
        checked = {
            str(key): (_finite(xy[0], "Observed X"), _finite(xy[1], "Observed Y"))
            for key, xy in points.items()
        }
        if self.still and set(checked) != set(self.still[0]):
            raise ValueError("The holdout identities changed or a point is missing")
        samples.append(checked)

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {"complete": len(self.still) == len(self.parked) == 10}
        for phase in ("still", "parked"):
            samples = getattr(self, phase)
            maximum = 0.0
            for first in samples:
                for second in samples:
                    maximum = max(maximum, *(math.dist(first[key], second[key]) for key in first))
            result[phase] = {"count": len(samples), "max_pairwise_mm": maximum if samples else None}
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"binding": json.loads(self.binding), "still": self.still,
                "parked": self.parked, "summary": self.summary()}

    @classmethod
    def from_dict(cls, raw: Any) -> RepeatabilityAssessment:
        if not isinstance(raw, dict) or set(raw) != {"binding", "still", "parked", "summary"}:
            raise ValueError("Malformed repeatability evidence")
        result = cls(binding_json(raw["binding"]))
        for phase in ("still", "parked"):
            if not isinstance(raw[phase], list) or len(raw[phase]) > 10:
                raise ValueError("Malformed repeatability capture count")
            for sample in raw[phase]:
                if not isinstance(sample, dict) or not sample or len(sample) > 1000:
                    raise ValueError("Malformed repeatability points")
                if any(not isinstance(xy, (list, tuple)) or len(xy) != 2 for xy in sample.values()):
                    raise ValueError("Malformed repeatability coordinates")
                result.record(phase, sample, result.binding)
        return result


@dataclass(frozen=True)
class PlacementObservation:
    plane: str
    region: str
    height_mm: float
    error_x_mm: float
    error_y_mm: float
    uncertainty_mm: float
    observed_at: float
    method: str
    target_x_mm: float
    target_y_mm: float

    def __post_init__(self) -> None:
        if self.plane not in {"lower", "middle", "upper"} or self.region not in REGIONS:
            raise ValueError("Unknown height or XY check position")
        for name in ("height_mm", "error_x_mm", "error_y_mm", "uncertainty_mm", "observed_at",
                     "target_x_mm", "target_y_mm"):
            _finite(getattr(self, name), name)
        if self.uncertainty_mm <= 0 or self.observed_at <= 0:
            raise ValueError("Independent measurements need positive uncertainty and observation time")
        if not isinstance(self.method, str) or not self.method.strip() or len(self.method) > 500:
            raise ValueError("Record the independent physical measurement method")

    @property
    def upper_error_mm(self) -> float:
        return math.hypot(self.error_x_mm, self.error_y_mm) + self.uncertainty_mm


@dataclass(frozen=True)
class PrecisionQualification:
    binding: str
    lower_mm: float
    upper_mm: float
    area: tuple[float, float, float, float]
    observations: tuple[PlacementObservation, ...]

    def __post_init__(self) -> None:
        binding_json(json.loads(self.binding))
        object.__setattr__(self, "area", tuple(self.area))
        object.__setattr__(self, "observations", tuple(self.observations))
        if any(not isinstance(item, PlacementObservation) for item in self.observations):
            raise ValueError("Qualification observations must be validated measurements")
        if _finite(self.lower_mm, "Lower height") > _finite(self.upper_mm, "Upper height"):
            raise ValueError("Lower height cannot exceed upper height")
        if len(self.area) != 4 or not all(math.isfinite(_finite(v, "Area bound")) for v in self.area):
            raise ValueError("Record a finite XY qualification area")
        if self.area[0] >= self.area[1] or self.area[2] >= self.area[3]:
            raise ValueError("Qualification area must have positive width and height")
        if len(self.observations) > 27:
            raise ValueError("Qualification has too many observations")
        keys = {(item.plane, item.region) for item in self.observations}
        if len(keys) != len(self.observations):
            raise ValueError("Duplicate qualification observation")

    def review(self, current_binding: Mapping[str, Any],
               height_range: tuple[float, float] | None = None,
               area: tuple[float, float, float, float] | None = None) -> dict[str, Any]:
        reasons = []
        if binding_json(current_binding) != self.binding:
            reasons.append("Setup identity changed; acquire fresh independent evidence")
        planes = ("lower",) if self.lower_mm == self.upper_mm else ("lower", "middle", "upper")
        expected = {(plane, region) for plane in planes for region in REGIONS}
        if {(item.plane, item.region) for item in self.observations} != expected:
            reasons.append("Complete all nine XY regions at each claimed height")
        for item in self.observations:
            expected_height = {"lower": self.lower_mm, "upper": self.upper_mm,
                               "middle": (self.lower_mm + self.upper_mm) / 2}[item.plane]
            if abs(item.height_mm - expected_height) > 1e-6:
                reasons.append("Observation height does not match the recorded qualification plane")
                break
        xs = (self.area[0], (self.area[0] + self.area[1]) / 2, self.area[1])
        ys = (self.area[2], (self.area[2] + self.area[3]) / 2, self.area[3])
        positions = dict(zip(REGIONS, ((x, y) for y in ys for x in xs), strict=True))
        for item in self.observations:
            if math.dist((item.target_x_mm, item.target_y_mm), positions[item.region]) > 0.001:
                reasons.append("Measured target XY does not cover its corner, edge midpoint or center of the claimed area")
                break
        maximum = max((item.upper_error_mm for item in self.observations), default=None)
        if maximum is not None and maximum > PRECISION_TARGET_MM + 1e-12:
            reasons.append("Measured radial XY error plus uncertainty exceeds 0.100 mm")
        if height_range is not None and not (
            self.lower_mm <= height_range[0] <= height_range[1] <= self.upper_mm
        ):
            reasons.append("Requested height range exceeds the independently checked range")
        if area is not None and not (
            self.area[0] <= area[0] < area[1] <= self.area[1]
            and self.area[2] <= area[2] < area[3] <= self.area[3]
        ):
            reasons.append("Requested XY area exceeds the independently checked area")
        return {"passed": not reasons, "reasons": reasons, "maximum_with_uncertainty_mm": maximum,
                "count": len(self.observations), "expected_count": len(expected)}

    def to_dict(self) -> dict[str, Any]:
        return {"binding": json.loads(self.binding), "lower_mm": self.lower_mm,
                "upper_mm": self.upper_mm, "area": list(self.area),
                "observations": [asdict(item) for item in self.observations]}

    @classmethod
    def from_dict(cls, raw: Any) -> PrecisionQualification:
        if not isinstance(raw, dict) or set(raw) != {
            "binding", "lower_mm", "upper_mm", "area", "observations",
        }:
            raise ValueError("Malformed precision qualification")
        if not isinstance(raw["observations"], list) or len(raw["observations"]) > 27:
            raise ValueError("Malformed precision observations")
        try:
            return cls(binding_json(raw["binding"]), raw["lower_mm"], raw["upper_mm"],
                       tuple(raw["area"]), tuple(PlacementObservation(**item) for item in raw["observations"]))
        except (KeyError, TypeError) as exc:
            raise ValueError("Malformed precision qualification") from exc


def qualifies_surface_model(qualification: PrecisionQualification, *, binding: Mapping[str, Any],
                            height_range: tuple[float, float],
                            area: tuple[float, float, float, float] | None = None) -> bool:
    return bool(qualification.review(binding, height_range, area)["passed"])


class PrecisionEvidenceStore:
    def __init__(self, directory: Path):
        self.path = directory / "precision_setup_evidence.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "qualification": None, "repeatability": None,
                    "bed_survey": None, "checklist": []}
        if self.path.stat().st_size > 250_000:
            raise ValueError("Precision evidence file is too large")
        raw = read_json(self.path, None)
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version", "qualification", "repeatability", "bed_survey", "checklist",
        } or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError("Unsupported or malformed precision evidence")
        if raw["qualification"] is not None:
            PrecisionQualification.from_dict(raw["qualification"])
        if raw["repeatability"] is not None:
            raw["repeatability"] = RepeatabilityAssessment.from_dict(raw["repeatability"]).to_dict()
        if raw["bed_survey"] is not None:
            raw["bed_survey"] = BedSurvey.from_dict(raw["bed_survey"]).to_dict()
        if not isinstance(raw["checklist"], list) or any(
            item not in {step.id for step in SETUP_STEPS} for item in raw["checklist"]
        ):
            raise ValueError("Malformed setup checklist")
        return raw

    def save(self, *, qualification: PrecisionQualification | None = None,
             repeatability: RepeatabilityAssessment | None = None,
             bed_survey: BedSurvey | None = None,
             checklist: list[str] | None = None) -> None:
        raw = self.load()
        if qualification is not None:
            raw["qualification"] = qualification.to_dict()
        if repeatability is not None:
            raw["repeatability"] = repeatability.to_dict()
        if bed_survey is not None:
            raw["bed_survey"] = bed_survey.to_dict()
        if checklist is not None:
            if any(item not in {step.id for step in SETUP_STEPS} for item in checklist):
                raise ValueError("Unknown setup checklist step")
            raw["checklist"] = list(checklist)
        atomic_write_json(self.path, raw)
