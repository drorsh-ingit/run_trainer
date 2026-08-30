import json
import logging
import queue
import time
import threading
from datetime import date, timedelta
from typing import Generator
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from database import get_db, SessionLocal
from models.models import TrainingPlan, PlannedWorkout, WorkoutActivity, WorkoutFeedback, User
from schemas import PlanCreateRequest, PlanOut, PlanReviseRequest, SavePreviewRequest, PreviewChatRequest
from schemas import ClaudePlanResponse, PlanChatRequest, CoachChatRequest
from schemas import AssessStartRequest, AssessReplyRequest, AssessApplyRequest
from services.claude import generate_plan, chat_plan_revision, start_coaching_session, continue_coaching_chat, build_coached_plan, generate_steps_for_workouts
from services.claude import assess_plan_chat, assess_build_plan, _build_comparison_context, AIServiceError
from services.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])

def _is_authorized(plan, current_user) -> bool:
    return plan.user_id == current_user.id or current_user.username == "admin"

DAY_OFFSETS = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def _compute_scheduled_date(week_1_start: date, week_number: int, day_of_week: str) -> date:
    week_start = week_1_start + timedelta(weeks=week_number - 1)
    return week_start + timedelta(days=DAY_OFFSETS.get(day_of_week, 0))


def _save_workouts(db: Session, plan: TrainingPlan, claude_plan, goal_date: date | None):
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    week_1_start = today if today.weekday() == 0 else today + timedelta(days=days_until_monday)

    has_race_workout = False
    for week in claude_plan.weeks:
        for workout in week.workouts:
            if workout.type == "race" and goal_date:
                scheduled = goal_date
                has_race_workout = True
            else:
                scheduled = _compute_scheduled_date(week_1_start, week.week_number, workout.day_of_week)
                if goal_date and scheduled == goal_date:
                    continue  # don't overwrite race day with a regular workout

            db.add(PlannedWorkout(
                plan_id=plan.id,
                week_number=week.week_number,
                day_of_week=workout.day_of_week,
                scheduled_date=scheduled,
                workout_type=workout.type,
                description=workout.description,
                target_distance_km=workout.distance_km,
                distance_label=workout.distance_label,
                target_duration_minutes=workout.duration_minutes,
                is_optional=workout.is_optional,
                steps=[s.model_dump() for s in workout.steps],
            ))

    # Guarantee a race workout exists on goal_date for race plans even if Claude omitted it
    if goal_date and not has_race_workout:
        last_week = max((w.week_number for w in claude_plan.weeks), default=1)
        db.add(PlannedWorkout(
            plan_id=plan.id,
            week_number=last_week,
            day_of_week=goal_date.strftime("%A"),
            scheduled_date=goal_date,
            workout_type="race",
            description=f"Race day! {plan.goal_distance} km. Trust your training and run your race.",
            target_distance_km=plan.goal_distance,
            target_duration_minutes=None,
            is_optional=False,
            steps=[],
        ))


