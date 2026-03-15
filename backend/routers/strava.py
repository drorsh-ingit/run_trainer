from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models.models import StravaToken
from schemas import StravaStatusOut
from services.auth import get_current_user
from services.strava import get_auth_url, exchange_code, sync_plan_activities
from models.models import User
from config import settings

router = APIRouter(prefix="/strava", tags=["strava"])

REDIRECT_URI_TEMPLATE = "{frontend_url}/strava/callback"


@router.get("/auth-url")
def strava_auth_url(current_user: User = Depends(get_current_user)):
    """Return the Strava OAuth URL for the frontend to redirect to."""
    redirect_uri = REDIRECT_URI_TEMPLATE.format(frontend_url=settings.frontend_url)
    return {"url": get_auth_url(redirect_uri)}


@router.post("/exchange")
def strava_exchange(
    code: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Exchange OAuth code for Strava token and persist it."""
    redirect_uri = REDIRECT_URI_TEMPLATE.format(frontend_url=settings.frontend_url)
    try:
        token = exchange_code(code, current_user.id, db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Strava exchange failed: {e}")
    return StravaStatusOut(
        connected=True,
        athlete_name=token.athlete_name,
        athlete_id=token.athlete_id,
    )


@router.get("/status", response_model=StravaStatusOut)
def strava_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    token = db.query(StravaToken).filter(StravaToken.user_id == current_user.id).first()
    if not token:
        return StravaStatusOut(connected=False)
    return StravaStatusOut(
        connected=True,
        athlete_name=token.athlete_name,
        athlete_id=token.athlete_id,
    )


@router.delete("/disconnect", status_code=204)
def strava_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    token = db.query(StravaToken).filter(StravaToken.user_id == current_user.id).first()
    if token:
        db.delete(token)
        db.commit()


@router.post("/sync/{plan_id}")
def sync_strava(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch recent Strava activities and match them to the plan's workouts."""
    try:
        result = sync_plan_activities(plan_id, current_user.id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Strava sync failed: {e}")
    return result
