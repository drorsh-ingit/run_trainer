"""Regression tests for the "Adjust a plan" chat revision flow.

Bug: adjusting a plan returned "Load failed" in the browser. Root cause was that the
plan we handed the model for revision had every workout `description` stripped (to save
tokens). The model mirrors the structure it is shown, so it returned workouts WITHOUT a
`description`, and `ClaudePlanResponse` requires `description` -> the revision raised a
validation error on every attempt (182 errors for a 46-week plan). The slow, silent model
call then also let the browser's streaming fetch drop, surfacing as "Load failed".

These tests pin the fix: the plan we show the model must retain `description` so the model
can preserve/edit real descriptions and produce schema-valid output.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.claude import _slim_plan_for_revision, _REVISION_WORKOUT_FIELDS


def _sample_plan():
    return {
        "summary": "12 week plan",
        "total_weeks": 2,
        "weeks": [
            {
                "week_number": 1,
                "theme": "Initial: Aerobic Base",
                "total_km": 20.0,
                "workouts": [
                    {
                        "day_of_week": "Tuesday",
                        "type": "easy",
                        "description": "5km easy @ 6:00/km, HR zone 2 (70% max), RPE 3.",
                        "distance_km": 5.0,
                        "distance_label": "5km",
                        "duration_minutes": 30,
                        "is_optional": False,
                        "steps": [{"foo": "bar"}],  # extraneous field must be dropped
                    },
                ],
            },
        ],
    }


def test_slim_plan_retains_description():
    slim = _slim_plan_for_revision(_sample_plan())
    workout = slim["weeks"][0]["workouts"][0]
    assert "description" in workout, "description must survive slimming or the model omits it"
    assert workout["description"].startswith("5km easy")


def test_description_is_in_the_revision_field_set():
    # Guards against a future refactor silently dropping description again.
    assert "description" in _REVISION_WORKOUT_FIELDS


def test_slim_plan_drops_unknown_fields():
    slim = _slim_plan_for_revision(_sample_plan())
    workout = slim["weeks"][0]["workouts"][0]
    assert "steps" not in workout
    assert set(workout).issubset(set(_REVISION_WORKOUT_FIELDS))


def test_slim_plan_preserves_week_structure():
    slim = _slim_plan_for_revision(_sample_plan())
    week = slim["weeks"][0]
    assert week["week_number"] == 1
    assert week["theme"] == "Initial: Aerobic Base"
    assert week["total_km"] == 20.0
    assert slim["total_weeks"] == 2


def test_slim_plan_handles_empty_plan():
    assert _slim_plan_for_revision({}) == {"summary": "", "total_weeks": None, "weeks": []}


def test_slim_plan_omits_missing_optional_fields():
    plan = {
        "weeks": [
            {"week_number": 1, "workouts": [{"day_of_week": "Monday", "type": "rest"}]},
        ],
    }
    workout = _slim_plan_for_revision(plan)["weeks"][0]["workouts"][0]
    assert workout == {"day_of_week": "Monday", "type": "rest"}  # only present keys kept


# --- endpoint context rehydration -------------------------------------------------
from datetime import date, timedelta
from types import SimpleNamespace

from routers.plans import _plan_to_context_request


def _fake_plan(**over):
    base = dict(
        plan_type="race", goal_distance=42.2, goal_date=date.today() + timedelta(days=30),
        plan_duration_weeks=None, schedule_description="3 runs/week",
        injuries=None, additional_notes=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_context_request_for_general_plan_does_not_raise():
    # General plans have no goal_distance/goal_date; strict validation would 500. model_construct
    # must accept them so the adjust chat works.
    req = _plan_to_context_request(_fake_plan(plan_type="general", goal_distance=None, goal_date=None, plan_duration_weeks=12))
    assert req.plan_type == "general"
    assert req.goal_distance_km is None
    assert req.plan_duration_weeks == 12
    assert req.schedule_description == "3 runs/week"


def test_context_request_for_race_plan_with_past_date_does_not_raise():
    # Revising a plan whose race already happened must not be blocked by the future-date rule.
    req = _plan_to_context_request(_fake_plan(goal_date=date.today() - timedelta(days=5)))
    assert req.goal_distance_km == 42.2
    assert req.goal_date < date.today()


def test_context_request_coerces_none_text_fields_to_empty():
    req = _plan_to_context_request(_fake_plan(schedule_description=None, injuries=None, additional_notes=None))
    assert req.schedule_description == ""
    assert req.injuries == ""
    assert req.additional_notes == ""
