from datetime import date as date_type, datetime, timedelta

import requests as _req
from sqlalchemy.orm import Session

from services.garmin import encrypt_str, decrypt_str, _fix_step_durations, _assign_activities_to_workouts

BASE_URL = "https://intervals.icu/api/v1"


def _auth(api_key: str) -> tuple:
    return ("API_KEY", api_key)


def validate_intervals_key(api_key: str) -> tuple[str, str]:
    resp = _req.get(f"{BASE_URL}/athlete/0", auth=_auth(api_key), timeout=15)
    if resp.status_code == 401:
        raise ValueError("Invalid API key")
    if not resp.ok:
        raise ValueError(f"Intervals.icu API error: HTTP {resp.status_code}")
    data = resp.json()
    athlete_id = str(data.get("id", "0"))
    athlete_name = data.get("name") or data.get("email") or f"Athlete {athlete_id}"
    return athlete_id, athlete_name


def _format_duration(duration_type: str, duration_value: int) -> str:
    if duration_type == "DISTANCE":
        m = duration_value
        if m >= 1000:
            km = m / 1000
            return f"{km:g}km"
        return f"{m}mtr"
    secs = duration_value
    if secs >= 60 and secs % 60 == 0:
        return f"{secs // 60}m"
    return f"{secs}s"


def _format_pace(sec_per_km: int) -> str:
    return f"{sec_per_km // 60}:{sec_per_km % 60:02d}"


def build_intervals_workout_text(workout) -> str:
    fixed_steps = _fix_step_durations(workout.steps or [], workout.target_duration_minutes)
    lines = []
    for step in fixed_steps:
        parts = []
        stype = step.get("step_type", "active")
        if stype == "warmup":
            parts.append("Warmup")
        elif stype == "cooldown":
            parts.append("Cooldown")

        parts.append(_format_duration(step.get("duration_type", "TIME"), step.get("duration_value", 0)))

        ttype = step.get("target_type", "OPEN")
        if ttype == "HEART_RATE_ZONE":
            parts.append(f"Z{step.get('target_low', 1)} HR")
        elif ttype == "PACE":
            low = step.get("target_low")
            high = step.get("target_high")
            if low and high and low != high:
                parts.append(f"{_format_pace(low)}-{_format_pace(high)}/km Pace")
            elif low:
                parts.append(f"{_format_pace(low)}/km Pace")

        lines.append("- " + " ".join(parts))
    return "\n".join(lines)


def push_workout_to_intervals(api_key: str, athlete_id: str, workout, workout_text: str) -> str:
    name = (
        f"Week {workout.week_number} - "
        f"{workout.workout_type.replace('_', ' ').title()} "
        f"({workout.scheduled_date})"
    )
    payload = {
        "start_date_local": f"{workout.scheduled_date}T07:00:00",
        "name": name,
        "category": "WORKOUT",
        "type": "Run",
        "description": workout_text,
    }
    resp = _req.post(
        f"{BASE_URL}/athlete/{athlete_id}/events",
        auth=_auth(api_key),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json().get("id", ""))


def delete_intervals_events_for_dates(api_key: str, athlete_id: str, dates: set[str]) -> int:
    import re
    if not dates:
        return 0
    sorted_dates = sorted(dates)
    resp = _req.get(
        f"{BASE_URL}/athlete/{athlete_id}/events",
        auth=_auth(api_key),
        params={"oldest": sorted_dates[0], "newest": sorted_dates[-1]},
        timeout=30,
    )
    if not resp.ok:
        return 0
    deleted = 0
    for event in resp.json():
        name = event.get("name", "")
        if not name.startswith("Week "):
            continue
        m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", name)
        if not m or m.group(1) not in dates:
            continue
        eid = event.get("id")
        if eid:
            dr = _req.delete(f"{BASE_URL}/athlete/{athlete_id}/events/{eid}", auth=_auth(api_key), timeout=30)
            if dr.ok:
                deleted += 1
    return deleted


# ── Activity sync (pull) ──────────────────────────────────────────────────────

