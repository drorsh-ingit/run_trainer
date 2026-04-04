from __future__ import annotations
from datetime import date
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


DEFAULT_MODEL = "claude-sonnet-4-6"
ALLOWED_MODELS = {
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-3.5-turbo",
}


# ---- Request ----

class PlanCreateRequest(BaseModel):
    plan_type: Literal["race", "general"] = Field("race", description="'race' = goal-race plan; 'general' = open-ended fitness plan")
    goal_distance_km: Optional[float] = Field(None, gt=0, description="Race distance in km — required for race plans")
    goal_date: Optional[date] = Field(None, description="Target race date — required for race plans")
    plan_duration_weeks: Optional[int] = Field(None, ge=4, le=52, description="Plan duration in weeks — required for general plans")
    schedule_description: str = Field(..., description='Describe your weekly schedule in plain English')
    current_weekly_km: float = Field(..., ge=0, description="Current weekly running volume in km")
    fitness_level: str = Field(..., description="beginner | intermediate | advanced")
    injuries: str = Field("", description="Known injuries or physical limitations")
    gradualness_preference: str = Field("moderate", description="conservative | moderate | aggressive")
    goal_time: Optional[str] = Field(None, description="Target finish time — race plans only")
    additional_notes: str = Field("", description="Any other context for plan generation")
    runner_name: str = Field("")
    goal_race_name: str = Field("")
    gender: str = Field("")
    age: Optional[int] = Field(None)
    height_cm: Optional[float] = Field(None)
    weight_kg: Optional[float] = Field(None)
    ai_model: str = Field(DEFAULT_MODEL)

    @model_validator(mode="after")
    def check_plan_type_fields(self) -> "PlanCreateRequest":
        if self.plan_type == "race":
            if self.goal_distance_km is None:
                raise ValueError("goal_distance_km is required for race plans")
            if self.goal_date is None:
                raise ValueError("goal_date is required for race plans")
            if self.goal_date <= date.today():
                raise ValueError("goal_date must be in the future")
        else:
            if self.plan_duration_weeks is None:
                raise ValueError("plan_duration_weeks is required for general plans")
        return self

    @field_validator("ai_model")
    @classmethod
    def validate_ai_model(cls, v: str) -> str:
        if v not in ALLOWED_MODELS:
            raise ValueError(f"ai_model must be one of {ALLOWED_MODELS}")
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


# ---- Auth ----

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)


class UserOut(BaseModel):
    id: int
    username: str
    is_active: bool
    max_hr: Optional[int] = None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    max_hr: Optional[int] = Field(None, ge=100, le=250)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- Adjust (saved plan) ----

class AdjustRequest(BaseModel):
    comment: str = Field(..., min_length=1)


# ---- Conversational chat ----

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class PlanChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: List[ChatMessage] = []
    ai_model: Optional[str] = Field(None, description="Override model; defaults to plan's stored model")


class CoachChatRequest(PlanCreateRequest):
    """For coaching Q&A phase and final build step."""
    message: str = Field("", description="User's reply (empty string for the initial start call)")
    history: List[ChatMessage] = []


# ---- Plan assessment (planned vs actual) ----

class AssessStartRequest(BaseModel):
    ai_model: Optional[str] = Field(None, description="Override model; defaults to plan's stored model")

class AssessReplyRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: List[ChatMessage] = []
    ai_model: Optional[str] = Field(None, description="Override model; defaults to plan's stored model")

class AssessApplyRequest(BaseModel):
    revised_plan_data: dict


# ---- Preview revision (unsaved) ----

class PlanReviseRequest(BaseModel):
    """Revise an unsaved preview plan. Returns updated plan JSON without persisting."""
    current_plan: dict
    comment: str = Field(..., min_length=1)
    goal_distance_km: Optional[float] = None
    goal_date: Optional[date] = None
    schedule_description: str
    injuries: str = ""
    additional_notes: str = ""


class PreviewChatRequest(BaseModel):
    """Conversational revision of an unsaved preview plan."""
    current_plan: dict
    message: str = Field(..., min_length=1)
    history: List[ChatMessage] = []
    goal_distance_km: Optional[float] = None
    goal_date: Optional[date] = None
    plan_duration_weeks: Optional[int] = None
    plan_type: Literal["race", "general"] = "race"
    schedule_description: str
    injuries: str = ""
    additional_notes: str = ""
    ai_model: str = Field(DEFAULT_MODEL)


