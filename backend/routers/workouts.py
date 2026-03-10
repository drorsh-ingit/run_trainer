from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(prefix="/workouts", tags=["workouts"])


@router.get("/")
def list_workouts(db: Session = Depends(get_db)):
    # TODO: return workouts, optionally filtered by week/date
    return []


@router.post("/{workout_id}/feedback")
def add_feedback(workout_id: int, db: Session = Depends(get_db)):
    # TODO: save user feedback for a workout
    return {"message": "not implemented"}
