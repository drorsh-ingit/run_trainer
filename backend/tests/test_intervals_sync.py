"""Tests for the Intervals.icu activity pull (services/intervals.py).

Pure-logic helpers plus a DB-backed sync test that stubs the network fetch and
the AI rescore so the matching/storage pipeline is exercised in isolation.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.intervals as isvc
from database import Base
from models.models import (
    IgnoredActivity,
    IntervalsSession,
    PlannedWorkout,
    TrainingPlan,
    User,
    WorkoutActivity,
)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_collapse_hr_zones_merges_seven_into_five():
    # 7 buckets: zones 5-7 collapse into the top zone (banker's rounding: 62.5 -> 62)
    assert isvc._collapse_hr_zones([600, 300, 60, 0, 0, 0, 0]) == [62, 31, 6, 0, 0]
    assert isvc._collapse_hr_zones([0, 0, 0, 0, 10, 20, 30]) == [0, 0, 0, 0, 100]


def test_collapse_hr_zones_pads_when_fewer_than_five():
    assert isvc._collapse_hr_zones([50, 50]) == [50, 50, 0, 0, 0]


def test_collapse_hr_zones_returns_none_when_empty():
    assert isvc._collapse_hr_zones(None) is None
    assert isvc._collapse_hr_zones([]) is None
    assert isvc._collapse_hr_zones([0, 0, 0, 0, 0, 0, 0]) is None


def test_is_run():
    assert isvc._is_run({"type": "Run"})
    assert isvc._is_run({"type": "TrailRun"})
    assert isvc._is_run({"type": "VirtualRun"})
    assert not isvc._is_run({"type": "Ride"})
    assert not isvc._is_run({"type": None})
    assert not isvc._is_run({})


def test_normalize_activity_maps_intervals_shape():
    raw = {
        "id": 174726705,
        "type": "Run",
        "name": "Easy",
        "start_date_local": "2026-08-10T06:12:43",
        "distance": 4644.18,
        "moving_time": 2360,
        "average_heartrate": 106,
        "average_speed": 1.935,
        "icu_hr_zone_times": [2361, 0, 0, 0, 0, 0, 0],
    }
    n = isvc._normalize_activity(raw)
    assert n["id"] == "174726705"
    assert n["startTimeLocal"] == "2026-08-10T06:12:43"
    assert n["distance"] == 4644.18
    assert n["moving_time"] == 2360
    assert n["average_heartrate"] == 106
    assert n["average_speed"] == 1.935
    assert n["hr_zone_times"] == [2361, 0, 0, 0, 0, 0, 0]


def test_normalize_activity_falls_back_to_elapsed_time():
    n = isvc._normalize_activity({"id": 1, "elapsed_time": 900})
    assert n["moving_time"] == 900


# ── DB-backed sync ────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_plan(db, *, connected=True):
    user = User(username="tester", hashed_password="x", is_active=True, max_hr=185)
    db.add(user)
    db.flush()
    if connected:
        db.add(IntervalsSession(
            user_id=user.id,
            api_key_enc=isvc.encrypt_str("secret-key"),
            athlete_id="i999",
            athlete_name="Tester",
        ))
    plan = TrainingPlan(user_id=user.id, plan_type="race", goal_distance=42.2)
    db.add(plan)
    db.flush()
    w1 = PlannedWorkout(plan_id=plan.id, week_number=1, scheduled_date=date(2026, 3, 17),
                        workout_type="easy", target_distance_km=8.0, completed=False)
    w2 = PlannedWorkout(plan_id=plan.id, week_number=1, scheduled_date=date(2026, 3, 19),
                        workout_type="easy", target_distance_km=7.0, completed=False)
    rest = PlannedWorkout(plan_id=plan.id, week_number=1, scheduled_date=date(2026, 3, 18),
                          workout_type="rest", completed=False)
    db.add_all([w1, w2, rest])
    db.commit()
    return user, plan, w1, w2


def _raw(id_, day, dist_m, dtype="Run"):
    return {
        "id": id_,
        "type": dtype,
        "name": f"act-{id_}",
        "start_date_local": f"2026-03-{day:02d}T06:00:00",
        "distance": dist_m,
        "moving_time": 2400,
        "average_heartrate": 138,
        "average_speed": 2.9,
        "icu_hr_zone_times": [100, 2000, 300, 0, 0, 0, 0],
    }


def test_sync_matches_activities_and_marks_completed(db, monkeypatch):
    user, plan, w1, w2 = _seed_plan(db)
    fake = [_raw("i1", 17, 8000), _raw("i2", 19, 7000), _raw("i9", 18, 5000, dtype="Ride")]
    monkeypatch.setattr(isvc, "fetch_activities_in_range", lambda *a, **k: fake)
    # Avoid hitting the Claude API during rescore
    import services.strava as ssvc
    monkeypatch.setattr(ssvc, "rescore_plan_activities", lambda *a, **k: 0)

    result = isvc.sync_plan_activities(plan.id, user.id, db)

    # The Ride is filtered out; both runs match their same-day workouts
    assert result["total"] == 2
    assert result["synced"] == 2
    assert result["skipped"] == 0

    rows = db.query(WorkoutActivity).filter_by(plan_id=plan.id).all()
    assert {r.strava_activity_id for r in rows} == {"i1", "i2"}
    matched = {r.strava_activity_id: r.workout_id for r in rows}
    assert matched["i1"] == w1.id
    assert matched["i2"] == w2.id

    db.refresh(w1); db.refresh(w2)
    assert w1.completed and w2.completed

    r1 = next(r for r in rows if r.strava_activity_id == "i1")
    assert r1.actual_distance_km == 8.0
    assert r1.streams_data == {"hr_zones": [4, 83, 12, 0, 0]}


def test_sync_excludes_ignored_activities(db, monkeypatch):
    user, plan, w1, w2 = _seed_plan(db)
    db.add(IgnoredActivity(plan_id=plan.id, user_id=user.id, activity_id="i1"))
    db.commit()
    monkeypatch.setattr(isvc, "fetch_activities_in_range",
                        lambda *a, **k: [_raw("i1", 17, 8000), _raw("i2", 19, 7000)])
    import services.strava as ssvc
    monkeypatch.setattr(ssvc, "rescore_plan_activities", lambda *a, **k: 0)

    result = isvc.sync_plan_activities(plan.id, user.id, db)

    assert result["total"] == 1
    ids = {r.strava_activity_id for r in db.query(WorkoutActivity).filter_by(plan_id=plan.id)}
    assert ids == {"i2"}


def test_sync_unmatched_activity_is_stored_but_not_completed(db, monkeypatch):
    user, plan, w1, w2 = _seed_plan(db)
    # Activity far outside the 2-day match window of any workout
    monkeypatch.setattr(isvc, "fetch_activities_in_range",
                        lambda *a, **k: [_raw("i5", 30, 6000)])
    import services.strava as ssvc
    monkeypatch.setattr(ssvc, "rescore_plan_activities", lambda *a, **k: 0)

    result = isvc.sync_plan_activities(plan.id, user.id, db)

    assert result["total"] == 1
    assert result["synced"] == 0
    assert result["skipped"] == 1
    row = db.query(WorkoutActivity).filter_by(plan_id=plan.id).one()
    assert row.workout_id is None


def test_sync_raises_when_not_connected(db):
    user, plan, _, _ = _seed_plan(db, connected=False)
    with pytest.raises(ValueError, match="not connected"):
        isvc.sync_plan_activities(plan.id, user.id, db)