class SavePreviewRequest(PlanCreateRequest):
    """Save a pre-approved preview plan without calling Claude again."""
    generated_plan: dict


# ---- Claude service internal types ----

class WorkoutStep(BaseModel):
    step_type: str          # warmup | active | rest | cooldown
    duration_type: str      # TIME | DISTANCE
    duration_value: int     # seconds (TIME) or meters (DISTANCE)
    target_type: str        # HEART_RATE_ZONE | PACE | OPEN
    target_low: Optional[int] = None
    target_high: Optional[int] = None


class WorkoutSchema(BaseModel):
    day_of_week: str
    type: str  # easy | tempo | long_run | intervals | fartlek | hill_repeats | strides | cross_training | rest | race
    description: str
    distance_km: Optional[float] = None
    duration_minutes: Optional[int] = None
    is_optional: bool = False
    steps: List[WorkoutStep] = []


class WeekSchema(BaseModel):
    week_number: int
    theme: str
    total_km: float
    workouts: List[WorkoutSchema]


class ClaudePlanResponse(BaseModel):
    summary: str
    total_weeks: int
    weeks: List[WeekSchema]


# ---- Strava ----

class StravaStatusOut(BaseModel):
    connected: bool
    athlete_name: Optional[str] = None
    athlete_id: Optional[str] = None


class ActivityOut(BaseModel):
    strava_activity_id: str
    name: Optional[str] = None
    actual_distance_km: Optional[float] = None
    actual_duration_sec: Optional[int] = None
    average_hr: Optional[float] = None
    average_pace_min_per_km: Optional[float] = None
    hr_zones: Optional[List[int]] = None
    has_streams: bool = False
    match_score: Optional[int] = None
    match_comment: Optional[str] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_activity(cls, obj: object) -> "ActivityOut":
        pace = None
        avg_speed = getattr(obj, "average_speed_ms", None)
        if avg_speed and avg_speed > 0:
            pace = round(1000 / (avg_speed * 60), 2)
        hr_zones = None
        streams = getattr(obj, "streams_data", None)
        if streams:
            if "hr_zones" in streams:
                hr_zones = streams["hr_zones"]
            elif "heartrate" in streams:
                from services.garmin import compute_hr_zones
                hr_zones = compute_hr_zones(streams["heartrate"])
        return cls(
            strava_activity_id=obj.strava_activity_id,
            name=getattr(obj, "name", None),
            actual_distance_km=getattr(obj, "actual_distance_km", None),
            actual_duration_sec=getattr(obj, "actual_duration_sec", None),
            average_hr=getattr(obj, "average_hr", None),
            average_pace_min_per_km=pace,
            hr_zones=hr_zones,
            has_streams=bool(streams),
            match_score=getattr(obj, "match_score", None),
            match_comment=getattr(obj, "match_comment", None),
        )


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
    strava_activity_id: Optional[str] = None
    steps: List[dict] = []
    activity: Optional["ActivityOut"] = None

    @field_validator("steps", mode="before")
    @classmethod
    def coerce_steps(cls, v: object) -> list:
        return v if v is not None else []

    @field_validator("activity", mode="before")
    @classmethod
    def coerce_activity(cls, v: object) -> Optional["ActivityOut"]:
        if v is None:
            return None
        if isinstance(v, dict):
            return ActivityOut(**v)
        # ORM object — compute derived fields
        return ActivityOut.from_orm_activity(v)

    model_config = {"from_attributes": True}


class PlanOut(BaseModel):
    id: int
    goal_distance: Optional[float]
    goal_date: Optional[date]
    plan_type: Optional[str] = "race"
    plan_duration_weeks: Optional[int] = None
    schedule_description: str
    injuries: str
    additional_notes: str
    plan_data: dict
    workouts: List[WorkoutOut]
    ai_model: Optional[str] = None

    model_config = {"from_attributes": True}


class PlanSummaryOut(BaseModel):
    id: int
    goal_distance: Optional[float]
    goal_date: Optional[date]
    created_at: str
    summary: str

    model_config = {"from_attributes": True}


class AdminPlanOut(BaseModel):
    id: int
    username: str
    goal_distance: Optional[float]
    goal_date: Optional[date]
    plan_type: Optional[str]
    plan_duration_weeks: Optional[int]
    created_at: str

    model_config = {"from_attributes": True}
