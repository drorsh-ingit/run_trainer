"""Regression tests for replacing a plan's workouts without breaking FK links.

Production bug (Postgres): adjusting a plan ran
    DELETE FROM planned_workouts WHERE plan_id = ...
but workout_activities.workout_id and workout_feedback.workout_id reference
planned_workouts.id with no ON DELETE, so the delete raised ForeignKeyViolation
("Key (id)=(700) is still referenced from table workout_activities"). It slipped
through local SQLite because SQLite doesn't enforce foreign keys by default.

These tests run SQLite WITH foreign_keys=ON (mirroring Postgres) and check that
_replace_plan_workouts rewrites the workouts, keeps every synced activity and
feedback row, and re-links them to the new workout on the same date.
"""
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Base
from models.models import TrainingPlan, PlannedWorkout, WorkoutActivity, WorkoutFeedback, User
from schemas import ClaudePlanResponse, WeekSchema, WorkoutSchema
from routers.plans import _replace_plan_workouts, _compute_scheduled_date, _save_workouts


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # Enforce FKs so this SQLite behaves like production Postgres.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _next_monday():
    today = date.today()
    return today if today.weekday() == 0 else today + timedelta(days=(7 - today.weekday()) % 7)


def _revised_plan():
    # Two Monday workouts (weeks 1 and 2) so dates are predictable via _compute_scheduled_date.
    return ClaudePlanResponse(
        summary="revised", total_weeks=2,
        weeks=[
            WeekSchema(week_number=1, theme="Initial: Base", total_km=5.0, workouts=[
                WorkoutSchema(day_of_week="Monday", type="easy", description="3km easy", distance_km=3.0),
            ]),
            WeekSchema(week_number=2, theme="Initial: Base", total_km=6.0, workouts=[
                WorkoutSchema(day_of_week="Monday", type="easy", description="4km easy", distance_km=4.0),
            ]),
        ],
    )


def _seed_plan_with_links(db):
    user = User(username="u", hashed_password="x")
    db.add(user); db.flush()
    plan = TrainingPlan(user_id=user.id, plan_type="general", plan_duration_weeks=2,
                        schedule_description="mondays", plan_data={"weeks": []})
    db.add(plan); db.flush()

    wk1_monday = _next_monday()
    w1 = PlannedWorkout(plan_id=plan.id, week_number=1, day_of_week="Monday",
                        scheduled_date=wk1_monday, workout_type="easy", description="old wk1")
    w2 = PlannedWorkout(plan_id=plan.id, week_number=2, day_of_week="Monday",
                        scheduled_date=wk1_monday + timedelta(weeks=1), workout_type="easy", description="old wk2")
    db.add_all([w1, w2]); db.flush()

    # An activity actually run on week-1 Monday, matched to w1; and feedback on w1.
    act = WorkoutActivity(plan_id=plan.id, workout_id=w1.id, strava_activity_id="700",
                          name="Morning Run", start_date=datetime.combine(wk1_monday, datetime.min.time()))
    fb = WorkoutFeedback(workout_id=w1.id, perceived_effort=5, feeling="good")
    db.add_all([act, fb]); db.flush()
    return plan, act.id, fb.id, wk1_monday


def test_replace_does_not_raise_fk_violation_and_preserves_rows(db):
    plan, act_id, fb_id, _ = _seed_plan_with_links(db)

    _replace_plan_workouts(db, plan, _revised_plan(), goal_date=None)
    db.commit()

    # No exception (the bug), and no synced data was deleted.
    assert db.query(WorkoutActivity).count() == 1
    assert db.query(WorkoutFeedback).count() == 1
    # Old workouts gone, new ones created.
    workouts = db.query(PlannedWorkout).filter(PlannedWorkout.plan_id == plan.id).all()
    assert len(workouts) == 2
    assert {w.description for w in workouts} == {"3km easy", "4km easy"}


def test_activity_relinks_to_new_workout_on_same_date(db):
    plan, act_id, fb_id, wk1_monday = _seed_plan_with_links(db)

    _replace_plan_workouts(db, plan, _revised_plan(), goal_date=None)
    db.commit()

    act = db.get(WorkoutActivity, act_id)
    new_wk1 = (db.query(PlannedWorkout)
               .filter(PlannedWorkout.plan_id == plan.id, PlannedWorkout.scheduled_date == wk1_monday)
               .first())
    assert new_wk1 is not None
    assert act.workout_id == new_wk1.id  # re-linked to the workout now on its run date


def test_feedback_relinks_by_original_workout_date(db):
    plan, act_id, fb_id, wk1_monday = _seed_plan_with_links(db)

    _replace_plan_workouts(db, plan, _revised_plan(), goal_date=None)
    db.commit()

    fb = db.get(WorkoutFeedback, fb_id)
    new_wk1 = (db.query(PlannedWorkout)
               .filter(PlannedWorkout.plan_id == plan.id, PlannedWorkout.scheduled_date == wk1_monday)
               .first())
    assert fb.workout_id == new_wk1.id


def test_activity_left_unmatched_when_no_workout_on_its_date(db):
    plan, act_id, fb_id, wk1_monday = _seed_plan_with_links(db)

    # Move the activity to a date no revised workout falls on.
    act = db.get(WorkoutActivity, act_id)
    act.start_date = datetime.combine(wk1_monday + timedelta(days=1), datetime.min.time())
    db.flush()

    _replace_plan_workouts(db, plan, _revised_plan(), goal_date=None)
    db.commit()

    act = db.get(WorkoutActivity, act_id)
    assert act.workout_id is None       # unmatched, but...
    assert db.query(WorkoutActivity).count() == 1  # ...still present, no data lost
