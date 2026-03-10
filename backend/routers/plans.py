from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/")
def list_plans(db: Session = Depends(get_db)):
    # TODO: return all training plans
    return []


@router.post("/")
def create_plan(db: Session = Depends(get_db)):
    # TODO: accept plan input, call Claude to generate plan, save to DB
    return {"message": "not implemented"}


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    # TODO: return plan with workouts
    return {"message": "not implemented"}


@router.post("/{plan_id}/adjust")
def adjust_plan(plan_id: int, db: Session = Depends(get_db)):
    # TODO: call Claude to adjust plan based on feedback + actual runs
    return {"message": "not implemented"}
