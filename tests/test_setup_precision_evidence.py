from __future__ import annotations

import json
from dataclasses import replace

import pytest

from laser_aligner.setup_workflow import (
    REGIONS,
    SURVEY_REGIONS,
    BedSurvey,
    PlacementObservation,
    PrecisionEvidenceStore,
    PrecisionQualification,
    RepeatabilityAssessment,
    SurveyObservation,
    binding_json,
    qualifies_surface_model,
)

BINDING = {"camera": "optical-1", "model": "model-1", "datum": "border-1"}
AREA = (10.0, 210.0, 10.0, 210.0)


def qualification(*, lower=-1.5, upper=18.5, error=(0.03, 0.04), uncertainty=0.02):
    planes = (("lower", lower),) if lower == upper else (
        ("lower", lower), ("middle", (lower + upper) / 2), ("upper", upper),
    )
    positions = dict(zip(REGIONS, ((x, y) for y in (10., 110., 210.) for x in (10., 110., 210.)), strict=True))
    observations = tuple(
        PlacementObservation(plane, region, height, *error, uncertainty, 100.0,
                             "Independent measured target and calibrated optical scale", *positions[region])
        for plane, height in planes for region in REGIONS
    )
    return PrecisionQualification(binding_json(BINDING), lower, upper, AREA, observations)


def test_physical_qualification_requires_every_region_and_adds_radial_uncertainty():
    evidence = qualification(error=(0.06, 0.08), uncertainty=0.001)
    review = evidence.review(BINDING)
    assert not review["passed"]
    assert review["maximum_with_uncertainty_mm"] == pytest.approx(0.101)
    accepted = qualification(error=(0.0, 0.08), uncertainty=0.02)
    assert accepted.review(BINDING)["passed"]
    assert not replace(accepted, observations=accepted.observations[:-1]).review(BINDING)["passed"]


def test_one_height_cannot_qualify_a_height_range_or_larger_xy_area():
    evidence = qualification(lower=2, upper=2)
    assert evidence.review(BINDING)["passed"]
    assert qualifies_surface_model(evidence, binding=BINDING, height_range=(2, 2), area=AREA)
    assert not qualifies_surface_model(evidence, binding=BINDING, height_range=(2, 3), area=AREA)
    assert not qualifies_surface_model(evidence, binding=BINDING, height_range=(2, 2),
                                       area=(0, 210, 10, 210))


def test_model_datum_and_observed_plane_identity_are_required():
    evidence = qualification()
    assert not evidence.review(dict(BINDING, model="replaced"))["passed"]
    first = replace(evidence.observations[0], height_mm=0)
    assert not replace(evidence, observations=(first, *evidence.observations[1:])).review(BINDING)["passed"]
    with pytest.raises(ValueError, match="Duplicate"):
        replace(evidence, observations=(first, first))
    mislabeled = replace(evidence.observations[0], target_x_mm=110, target_y_mm=110)
    assert not replace(evidence, observations=(mislabeled, *evidence.observations[1:])).review(BINDING)["passed"]


@pytest.mark.parametrize("field,value", [
    ("error_x_mm", float("nan")), ("error_y_mm", float("inf")),
    ("uncertainty_mm", 0), ("uncertainty_mm", -1), ("height_mm", True),
    ("observed_at", 0), ("method", ""),
])
def test_rejects_unmeasured_or_invalid_physical_evidence(field, value):
    with pytest.raises(ValueError):
        replace(qualification().observations[0], **{field: value})


def test_repeatability_separates_stationary_noise_from_park_variation():
    evidence = RepeatabilityAssessment(binding_json(BINDING))
    with pytest.raises(ValueError, match="without movement"):
        evidence.record("parked", {"1": (0., 0.)}, evidence.binding)
    for _ in range(10):
        evidence.record("still", {"1": (1., 2.), "2": (3., 4.)}, evidence.binding)
    for index in range(10):
        evidence.record("parked", {"1": (1. + index * .01, 2.), "2": (3., 4.)}, evidence.binding)
    result = evidence.summary()
    assert result["complete"]
    assert result["still"]["max_pairwise_mm"] == 0
    assert result["parked"]["max_pairwise_mm"] == pytest.approx(.09)
    with pytest.raises(ValueError, match="already contains"):
        evidence.record("still", {"1": (1., 2.), "2": (3., 4.)}, evidence.binding)