def fetch_activities_in_range(
    api_key: str, athlete_id: str, start: date_type, end: date_type
) -> list[dict]:
    """Return raw Intervals.icu activities between start and end (inclusive)."""
    resp = _req.get(
        f"{BASE_URL}/athlete/{athlete_id}/activities",
        auth=_auth(api_key),
        params={"oldest": start.isoformat(), "newest": end.isoformat()},
        timeout=30,
    )
    if resp.status_code in (401, 403):
        raise ValueError("Intervals.icu session invalid — please reconnect")
    if not resp.ok:
        raise ValueError(f"Intervals.icu API error: HTTP {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, list) else []


def _is_run(activity: dict) -> bool:
    return "run" in (activity.get("type") or "").lower()


def _collapse_hr_zones(zone_times: list | None) -> list[int] | None:
    """Collapse Intervals' per-zone seconds (up to 7 zones) into 5 zone percentages.

    Intervals athletes may configure 5–7 HR zones; the app displays 5. Zones 5+
    (VO2max / anaerobic / neuromuscular) are merged into a single top zone.
    Returns [z1%, z2%, z3%, z4%, z5%] summing to ~100, or None when unavailable.
    """
    if not zone_times or not any(zone_times):
        return None
    z = list(zone_times)
    if len(z) > 5:
        z = z[:4] + [sum(z[4:])]
    elif len(z) < 5:
        z = z + [0] * (5 - len(z))
    total = sum(z)
    if total <= 0:
        return None
    return [round(x * 100 / total) for x in z]


def _normalize_activity(a: dict) -> dict:
    """Map an Intervals.icu activity onto the keys the shared matcher/sync expect.

    `startTimeLocal` and `distance` (metres) mirror the Garmin shape so the
    optimal date/distance matcher can be reused unchanged.
    """
    return {
        "id": str(a.get("id")),
        "startTimeLocal": a.get("start_date_local"),
        "distance": a.get("distance") or 0,
        "name": a.get("name"),
        "moving_time": a.get("moving_time") or a.get("elapsed_time") or 0,
        "average_heartrate": a.get("average_heartrate"),
        "average_speed": a.get("average_speed"),
        "hr_zone_times": a.get("icu_hr_zone_times"),
    }


def sync_plan_activities(plan_id: int, user_id: int, db: Session) -> dict:
    """Fetch running activities from Intervals.icu in the plan window and match them
    to the plan's workouts. Mirrors the Garmin sync: clear-and-rebuild, optimal
    date/distance matching, then AI rescoring from stored data."""
    from models.models import IgnoredActivity, IntervalsSession, PlannedWorkout, WorkoutActivity

    session = db.query(IntervalsSession).filter(IntervalsSession.user_id == user_id).first()
    if not session:
        raise ValueError("Intervals.icu not connected")
    try:
        api_key = decrypt_str(session.api_key_enc)
    except Exception:
        raise ValueError("Intervals.icu session invalid — please reconnect")
    athlete_id = session.athlete_id or "0"

    workouts = (
        db.query(PlannedWorkout)
        .filter(PlannedWorkout.plan_id == plan_id, PlannedWorkout.workout_type != "rest")
        .all()
    )
    if not workouts:
        return {"synced": 0, "skipped": 0, "new_total": 0, "total": 0, "total_matched": 0, "errors": []}

    all_dates = sorted(w.scheduled_date for w in workouts)
    fetch_start = all_dates[0] - timedelta(weeks=1)
    fetch_end = all_dates[-1] + timedelta(weeks=1)

    raw = fetch_activities_in_range(api_key, athlete_id, fetch_start, fetch_end)
    run_activities = [
        _normalize_activity(a) for a in raw
        if _is_run(a) and (a.get("start_date_local") or "")[:10]
    ]

    ignored_ids = {
        r.activity_id for r in
        db.query(IgnoredActivity).filter(IgnoredActivity.plan_id == plan_id).all()
    }
    run_activities = [a for a in run_activities if a["id"] not in ignored_ids]

    # Clear all previous activity records for this plan (matched and unmatched)
    db.query(WorkoutActivity).filter(WorkoutActivity.plan_id == plan_id).delete(synchronize_session=False)
    for w in workouts:
        w.completed = False
    db.flush()

    matched = _assign_activities_to_workouts(run_activities, workouts)
    matched_by_act_id = {act["id"]: workout_id for workout_id, act in matched.items()}
    workout_map = {w.id: w for w in workouts}

    synced, errors = 0, []
    for act in run_activities:
        actual_km = (act["distance"] or 0) / 1000
        hr_zones = _collapse_hr_zones(act.get("hr_zone_times"))
        workout_id = matched_by_act_id.get(act["id"])

        row = WorkoutActivity(plan_id=plan_id, workout_id=workout_id)
        db.add(row)
        row.strava_activity_id = act["id"]
        row.name = act.get("name")
        row.actual_distance_km = round(actual_km, 2)
        row.actual_duration_sec = int(act.get("moving_time") or 0)
        row.average_hr = act.get("average_heartrate")
        row.average_speed_ms = act.get("average_speed")
        row.streams_data = {"hr_zones": hr_zones} if hr_zones else None
        try:
            row.start_date = datetime.fromisoformat(act["startTimeLocal"])
        except Exception:
            row.start_date = None

        if workout_id:
            workout_map[workout_id].completed = True
            synced += 1

    skipped = len(run_activities) - synced
    db.commit()

    # Score matched activities from stored data (no Intervals re-fetch)
    try:
        from services.strava import rescore_plan_activities
        rescore_plan_activities(plan_id, user_id, db)
    except Exception:
        pass

    return {
        "synced": synced,
        "skipped": skipped,
        "new_total": len(run_activities),
        "total": len(run_activities),
        "total_matched": synced,
        "errors": errors,
    }
