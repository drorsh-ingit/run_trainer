import json
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.models import IntervalsSession, TrainingPlan, User
from services.auth import get_current_user
from services.garmin import decrypt_str, encrypt_str
from services.intervals import (
    build_intervals_workout_text,
    delete_intervals_events_for_dates,
    push_workout_to_intervals,
    sync_plan_activities,
    validate_intervals_key,
)

router = APIRouter(prefix="/intervals", tags=["intervals"])
plans_router = APIRouter(prefix="/plans", tags=["intervals"])


class IntervalsAuthRequest(BaseModel):
    api_key: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.get("/status")
def intervals_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.query(IntervalsSession).filter(IntervalsSession.user_id == current_user.id).first()
    if not session:
        return {"connected": False}
    return {"connected": True, "athlete_name": session.athlete_name}


@router.post("/auth")
def intervals_auth(
    body: IntervalsAuthRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        athlete_id, athlete_name = validate_intervals_key(body.api_key)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    session = db.query(IntervalsSession).filter(IntervalsSession.user_id == current_user.id).first()
    if session:
        session.api_key_enc = encrypt_str(body.api_key)
        session.athlete_id = athlete_id
        session.athlete_name = athlete_name
    else:
        session = IntervalsSession(
            user_id=current_user.id,
            api_key_enc=encrypt_str(body.api_key),
            athlete_id=athlete_id,
            athlete_name=athlete_name,
        )
        db.add(session)
    db.commit()
    return {"connected": True, "athlete_name": athlete_name}


@router.delete("/auth", status_code=204)
def intervals_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(IntervalsSession).filter(IntervalsSession.user_id == current_user.id).delete()
    db.commit()


@plans_router.post("/{plan_id}/intervals-sync")
def intervals_sync(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pull completed Intervals.icu activities and match them to this plan's workouts."""
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found or not authorized")
    try:
        return sync_plan_activities(plan_id, current_user.id, db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Sync failed: {e}")


@plans_router.post("/{plan_id}/intervals-push")
def intervals_push(
    plan_id: int,
    month: str | None = None,
    regenerate: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from routers.garmin import _load_workouts, _ensure_steps

    def stream() -> Generator[str, None, None]:
        session = db.query(IntervalsSession).filter(IntervalsSession.user_id == current_user.id).first()
        if not session:
            yield _sse({"type": "error", "message": "Intervals.icu not connected"})
            return

        plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
        if not plan or plan.user_id != current_user.id:
            yield _sse({"type": "error", "message": "Plan not found or not authorized"})
            return

        try:
            api_key = decrypt_str(session.api_key_enc)
        except Exception:
            yield _sse({"type": "error", "message": "Intervals.icu session invalid — please reconnect"})
            return

        athlete_id = session.athlete_id or "0"

        workouts = _load_workouts(db, plan_id, month)
        total = len(workouts)
        yield _sse({"type": "status", "message": f"Preparing {total} workout(s)…"})

        if regenerate:
            to_regen = [w for w in workouts if w.workout_type not in ("rest", "cross_training")]
            if to_regen:
                old_steps = {w.id: w.steps for w in to_regen}
                for w in to_regen:
                    w.steps = None
                db.commit()
                yield _sse({"type": "status", "message": f"Regenerating steps for {len(to_regen)} workout(s) via AI…"})
                _ensure_steps(workouts, db)
                failed = [w for w in to_regen if not w.steps]
                if failed:
                    for w in failed:
                        w.steps = old_steps[w.id]
                    db.commit()
                    if all(not w.steps for w in to_regen):
                        yield _sse({"type": "error", "message": "Step generation failed — AI credits may be depleted. Kept existing steps."})
                        return
                    yield _sse({"type": "status", "message": f"Regenerated {len(to_regen) - len(failed)} of {len(to_regen)} — restored {len(failed)} that failed"})
        else:
            missing = [w for w in workouts if not w.steps and w.workout_type not in ("rest", "cross_training")]
            if missing:
                yield _sse({"type": "status", "message": f"Generating steps for {len(missing)} workout(s) via AI…"})
                _ensure_steps(workouts, db)
                still_missing = [w for w in missing if not w.steps]
                if len(still_missing) == len(missing):
                    yield _sse({"type": "error", "message": "Step generation failed — please try again in a moment."})
                    return
                if still_missing:
                    yield _sse({"type": "status", "message": f"Generated {len(missing) - len(still_missing)} of {len(missing)} — {len(still_missing)} could not be generated"})

        dates_to_push = {str(w.scheduled_date) for w in workouts}
        yield _sse({"type": "status", "message": "Removing previously pushed workouts…"})
        delete_intervals_events_for_dates(api_key, athlete_id, dates_to_push)

        pushed, skipped, errors = [], [], []
        for i, w in enumerate(workouts, 1):
            name = f"{w.workout_type.replace('_', ' ').title()} ({w.scheduled_date})"
            if not w.steps:
                skipped.append(w.id)
                yield _sse({"type": "progress", "current": i, "total": total, "message": f"Skipped {name} (no steps)"})
                continue
            yield _sse({"type": "progress", "current": i, "total": total, "message": f"Pushing {name}…"})
            try:
                workout_text = build_intervals_workout_text(w)
                event_id = push_workout_to_intervals(api_key, athlete_id, w, workout_text)
                pushed.append({"workout_id": w.id, "intervals_event_id": event_id})
            except Exception as e:
                errors.append({"workout_id": w.id, "error": str(e)})
                yield _sse({"type": "progress", "current": i, "total": total, "message": f"Failed: {name}"})

        err_note = f" ({len(errors)} failed)" if errors else ""
        yield _sse({"type": "done", "pushed": len(pushed), "skipped": len(skipped), "errors": errors,
                    "message": f"Done — pushed {len(pushed)} workout(s){err_note}"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