def test_repeatability_rejects_changed_surface_identity_or_missing_holdouts():
    evidence = RepeatabilityAssessment(binding_json(BINDING))
    evidence.record("still", {"1": (1., 2.), "2": (3., 4.)}, evidence.binding)
    with pytest.raises(ValueError, match="identity changed"):
        evidence.record("still", {"1": (1., 2.)}, binding_json(dict(BINDING, surface="new")))
    with pytest.raises(ValueError, match="missing"):
        evidence.record("still", {"1": (1., 2.)}, evidence.binding)
    assert len(evidence.still) == 1


def test_survey_uses_target_thickness_and_rejects_reusing_contacts_or_reference():
    reference = binding_json({"reference_id": "reference-1"})
    survey = BedSurvey(reference)
    first = SurveyObservation(SURVEY_REGIONS[0], "measurement-1", 3.5, 5.0, (0., 0.), 100.)
    survey.record(first, reference)
    assert survey.summary()["mean_mm"] == -1.5
    with pytest.raises(ValueError, match="separately"):
        survey.record(replace(first, region=SURVEY_REGIONS[1]), reference)
    with pytest.raises(ValueError, match="reference changed"):
        survey.record(first, "reference-2")
    for index, (region, xy) in enumerate(zip(SURVEY_REGIONS[1:], ((100., 0.), (100., 100.), (0., 100.), (50., 50.)), strict=True), 2):
        survey.record(SurveyObservation(region, f"measurement-{index}", 3.6, 5., xy, 100.), reference)
    assert survey.summary()["complete"]
    assert survey.summary()["span_mm"] == pytest.approx(.1)
    survey.associate_saved_datum(-1.42, 200., reference)
    restored = BedSurvey.from_dict(survey.to_dict())
    assert restored.saved_datum == {"value_mm": -1.42, "saved_at": 200.}
    with pytest.raises(ValueError, match="current reference"):
        survey.associate_saved_datum(-1.42, 201., "new-reference")


def test_survey_rejects_new_ids_at_the_same_actual_xy():
    reference = binding_json({"reference_id": "reference-1"})
    survey = BedSurvey(reference)
    first = SurveyObservation(SURVEY_REGIONS[0], "measurement-1", 3.5, 5., (1., 2.), 100.)
    survey.record(first, reference)
    with pytest.raises(ValueError, match="physically distinct"):
        survey.record(replace(first, region=SURVEY_REGIONS[1], measurement_id="new-contact"), reference)


def test_evidence_store_recomputes_results_and_rejects_unknown_schema(tmp_path):
    store = PrecisionEvidenceStore(tmp_path)
    evidence = qualification()
    repeatability = RepeatabilityAssessment(binding_json(BINDING))
    repeatability.record("still", {"1": (1., 2.)}, repeatability.binding)
    store.save(qualification=evidence, repeatability=repeatability, checklist=["mechanics"])
    restored = PrecisionQualification.from_dict(store.load()["qualification"])
    assert restored.review(BINDING)["passed"]
    assert not restored.review(dict(BINDING, camera="changed"))["passed"]
    raw = json.loads(store.path.read_text())
    raw["repeatability"]["summary"] = {"complete": True}
    store.path.write_text(json.dumps(raw))
    assert not store.load()["repeatability"]["summary"]["complete"]
    raw["schema_version"] = True
    store.path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Unsupported"):
        store.load()


def test_malformed_saved_pass_flag_does_not_grant_qualification():
    raw = qualification().to_dict()
    raw["passed"] = True
    with pytest.raises(ValueError, match="Malformed"):
        PrecisionQualification.from_dict(raw)


