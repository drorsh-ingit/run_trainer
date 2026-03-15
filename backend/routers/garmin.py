import json
from datetime import datetime
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.models import GarminSession, PlannedWorkout, TrainingPlan, User, WorkoutActivity
from services.auth import get_current_user
from services.claude import generate_steps_for_workouts
from services.garmin import (
    authenticate_garmin,
    build_garmin_workout_payload,
    build_garth_client,
    decrypt_str,
    delete_garmin_workouts_for_dates,
    encrypt_str,
    push_workout_to_garmin,
    sync_plan_activities,
)

router = APIRouter(prefix="/garmin", tags=["garmin"])
plans_router = APIRouter(prefix="/plans", tags=["garmin"])


# ── Credentials schema ───────────────────────────────────────────────────────

class GarminAuthRequest(BaseModel):
    username: str
    password: str


# ── /garmin/status ───────────────────────────────────────────────────────────

@router.get("/status")
def garmin_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.query(GarminSession).filter(GarminSession.user_id == current_user.id).first()
    if not session:
        return {"connected": False}
    return {"connected": True, "display_name": session.garmin_display_name}


# ── /garmin/auth ─────────────────────────────────────────────────────────────

@router.post("/auth")
def garmin_auth(
    body: GarminAuthRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        token_dump, display_name = authenticate_garmin(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    session = db.query(GarminSession).filter(GarminSession.user_id == current_user.id).first()
    if session:
        session.token_dump_enc = encrypt_str(token_dump)
        session.garmin_display_name = display_name
    else:
        session = GarminSession(
            user_id=current_user.id,
            token_dump_enc=encrypt_str(token_dump),
            garmin_display_name=display_name,
        )
        db.add(session)
    db.commit()
    return {"connected": True, "display_name": display_name}


# ── /garmin/auth DELETE (disconnect) ────────────────────────────────────────

@router.delete("/auth", status_code=204)
def garmin_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(GarminSession).filter(GarminSession.user_id == current_user.id).delete()
    db.commit()


# ── Shared helpers ───────────────────────────────────────────────────────────

def _load_workouts(db: Session, plan_id: int, month: str | None):
    query = (
        db.query(PlannedWorkout)
        .filter(PlannedWorkout.plan_id == plan_id, PlannedWorkout.workout_type != "rest")
    )
    if month:
        try:
            month_date = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(400, "month must be in YYYY-MM format")
        query = query.filter(
            PlannedWorkout.scheduled_date >= month_date.date().replace(day=1),
            PlannedWorkout.scheduled_date < (
                month_date.replace(month=month_date.month % 12 + 1, day=1)
                if month_date.month < 12
                else month_date.replace(year=month_date.year + 1, month=1, day=1)
            ).date(),
        )
    return query.order_by(PlannedWorkout.week_number, PlannedWorkout.scheduled_date).all()


def _ensure_steps(workouts: list, db: Session):
    """Generate and persist steps for any workouts that are missing them."""
    missing = [w for w in workouts if not w.steps and w.workout_type not in ("rest", "cross_training")]
    if not missing:
        return
    BATCH = 20
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i + BATCH]
        batch_input = [{"id": w.id, "type": w.workout_type, "description": w.description,
                         "duration_minutes": w.target_duration_minutes, "distance_km": w.target_distance_km}
                        for w in batch]
        try:
            steps_map = generate_steps_for_workouts(batch_input)
            for w in batch:
                w.steps = steps_map.get(w.id, [])
        except Exception:
            pass
    db.commit()


# ── /plans/{plan_id}/garmin-push ─────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@plans_router.post("/{plan_id}/garmin-push")
def garmin_push(
    plan_id: int,
    month: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    def stream() -> Generator[str, None, None]:
        # 1. Garmin session
        garmin_session = db.query(GarminSession).filter(GarminSession.user_id == current_user.id).first()
        if not garmin_session:
            yield _sse({"type": "error", "message": "Garmin not connected"})
            return

        # 2. Plan auth
        plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
        if not plan or plan.user_id != current_user.id:
            yield _sse({"type": "error", "message": "Plan not found or not authorized"})
            return

        # 3. Load workouts
        workouts = _load_workouts(db, plan_id, month)
        total = len(workouts)
        yield _sse({"type": "status", "message": f"Preparing {total} workout(s)…"})

        # 4. Generate missing steps (may call Claude)
        missing = [w for w in workouts if not w.steps and w.workout_type not in ("rest", "cross_training")]
        if missing:
            yield _sse({"type": "status", "message": f"Generating steps for {len(missing)} workout(s) via AI…"})
            _ensure_steps(workouts, db)

        # 5. Build garth client
        try:
            client = build_garth_client(decrypt_str(garmin_session.token_dump_enc))
        except Exception as e:
            yield _sse({"type": "error", "message": f"Failed to restore Garmin session: {e}"})
            return

        # 6. Remove any previously pushed workouts for the same dates
        dates_to_push = {str(w.scheduled_date) for w in workouts}
        yield _sse({"type": "status", "message": "Removing previously pushed workouts…"})
        delete_garmin_workouts_for_dates(client, dates_to_push)

        # 7. Push each workout
        pushed, skipped, errors = [], [], []
        for i, w in enumerate(workouts, 1):
            name = f"{w.workout_type.replace('_', ' ').title()} ({w.scheduled_date})"
            if not w.steps:
                skipped.append(w.id)
                yield _sse({"type": "progress", "current": i, "total": total, "message": f"Skipped {name} (no steps)"})
                continue
            yield _sse({"type": "progress", "current": i, "total": total, "message": f"Pushing {name}…"})
            try:
                garmin_id = push_workout_to_garmin(client, build_garmin_workout_payload(w), w.scheduled_date)
                pushed.append({"workout_id": w.id, "garmin_workout_id": garmin_id})
            except Exception as e:
                errors.append({"workout_id": w.id, "error": str(e)})
                yield _sse({"type": "progress", "current": i, "total": total, "message": f"Failed: {name}"})

        # 7. Persist refreshed tokens
        try:
            garmin_session.token_dump_enc = encrypt_str(client.dumps())
            db.commit()
        except Exception:
            pass

        err_note = f" ({len(errors)} failed)" if errors else ""
        yield _sse({"type": "done", "pushed": len(pushed), "skipped": len(skipped), "errors": errors,
                    "message": f"Done — pushed {len(pushed)} workout(s){err_note}"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── /garmin/debug-activities ─────────────────────────────────────────────────

@router.get("/debug-activities")
def debug_activities(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return raw Garmin activity list for debugging."""
    from datetime import date, timedelta
    from services.garmin import build_garminconnect_client, decrypt_str, fetch_activities_in_range
    garmin_session = db.query(GarminSession).filter(GarminSession.user_id == current_user.id).first()
    if not garmin_session:
        raise HTTPException(400, "Garmin not connected")
    api = build_garminconnect_client(decrypt_str(garmin_session.token_dump_enc))
    today = date.today()
    try:
        activities = fetch_activities_in_range(api, today - timedelta(days=30), today + timedelta(days=1))
        return {"count": len(activities), "sample": activities[:3]}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Garmin API error: {e}")


# ── /plans/{plan_id}/activities ──────────────────────────────────────────────

@plans_router.get("/{plan_id}/activities")
def plan_activities(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all synced activities for a plan, keyed by their actual date."""
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found or not authorized")

    rows = (
        db.query(WorkoutActivity)
        .filter(WorkoutActivity.plan_id == plan_id)
        .all()
    )

    result = []
    for r in rows:
        if not r.start_date:
            continue
        pace = None
        if r.average_speed_ms and r.average_speed_ms > 0:
            pace = round(1000 / (r.average_speed_ms * 60), 2)
        hr_zones = None
        if r.streams_data:
            if "hr_zones" in r.streams_data:
                hr_zones = r.streams_data["hr_zones"]
        result.append({
            "date": r.start_date.strftime("%Y-%m-%d"),
            "name": r.name,
            "actual_distance_km": r.actual_distance_km,
            "actual_duration_sec": r.actual_duration_sec,
            "average_hr": r.average_hr,
            "average_pace_min_per_km": pace,
            "hr_zones": hr_zones,
        })
    return result


# ── /plans/{plan_id}/garmin-sync ──────────────────────────────────────────────

@plans_router.post("/{plan_id}/garmin-sync")
def garmin_sync(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pull completed Garmin activities and match them to this plan's workouts."""
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found or not authorized")
    try:
        result = sync_plan_activities(plan_id, current_user.id, db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Sync failed: {e}")
    return result