def _replace_plan_workouts(db: Session, plan: TrainingPlan, claude_plan, goal_date: date | None):
    """Rewrite a plan's PlannedWorkouts without orphaning linked activities/feedback.

    workout_activities and workout_feedback both reference planned_workouts.id (no ON DELETE),
    so deleting the workouts outright violates those FKs on Postgres. Instead we detach the
    children, delete + recreate the workouts, then re-link each child to the new workout on the
    same calendar date. Nothing is deleted from workout_activities/feedback, so no synced activity
    data is lost — any child whose date no longer has a workout is simply left unmatched (a state
    the app already supports).
    """
    old = db.query(PlannedWorkout).filter(PlannedWorkout.plan_id == plan.id).all()
    old_ids = [w.id for w in old]
    date_by_old_id = {w.id: w.scheduled_date for w in old}

    activities = (
        db.query(WorkoutActivity).filter(WorkoutActivity.workout_id.in_(old_ids)).all() if old_ids else []
    )
    feedback = (
        db.query(WorkoutFeedback).filter(WorkoutFeedback.workout_id.in_(old_ids)).all() if old_ids else []
    )
    # An activity's own start_date is the ground truth for which day it belongs to; fall back to
    # the date of the workout it was matched to. Feedback has no date of its own, so use its
    # workout's date.
    act_target_date = {
        a.id: (a.start_date.date() if a.start_date else date_by_old_id.get(a.workout_id))
        for a in activities
    }
    fb_target_date = {f.id: date_by_old_id.get(f.workout_id) for f in feedback}

    # Detach so the workouts can be deleted, then delete + recreate.
    for a in activities:
        a.workout_id = None
    for f in feedback:
        f.workout_id = None
    db.flush()
    db.query(PlannedWorkout).filter(PlannedWorkout.plan_id == plan.id).delete()
    db.flush()
    _save_workouts(db, plan, claude_plan, goal_date)
    db.flush()  # assign ids to the new workouts

    # Re-link children to the new workout sharing their date. workout_activities.workout_id is
    # unique and PlannedWorkout.activity is one-to-one, so hand each new workout at most one child.
    new_id_by_date: dict[date, int] = {}
    for w in db.query(PlannedWorkout).filter(PlannedWorkout.plan_id == plan.id).all():
        new_id_by_date.setdefault(w.scheduled_date, w.id)

    taken_by_activity: set[int] = set()
    for a in activities:
        wid = new_id_by_date.get(act_target_date.get(a.id))
        if wid is not None and wid not in taken_by_activity:
            a.workout_id = wid
            taken_by_activity.add(wid)

    taken_by_feedback: set[int] = set()
    for f in feedback:
        wid = new_id_by_date.get(fb_target_date.get(f.id))
        if wid is not None and wid not in taken_by_feedback:
            f.workout_id = wid
            taken_by_feedback.add(wid)


@router.get("/", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == current_user.id)
        .order_by(TrainingPlan.created_at.desc())
        .all()
    )


