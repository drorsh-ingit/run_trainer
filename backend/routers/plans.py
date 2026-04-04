import json
from datetime import date, timedelta
from typing import Generator
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models.models import TrainingPlan, PlannedWorkout, WorkoutActivity, User
from schemas import PlanCreateRequest, PlanOut, PlanReviseRequest, SavePreviewRequest, PreviewChatRequest
from schemas import ClaudePlanResponse, PlanChatRequest, CoachChatRequest
from schemas import AssessStartRequest, AssessReplyRequest, AssessApplyRequest
from services.claude import generate_plan, chat_plan_revision, start_coaching_session, continue_coaching_chat, build_coached_plan, generate_steps_for_workouts
from services.claude import assess_plan_revision, _build_comparison_context
from services.auth import get_current_user

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
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/reply")
def coach_reply(body: CoachChatRequest, current_user: User = Depends(get_current_user)):
    """Continue coaching Q&A. Returns question or ready signal."""
    try:
        return continue_coaching_chat(body.history, body.message, model=body.ai_model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/coach/build")
def coach_build(body: CoachChatRequest, current_user: User = Depends(get_current_user)):
    """Build the plan from the coaching conversation. Returns plan JSON (not saved)."""
    try:
        plan = build_coached_plan(body, body.history, model=body.ai_model)
        return plan.model_dump()
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

    # Generate steps lazily for workouts that don't have them yet (batch by 20)
    missing = [w for w in workouts if not w.steps and w.workout_type not in ("rest", "cross_training")]
    if missing:
        BATCH = 20
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
            except Exception:
                pass  # export still works — just omits steps for this batch
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

    req = PlanCreateRequest(
        goal_distance_km=plan.goal_distance,
        goal_date=plan.goal_date,
        schedule_description=plan.schedule_description,
        injuries=plan.injuries,
        additional_notes=plan.additional_notes,
        current_weekly_km=0,
        fitness_level="intermediate",
    )

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Thinking…"})
        try:
            model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
            result = chat_plan_revision(plan.plan_data, req, body.history, body.message, model=model)
        except Exception as e:
            yield _chat_sse({"type": "error", "message": str(e)})
            return

        if result["type"] == "question":
            yield _chat_sse({"type": "question", "message": result["message"]})
            return

        yield _chat_sse({"type": "status", "message": "Updating your plan…"})
        revised = result["plan"]
        db.query(PlannedWorkout).filter(PlannedWorkout.plan_id == plan.id).delete()
        plan.plan_data = revised.model_dump()
        db.flush()
        _save_workouts(db, plan, revised, plan.goal_date)
        db.commit()
        db.refresh(plan)

        from schemas import PlanOut
        yield _chat_sse({
            "type": "plan",
            "message": result["message"],
            "plan": PlanOut.model_validate(plan).model_dump(mode="json"),
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


@router.post("/{plan_id}/assess/start")
def assess_start(
    plan_id: int,
    body: AssessStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = _load_plan_for_assess(plan_id, db, current_user)

    comparison = _build_comparison_context(plan.workouts, plan.plan_data)
    future_weeks = _get_future_weeks(plan)
    future_plan = {
        "summary": plan.plan_data.get("summary", ""),
        "total_weeks": len(future_weeks),
        "weeks": future_weeks,
    }
    plan_context = _build_plan_context(plan)

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Analyzing your training…"})
        try:
            model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
            result = assess_plan_revision(
                comparison, future_plan, plan_context,
                history=[],
                message="Please assess my plan adherence and suggest adjustments to the remaining weeks.",
                model=model,
            )
        except Exception as e:
            yield _chat_sse({"type": "error", "message": str(e)})
            return

        if result["type"] == "question":
            yield _chat_sse({"type": "question", "message": result["message"]})
        elif result["type"] == "plan":
            yield _chat_sse({
                "type": "plan_preview",
                "message": result["message"],
                "revised_future_plan": result["plan"].model_dump(),
            })

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

    comparison = _build_comparison_context(plan.workouts, plan.plan_data)
    future_weeks = _get_future_weeks(plan)
    future_plan = {
        "summary": plan.plan_data.get("summary", ""),
        "total_weeks": len(future_weeks),
        "weeks": future_weeks,
    }
    plan_context = _build_plan_context(plan)

    def stream() -> Generator[str, None, None]:
        yield _chat_sse({"type": "status", "message": "Thinking…"})
        try:
            model = body.ai_model or plan.ai_model or "claude-sonnet-4-6"
            result = assess_plan_revision(
                comparison, future_plan, plan_context,
                history=body.history,
                message=body.message,
                model=model,
            )
        except Exception as e:
            yield _chat_sse({"type": "error", "message": str(e)})
            return

        if result["type"] == "question":
            yield _chat_sse({"type": "question", "message": result["message"]})
        elif result["type"] == "plan":
            yield _chat_sse({
                "type": "plan_preview",
                "message": result["message"],
                "revised_future_plan": result["plan"].model_dump(),
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
                target_duration_minutes=workout.duration_minutes,
                is_optional=workout.is_optional,
                steps=[s.model_dump() for s in workout.steps],
            ))

    db.commit()
    db.refresh(plan)
    return plan
