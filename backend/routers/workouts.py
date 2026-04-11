from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.models import PlannedWorkout, TrainingPlan, User
from services.auth import get_current_user

router = APIRouter(prefix="/workouts", tags=["workouts"])


@router.get("/")
def list_workouts(db: Session = Depends(get_db)):
    # TODO: return workouts, optionally filtered by week/date
    return []


@router.post("/{workout_id}/feedback")
def add_feedback(workout_id: int, db: Session = Depends(get_db)):
    # TODO: save user feedback for a workout
    return {"message": "not implemented"}


@router.patch("/{workout_id}/toggle-completed")
def toggle_completed(
    workout_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    workout = db.query(PlannedWorkout).filter(PlannedWorkout.id == workout_id).first()
    if not workout:
        raise HTTPException(404)
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == workout.plan_id).first()
    if not plan or (plan.user_id != current_user.id and current_user.username != "admin"):
        raise HTTPException(403)
    workout.completed = not workout.completed
    db.commit()
    return {"id": workout.id, "completed": workout.completed}
