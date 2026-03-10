from __future__ import annotations
from datetime import date
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


# ---- Request ----

class PlanCreateRequest(BaseModel):
    goal_distance_km: float = Field(..., gt=0, description="Race distance in km, e.g. 42.195 for marathon")
    goal_date: date = Field(..., description="Target race date")
    runs_per_week: int = Field(..., ge=1, le=14, description="Number of mandatory runs per week")
    optional_runs_per_week: int = Field(0, ge=0, le=7, description="Additional optional runs per week")
    typical_session_duration_minutes: int = Field(..., ge=15, le=360, description="Typical available time per session")
    current_weekly_km: float = Field(..., ge=0, description="Current weekly running volume in km")
    fitness_level: str = Field(..., description="beginner | intermediate | advanced")
    injuries: str = Field("", description="Known injuries or physical limitations")
    gradualness_preference: str = Field("moderate", description="conservative | moderate | aggressive")
    additional_notes: str = Field("", description="Any other context for plan generation")

    @field_validator("goal_date")
    @classmethod
    def goal_date_must_be_future(cls, v: date) -> date:
        if v <= date.today():
            raise ValueError("goal_date must be in the future")
        return v

    @field_validator("fitness_level")
    @classmethod
    def validate_fitness_level(cls, v: str) -> str:
        allowed = {"beginner", "intermediate", "advanced"}
        if v.lower() not in allowed:
            raise ValueError(f"fitness_level must be one of {allowed}")
        return v.lower()

    @field_validator("gradualness_preference")
    @classmethod
    def validate_gradualness(cls, v: str) -> str:
        allowed = {"conservative", "moderate", "aggressive"}
        if v.lower() not in allowed:
            raise ValueError(f"gradualness_preference must be one of {allowed}")
        return v.lower()


# ---- Claude service internal types ----

class WorkoutSchema(BaseModel):
    day_of_week: str
    type: str  # easy | tempo | long_run | intervals | hill_repeats | strides | cross_training | rest | race
    description: str
    distance_km: Optional[float] = None
    duration_minutes: Optional[int] = None
    is_optional: bool = False


class WeekSchema(BaseModel):
    week_number: int
    theme: str
    total_km: float
    workouts: List[WorkoutSchema]


class ClaudePlanResponse(BaseModel):
    summary: str
    total_weeks: int
    weeks: List[WeekSchema]


# ---- API Response ----

class WorkoutOut(BaseModel):
    id: int
    week_number: int
    day_of_week: str
    scheduled_date: date
    workout_type: str
    description: str
    target_distance_km: Optional[float]
    target_duration_minutes: Optional[int]
    is_optional: bool
    completed: bool

    model_config = {"from_attributes": True}


class PlanOut(BaseModel):
    id: int
    goal_distance: float
    goal_date: date
    runs_per_week: int
    optional_runs_per_week: int
    session_duration_minutes: int
    injuries: str
    additional_notes: str
    plan_data: dict
    workouts: List[WorkoutOut]

    model_config = {"from_attributes": True}