@router.post("/", status_code=201)
def create_plan(
    req: PlanCreateRequest,
    dry_run: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        claude_plan = generate_plan(req, model=req.ai_model)
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Plan generation failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if dry_run:
        return claude_plan.model_dump()

    plan = TrainingPlan(
        user_id=current_user.id,
        plan_type=req.plan_type,
        goal_distance=req.goal_distance_km,
        goal_date=req.goal_date,
        plan_duration_weeks=req.plan_duration_weeks,
        schedule_description=req.schedule_description,
        injuries=req.injuries,
        additional_notes=req.additional_notes,
        plan_data=claude_plan.model_dump(),
        ai_model=req.ai_model,
    )
    db.add(plan)
    db.flush()

    _save_workouts(db, plan, claude_plan, req.goal_date)

    db.commit()
    db.refresh(plan)
    return plan


@router.post("/revise")
def revise_preview(
    body: PlanReviseRequest,
    current_user: User = Depends(get_current_user),
):
    """Revise an unsaved preview plan. Returns updated plan JSON without persisting."""
    req = PlanCreateRequest(
        goal_distance_km=body.goal_distance_km,
        goal_date=body.goal_date,
        schedule_description=body.schedule_description,
        injuries=body.injuries,
        additional_notes=body.additional_notes,
        current_weekly_km=0,
        fitness_level="intermediate",
    )
    try:
        revised = adjust_plan(body.current_plan, req, body.comment)
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Plan revision failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return revised.model_dump()


@router.post("/preview/chat")
def preview_chat(body: PreviewChatRequest, current_user: User = Depends(get_current_user)):
    """Conversational revision of an unsaved preview plan. Nothing is persisted."""
    req = PlanCreateRequest(
        plan_type=body.plan_type,
        goal_distance_km=body.goal_distance_km,
        goal_date=body.goal_date,
        plan_duration_weeks=body.plan_duration_weeks,
        schedule_description=body.schedule_description,
        injuries=body.injuries,
        additional_notes=body.additional_notes,
        current_weekly_km=0,
        fitness_level="intermediate",
    )
    try:
        result = chat_plan_revision(body.current_plan, req, body.history, body.message, model=body.ai_model)
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Chat failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if result["type"] == "question":
        return {"type": "question", "message": result["message"]}
    return {"type": "plan", "message": result["message"], "plan": result["plan"].model_dump()}


@router.post("/save-preview", status_code=201, response_model=PlanOut)
def save_preview(
    body: SavePreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save a pre-approved preview plan without calling Claude again."""
    try:
        claude_plan = ClaudePlanResponse.model_validate(body.generated_plan)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid plan data: {e}")

    plan = TrainingPlan(
        user_id=current_user.id,
        plan_type=body.plan_type,
        goal_distance=body.goal_distance_km,
        goal_date=body.goal_date,
        plan_duration_weeks=body.plan_duration_weeks,
        schedule_description=body.schedule_description,
        injuries=body.injuries,
        additional_notes=body.additional_notes,
        plan_data=claude_plan.model_dump(),
        ai_model=body.ai_model,
    )
    db.add(plan)
    db.flush()

    _save_workouts(db, plan, claude_plan, body.goal_date)

    db.commit()
    db.refresh(plan)
    return plan


@router.post("/coach/start")
def coach_start(req: PlanCreateRequest, current_user: User = Depends(get_current_user)):
    """Start a coaching session — returns Claude's opening questions."""
    try:
        return start_coaching_session(req, model=req.ai_model)
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/reply")
def coach_reply(body: CoachChatRequest, current_user: User = Depends(get_current_user)):
    """Continue coaching Q&A. Returns question or ready signal."""
    try:
        return continue_coaching_chat(body.history, body.message, model=body.ai_model)
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/build")
def coach_build(body: CoachChatRequest, current_user: User = Depends(get_current_user)):
    """Build the plan from the coaching conversation. Returns plan JSON (not saved)."""
    try:
        plan = build_coached_plan(body, body.history, model=body.ai_model)
        return plan.model_dump()
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


STEP_TYPE_MAP = {
    "warmup": "WARMUP", "active": "INTERVAL",
    "rest": "RECOVERY", "cooldown": "COOLDOWN",
}


def _to_garmin_step(step: dict) -> dict:
    return {
        "type": STEP_TYPE_MAP.get(step["step_type"], "OTHER"),
        "durationType": step["duration_type"],
        "durationValue": step["duration_value"],
        "targetType": "PACE_ZONE" if step["target_type"] == "PACE" else step["target_type"],
        "targetValueLow": step.get("target_low"),
        "targetValueHigh": step.get("target_high"),
    }


@router.get("/{plan_id}/garmin-export")
def garmin_export(
    plan_id: int,
    month: str | None = None,  # optional filter: "YYYY-MM"
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(403, "Not authorized")

    query = (
        db.query(PlannedWorkout)
        .filter(PlannedWorkout.plan_id == plan_id, PlannedWorkout.workout_type != "rest")
    )
    if month:
        try:
            from datetime import datetime
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

    workouts = query.order_by(PlannedWorkout.week_number, PlannedWorkout.scheduled_date).all()

    # Generate steps lazily for workouts that don't have them yet. Keep batches
    # small so the steps JSON stays under the model token cap (see BATCH note in
    # garmin._ensure_steps) — a truncated batch would otherwise lose all steps.
    missing = [w for w in workouts if not w.steps and w.workout_type not in ("rest", "cross_training")]
    if missing:
        BATCH = 10
        for i in range(0, len(missing), BATCH):
            batch = missing[i:i + BATCH]
            batch_input = [
                {"id": w.id, "type": w.workout_type, "description": w.description}
                for w in batch
            ]
            try:
                steps_map = generate_steps_for_workouts(batch_input)
                for w in batch:
                    w.steps = steps_map.get(w.id, [])
            except Exception:  # export still works — just omits steps for this batch
                logger.exception("Step generation failed for export batch of %d workout(s)", len(batch))
        db.commit()

    result = []
    for w in workouts:
        if not w.steps:
            continue
        result.append({
            "workoutName": f"Week {w.week_number} – {w.workout_type.replace('_', ' ').title()} ({w.scheduled_date})",
            "sport": "RUNNING",
            "steps": [_to_garmin_step(s) for s in w.steps],
        })
    return result


@router.get("/{plan_id}/fit-export")
def fit_export(
    plan_id: int,
    month: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import io
    import zipfile
    from routers.garmin import _load_workouts, _ensure_steps
    from services.garmin import build_fit_workout_file

    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(403, "Not authorized")

    workouts = _load_workouts(db, plan_id, month)
    _ensure_steps(workouts, db)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for w in workouts:
            if not w.steps:
                continue
            fit_bytes = build_fit_workout_file(w)
            fname = f"week{w.week_number}_{w.workout_type}_{w.scheduled_date}.fit"
            zf.writestr(fname, fit_bytes)
    buf.seek(0)

    if buf.getbuffer().nbytes <= 22:
        raise HTTPException(404, "No workouts with steps found")

    filename = f"training-plan-{plan_id}"
    if month:
        filename += f"-{month}"
    filename += ".zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    plan = (
        db.query(TrainingPlan)
        .options(joinedload(TrainingPlan.workouts).joinedload(PlannedWorkout.activity))
        .filter(TrainingPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized to view this plan")
    return plan


@router.post("/{plan_id}/rescore")
def rescore_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized")
    from services.strava import rescore_plan_activities
    count = rescore_plan_activities(plan_id, current_user.id, db)
    return {"rescored": count}


@router.post("/{plan_id}/activities/{strava_id}/rescore")
def rescore_activity(
    plan_id: int,
    strava_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized")
    wa = (
        db.query(WorkoutActivity)
        .filter(WorkoutActivity.strava_activity_id == strava_id, WorkoutActivity.plan_id == plan_id)
        .first()
    )
    if not wa:
        raise HTTPException(status_code=404, detail="Activity not found")
    from services.strava import rescore_single_activity
    from services.claude import AIServiceError
    try:
        score, comment = rescore_single_activity(wa.id, current_user.id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AIServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return {"score": score, "comment": comment}


@router.delete("/{plan_id}", status_code=204)
def delete_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized")
    db.delete(plan)
    db.commit()


def _chat_sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _plan_to_context_request(plan: TrainingPlan) -> PlanCreateRequest:
    """Rehydrate a saved plan into the context object the revision model needs.

    Uses model_construct to bypass create-time validation: the stored plan is already valid, and
    the strict validators reject cases that are perfectly fine to *revise* — general plans (no
    goal_distance) and race plans whose goal_date has already passed. Building a strict
    PlanCreateRequest here 500'd on those plans ("Internal server error" in the adjust chat).
    """
    return PlanCreateRequest.model_construct(
        plan_type=plan.plan_type or "race",
        goal_distance_km=plan.goal_distance,
        goal_date=plan.goal_date,
        plan_duration_weeks=plan.plan_duration_weeks,
        schedule_description=plan.schedule_description or "",
        injuries=plan.injuries or "",
        additional_notes=plan.additional_notes or "",
        current_weekly_km=0,
        fitness_level="intermediate",
    )


@router.post("/{plan_id}/chat")
def chat_adjust(
    plan_id: int,
    body: PlanChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized")

    req = _plan_to_context_request(plan)

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Thinking…"})

        # Run the (slow, blocking) model call on a worker thread so we can emit SSE heartbeats
        # while we wait. Without periodic bytes the browser can idle-timeout the streaming fetch
        # mid-call and surface a network error ("Load failed" in Safari).
        # NB: read everything the worker needs off the ORM `plan` here, on the request thread —
        # SQLAlchemy sessions are not thread-safe, and touching `plan.plan_data` from the worker
        # detaches the instance ("not persistent within this Session") before the later save.
        model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
        plan_data = plan.plan_data
        result_q: queue.Queue = queue.Queue(maxsize=1)

        def _work():
            try:
                result_q.put(("ok", chat_plan_revision(plan_data, req, body.history, body.message, model=model)))
            except Exception as e:  # surfaced to the client as an error event
                result_q.put(("error", str(e)))

        worker = threading.Thread(target=_work, daemon=True)
        worker.start()

        while True:
            try:
                status, payload = result_q.get(timeout=10)
                break
            except queue.Empty:
                yield ": keepalive\n\n"  # SSE comment — ignored by the client, keeps the socket warm

        if status == "error":
            yield _chat_sse({"type": "error", "message": payload})
            return
        result = payload

        if result["type"] == "question":
            yield _chat_sse({"type": "question", "message": result["message"]})
            return

        yield _chat_sse({"type": "status", "message": "Updating your plan…"})
        # Persist on a fresh session. The request-scoped `db` from Depends(get_db) is closed when
        # the endpoint function returns, which happens BEFORE this generator streams — reusing it
        # here fails with "Instance ... is not persistent within this Session".
        from schemas import PlanOut
        revised = result["plan"]
        save_db = SessionLocal()
        try:
            saved = save_db.query(TrainingPlan).filter(TrainingPlan.id == plan_id).first()
            if saved is None:
                yield _chat_sse({"type": "error", "message": "Plan no longer exists."})
                return
            saved.plan_data = revised.model_dump()
            save_db.flush()
            _replace_plan_workouts(save_db, saved, revised, saved.goal_date)
            save_db.commit()
            save_db.refresh(saved)
            payload_plan = PlanOut.model_validate(saved).model_dump(mode="json")
        except Exception as e:
            save_db.rollback()
            logging.exception("Failed to save revised plan %s", plan_id)
            yield _chat_sse({"type": "error", "message": f"Couldn't save the revised plan: {e}"})
            return
        finally:
            save_db.close()

        yield _chat_sse({
            "type": "plan",
            "message": result["message"],
            "plan": payload_plan,
        })

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _load_plan_for_assess(plan_id: int, db: Session, current_user: User) -> TrainingPlan:
    plan = (
        db.query(TrainingPlan)
        .options(joinedload(TrainingPlan.workouts).joinedload(PlannedWorkout.activity))
        .filter(TrainingPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized")
    return plan


def _build_plan_context(plan: TrainingPlan) -> str:
    parts = [f"- Goal: {plan.goal_distance} km on {plan.goal_date}" if plan.goal_date
             else f"- General plan: {plan.plan_duration_weeks} weeks"]
    parts.append(f"- Schedule: {plan.schedule_description}")
    parts.append(f"- Injuries: {plan.injuries or 'None'}")
    parts.append(f"- Notes: {plan.additional_notes or 'None'}")
    return "\n".join(parts)


def _get_unmatched_activities(plan_id: int, db: Session) -> list:
    """Get activities synced to this plan that didn't match any planned workout."""
    return (
        db.query(WorkoutActivity)
        .filter(WorkoutActivity.plan_id == plan_id, WorkoutActivity.workout_id.is_(None))
        .all()
    )


def _get_future_weeks(plan: TrainingPlan) -> list[dict]:
    """Get future week numbers based on workouts scheduled from today onwards."""
    today = date.today()
    future_week_nums = set()
    for w in plan.workouts:
        if w.scheduled_date >= today:
            future_week_nums.add(w.week_number)
    return [
        week for week in plan.plan_data.get("weeks", [])
        if week.get("week_number") in future_week_nums
    ]


def _assess_data(plan_id: int, plan: TrainingPlan, db: Session):
    """Gather comparison data and future plan for assessment endpoints."""
    unmatched = _get_unmatched_activities(plan_id, db)
    comparison = _build_comparison_context(plan.workouts, plan.plan_data, unmatched)
    future_weeks = _get_future_weeks(plan)
    future_plan = {
        "summary": plan.plan_data.get("summary", ""),
        "total_weeks": len(future_weeks),
        "weeks": future_weeks,
    }
    plan_context = _build_plan_context(plan)
    return comparison, future_plan, plan_context


@router.post("/{plan_id}/assess/start")
def assess_start(
    plan_id: int,
    body: AssessStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = _load_plan_for_assess(plan_id, db, current_user)
    comparison, future_plan, plan_context = _assess_data(plan_id, plan, db)

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Analyzing your training…"})
        try:
            model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
            result = assess_plan_chat(
                comparison, future_plan, plan_context,
                history=[],
                message="Please assess my plan adherence and suggest adjustments to the remaining weeks.",
                model=model,
            )
        except Exception as e:
            yield _chat_sse({"type": "error", "message": str(e)})
            return

        if result["type"] == "ready":
            yield _chat_sse({"type": "ready", "message": result["message"]})
        else:
            yield _chat_sse({"type": "question", "message": result["message"]})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{plan_id}/assess/reply")
def assess_reply(
    plan_id: int,
    body: AssessReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = _load_plan_for_assess(plan_id, db, current_user)
    comparison, future_plan, plan_context = _assess_data(plan_id, plan, db)

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Thinking…"})
        try:
            model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
            result = assess_plan_chat(
                comparison, future_plan, plan_context,
                history=body.history,
                message=body.message,
                model=model,
            )
        except Exception as e:
            yield _chat_sse({"type": "error", "message": str(e)})
            return

        if result["type"] == "ready":
            yield _chat_sse({"type": "ready", "message": result["message"]})
        else:
            yield _chat_sse({"type": "question", "message": result["message"]})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{plan_id}/assess/build")
def assess_build(
    plan_id: int,
    body: AssessReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Build the revised plan after Q&A is complete. This is the slow step."""
    plan = _load_plan_for_assess(plan_id, db, current_user)
    comparison, future_plan, plan_context = _assess_data(plan_id, plan, db)

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Building revised plan…"})

        model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
        from schemas import ChatMessage
        chat_history = [ChatMessage(role=m.role, content=m.content) for m in body.history]

        result: list = [None]
        error: list = [None]

        def run_build():
            try:
                result[0] = assess_build_plan(
                    comparison, future_plan, plan_context,
                    history=chat_history,
                    model=model,
                )
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=run_build)
        t.start()

        while t.is_alive():
            t.join(timeout=15)
            if t.is_alive():
                yield ": keepalive\n\n"

        if error[0]:
            yield _chat_sse({"type": "error", "message": str(error[0])})
            return

        yield _chat_sse({
            "type": "plan_preview",
            "message": result[0].summary,
            "revised_future_plan": result[0].model_dump(),
        })

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{plan_id}/assess/apply", response_model=PlanOut)
def assess_apply(
    plan_id: int,
    body: AssessApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = (
        db.query(TrainingPlan)
        .options(joinedload(TrainingPlan.workouts).joinedload(PlannedWorkout.activity))
        .filter(TrainingPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not _is_authorized(plan, current_user):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        revised = ClaudePlanResponse.model_validate(body.revised_plan_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid plan data: {e}")

    today = date.today()
    revised_week_nums = {w.week_number for w in revised.weeks}

    # Delete only future PlannedWorkout rows that will be replaced
    db.query(PlannedWorkout).filter(
        PlannedWorkout.plan_id == plan.id,
        PlannedWorkout.scheduled_date >= today,
    ).delete()

    # Merge plan_data: keep past weeks, replace future weeks
    existing_weeks = plan.plan_data.get("weeks", [])
    past_weeks = [w for w in existing_weeks if w.get("week_number") not in revised_week_nums]
    merged_weeks = past_weeks + [w.model_dump() for w in revised.weeks]
    merged_weeks.sort(key=lambda w: w.get("week_number", 0))

    plan.plan_data = {
        **plan.plan_data,
        "total_weeks": len(merged_weeks),
        "weeks": merged_weeks,
    }
    db.flush()

    # Save only the new future workouts using existing _save_workouts helper logic
    # We need to compute week_1_start from existing past workouts
    past_workouts = [w for w in plan.workouts if w.scheduled_date < today]
    if past_workouts:
        # Derive week_1_start from the earliest workout
        earliest = min(w.scheduled_date for w in past_workouts)
        # week_1_start is the Monday of that week
        week_1_start = earliest - timedelta(days=earliest.weekday())
    else:
        days_until_monday = (7 - today.weekday()) % 7
        week_1_start = today if today.weekday() == 0 else today + timedelta(days=days_until_monday)

    for week in revised.weeks:
        for workout in week.workouts:
            if workout.type == "race" and plan.goal_date:
                scheduled = plan.goal_date
            else:
                scheduled = _compute_scheduled_date(week_1_start, week.week_number, workout.day_of_week)
                if plan.goal_date and scheduled == plan.goal_date:
                    continue

            db.add(PlannedWorkout(
                plan_id=plan.id,
                week_number=week.week_number,
                day_of_week=workout.day_of_week,
                scheduled_date=scheduled,
                workout_type=workout.type,
                description=workout.description,
                target_distance_km=workout.distance_km,
                distance_label=workout.distance_label,
                target_duration_minutes=workout.duration_minutes,
                is_optional=workout.is_optional,
                steps=[s.model_dump() for s in workout.steps],
            ))

    db.commit()
    db.refresh(plan)
    return plan