def test_setup_progress_uses_prerequisites_and_never_manual_checkmarks():
    from laser_aligner.setup_workflow import SETUP_STEPS, evaluate_setup_steps

    states = evaluate_setup_steps(readiness={}, evidence={"checklist": [step.id for step in SETUP_STEPS]})
    assert all(state.state != "complete" for state in states.values())
    assert states["bed"].state == "blocked"
    assert states["bed"].next_action == "lens"
    assert "lens" in states["bed"].reason
    assert all(step.evidence_requirements for step in SETUP_STEPS)


def test_setup_progress_invalidates_reference_surface_model_and_saved_datum():
    from laser_aligner.setup_workflow import evaluate_setup_steps

    reference = binding_json({"reference": "border-1"})
    survey = BedSurvey(reference)
    for index, region in enumerate(SURVEY_REGIONS):
        xy = ((0, 0), (100, 0), (100, 100), (0, 100), (50, 50))[index]
        survey.record(SurveyObservation(region, f"probe-{index}", 3.5, 5., xy, 100.), reference)
    survey.associate_saved_datum(-1.5, 200., reference)
    assessment = RepeatabilityAssessment(binding_json(BINDING))
    for phase in ("still", "parked"):
        for _ in range(10):
            assessment.record(phase, {"target": (1., 2.)}, binding_json(BINDING))
    evidence = {"bed_survey": survey.to_dict(), "repeatability": assessment.to_dict(),
                "qualification": qualification().to_dict()}
    arguments = dict(readiness={key: True for key in ("camera", "lens", "bed", "gauge", "heights")},
                     evidence=evidence, current_binding=BINDING, assessment_binding=binding_json(BINDING),
                     survey_binding=reference, saved_datum_mm=-1.5)
    statuses = evaluate_setup_steps(**arguments)
    assert statuses["mechanics"].state == "review"
    assert all(statuses[key].state == "complete" for key in ("lens", "bed", "datum", "gauge", "precision", "heights", "qualification"))
    stale = evaluate_setup_steps(**dict(arguments, survey_binding="new-reference"))
    assert stale["datum"].state == "stale" and stale["gauge"].state == "blocked"
    stale = evaluate_setup_steps(**dict(arguments, saved_datum_mm=-1.4))
    assert stale["datum"].state == "stale"
    stale = evaluate_setup_steps(**dict(arguments, assessment_binding=binding_json(dict(BINDING, camera="new-camera"))))
    assert stale["precision"].state == "stale" and stale["qualification"].state == "blocked"
    stale = evaluate_setup_steps(**dict(arguments, current_binding=dict(BINDING, model="new-model")))
    assert stale["qualification"].state == "stale"


def test_completed_baseline_keeps_recorded_plane_across_new_measurements_but_not_rig_changes():
    from laser_aligner.setup_workflow import evaluate_setup_steps

    rig = dict(BINDING, mount_revision="mount-1", datum_id="datum-1", surface_model="model-1")
    recorded = dict(rig, measured_surface={"measurement_id": "first", "surface_elevation_mm": 2.})
    assessment = RepeatabilityAssessment(binding_json(recorded))
    for phase in ("still", "parked"):
        for _ in range(10):
            assessment.record(phase, {"target": (1., 2.)}, binding_json(recorded))
    def status(binding):
        return evaluate_setup_steps(readiness={}, evidence={"repeatability": assessment.to_dict()},
                                    assessment_binding=binding_json(binding))["precision"]
    current = dict(rig, measured_surface={"measurement_id": "next", "surface_elevation_mm": 18.})
    assert status(current).state != "stale"
    assert "recorded elevation 2" in status(current).reason
    assert "diagnostic only" in status(dict(current, surface_model="new-model")).reason
    for key in ("camera", "mount_revision", "datum_id"):
        assert status(dict(current, **{key: "changed"})).state == "stale"


def test_frozen_evidence_copies_mutable_constructor_coordinates_and_collections():
    accepted = qualification()
    area, observations = list(accepted.area), list(accepted.observations)
    frozen = replace(accepted, area=area, observations=observations)
    area[0] += 50
    observations.clear()
    assert frozen.review(BINDING)["passed"]
    xy = [10., 20.]
    contact = SurveyObservation("center", "probe-1", 3.5, 5., xy, 100.)
    xy[0] = 999.
    assert contact.carriage_xy_mm == (10., 20.)
