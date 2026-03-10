from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id = Column(Integer, primary_key=True)
    goal_distance = Column(Float)           # in km
    goal_date = Column(Date)
    runs_per_week = Column(Integer)
    optional_runs_per_week = Column(Integer, default=0)
    session_duration_minutes = Column(Integer)
    injuries = Column(Text, default="")
    additional_notes = Column(Text, default="")
    plan_data = Column(JSON)                # full plan from Claude
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)

    workouts = relationship("PlannedWorkout", back_populates="plan")


class PlannedWorkout(Base):
    __tablename__ = "planned_workouts"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("training_plans.id"))
    week_number = Column(Integer)
    day_of_week = Column(String)            # Monday, Tuesday, etc.
    scheduled_date = Column(Date)
    workout_type = Column(String)           # easy, tempo, long run, intervals, rest
    description = Column(Text)
    target_distance_km = Column(Float)
    target_duration_minutes = Column(Integer)
    is_optional = Column(Boolean, default=False)
    completed = Column(Boolean, default=False)
    strava_activity_id = Column(String, nullable=True)

    plan = relationship("TrainingPlan", back_populates="workouts")
    feedback = relationship("WorkoutFeedback", back_populates="workout", uselist=False)


class WorkoutFeedback(Base):
    __tablename__ = "workout_feedback"

    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, ForeignKey("planned_workouts.id"))
    perceived_effort = Column(Integer)      # 1-10
    feeling = Column(String)               # great, good, ok, tired, bad
    notes = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())

    workout = relationship("PlannedWorkout", back_populates="feedback")


class StravaToken(Base):
    __tablename__ = "strava_tokens"

    id = Column(Integer, primary_key=True)
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(Integer)
    athlete_id = Column(String)
    athlete_name = Column(String)
