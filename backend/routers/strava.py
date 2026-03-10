from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(prefix="/strava", tags=["strava"])


@router.get("/auth")
def strava_auth():
    # TODO: redirect to Strava OAuth
    return {"message": "not implemented"}


@router.get("/callback")
def strava_callback(code: str, db: Session = Depends(get_db)):
    # TODO: exchange code for token, save to DB
    return {"message": "not implemented"}


@router.get("/status")
def strava_status(db: Session = Depends(get_db)):
    # TODO: return whether Strava is connected and athlete info
    return {"connected": False}


@router.post("/sync")
def sync_activities(db: Session = Depends(get_db)):
    # TODO: pull recent runs from Strava, match to planned workouts
    return {"message": "not implemented"}
