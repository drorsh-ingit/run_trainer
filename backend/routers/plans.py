from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.models import TrainingPlan, PlannedWorkout
from schemas import PlanCreateRequest, PlanOut
from services.claude import generate_plan

router = APIRouter(prefix="/plans", tags=["plans"])

DAY_OFFSETS = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def _compute_scheduled_date(week_1_start: date, week_number: int, day_of_week: str) -> date:
    week_start = week_1_start + timedelta(weeks=week_number - 1)
    return week_start + timedelta(days=DAY_OFFSETS.get(day_of_week, 0))


@router.get("/", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)):
    return db.query(TrainingPlan).order_by(TrainingPlan.created_at.desc()).all()


@router.post("/", response_model=PlanOut, status_code=201)
def create_plan(req: PlanCreateRequest, db: Session = Depends(get_db)):
    try:
        claude_plan = generate_plan(req)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Plan generation failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    plan = TrainingPlan(
        goal_distance=req.goal_distance_km,
        goal_date=req.goal_date,
        runs_per_week=req.runs_per_week,
        optional_runs_per_week=req.optional_runs_per_week,
        session_duration_minutes=req.typical_session_duration_minutes,
        injuries=req.injuries,
        additional_notes=req.additional_notes,
        plan_data=claude_plan.model_dump(),
    )
    db.add(plan)
    db.flush()

    # Anchor week 1 to the next Monday (or today if today is Monday)
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    week_1_start = today if today.weekday() == 0 else today + timedelta(days=days_until_monday)

    for week in claude_plan.weeks:
        for workout in week.workouts:
            if workout.type == "race":
                scheduled = req.goal_date
            else:
                scheduled = _compute_scheduled_date(week_1_start, week.week_number, workout.day_of_week)

            db.add(PlannedWorkout(
                plan_id=plan.id,
                week_number=week.week_number,
                day_of_week=workout.day_of_week,
                scheduled_date=scheduled,
                workout_type=workout.type,
                description=workout.description,
                target_distance_km=workout.distance_km,
                target_duration_minutes=workout.duration_minutes,
                is_optional=workout.is_optional,
            ))

    db.commit()
    db.refresh(plan)
    return plan


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.post("/{plan_id}/adjust")
def adjust_plan(plan_id: int, db: Session = Depends(get_db)):
    return {"message": "not implemented"}
