from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    max_hr = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    plans = relationship("TrainingPlan", back_populates="user")


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    plan_type = Column(String, default="race")          # "race" | "general"
    goal_distance = Column(Float, nullable=True)        # in km; null for general plans
    goal_date = Column(Date, nullable=True)             # null for general plans
    plan_duration_weeks = Column(Integer, nullable=True)  # set for general plans
    schedule_description = Column(Text, default="")
    injuries = Column(Text, default="")
    additional_notes = Column(Text, default="")
    plan_data = Column(JSON)                # full plan from Claude
    ai_model = Column(String, nullable=True, default="claude-sonnet-4-6")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="plans")
    workouts = relationship("PlannedWorkout", back_populates="plan", cascade="all, delete-orphan")


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
    steps = Column(JSON, nullable=True)

    plan = relationship("TrainingPlan", back_populates="workouts")
    feedback = relationship("WorkoutFeedback", back_populates="workout", uselist=False)
    activity = relationship("WorkoutActivity", back_populates="workout", uselist=False)


class WorkoutFeedback(Base):
    __tablename__ = "workout_feedback"

    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, ForeignKey("planned_workouts.id"))
    perceived_effort = Column(Integer)      # 1-10
    feeling = Column(String)               # great, good, ok, tired, bad
    notes = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())

    workout = relationship("PlannedWorkout", back_populates="feedback")


class WorkoutActivity(Base):
    __tablename__ = "workout_activities"

    id                 = Column(Integer, primary_key=True)
    plan_id            = Column(Integer, ForeignKey("training_plans.id"), nullable=True, index=True)
    workout_id         = Column(Integer, ForeignKey("planned_workouts.id"), unique=True, nullable=True)
    strava_activity_id = Column(String, nullable=False, index=True)
    name               = Column(String, nullable=True)
    actual_distance_km = Column(Float, nullable=True)
    actual_duration_sec = Column(Integer, nullable=True)
    average_hr         = Column(Float, nullable=True)
    average_speed_ms   = Column(Float, nullable=True)
    start_date         = Column(DateTime, nullable=True)
    streams_data       = Column(JSON, nullable=True)
    synced_at          = Column(DateTime, server_default=func.now())

    workout = relationship("PlannedWorkout", back_populates="activity")


class StravaToken(Base):
    __tablename__ = "strava_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=True)
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(Integer)
    athlete_id = Column(String)
    athlete_name = Column(String)


class GarminSession(Base):
    __tablename__ = "garmin_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    token_dump_enc = Column(Text, nullable=False)
    garmin_display_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User")
