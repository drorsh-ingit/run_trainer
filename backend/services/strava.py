import logging
import time
import urllib.parse
from datetime import datetime, timezone, date as date_type

import httpx
from sqlalchemy.orm import Session

from config import settings
from models.models import IgnoredActivity, PlannedWorkout, StravaToken, WorkoutActivity

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_API = "https://www.strava.com/api/v3"


# ── Auth ─────────────────────────────────────────────────────────────────────

def get_auth_url(redirect_uri: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": settings.strava_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "activity:read_all",
        "approval_prompt": "auto",
    })
    return f"{STRAVA_AUTH_URL}?{params}"


def exchange_code(code: str, user_id: int, db: Session) -> StravaToken:
    resp = httpx.post(STRAVA_TOKEN_URL, data={
        "client_id": settings.strava_client_id,
        "client_secret": settings.strava_client_secret,
        "code": code,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    data = resp.json()

    token = db.query(StravaToken).filter(StravaToken.user_id == user_id).first()
    if not token:
        token = StravaToken(user_id=user_id)
        db.add(token)

    token.access_token = data["access_token"]
    token.refresh_token = data["refresh_token"]
    token.expires_at = data["expires_at"]
    token.athlete_id = str(data["athlete"]["id"])
    token.athlete_name = f"{data['athlete']['firstname']} {data['athlete']['lastname']}"
    db.commit()
    db.refresh(token)
    return token


def get_valid_token(user_id: int, db: Session) -> str:
    token = db.query(StravaToken).filter(StravaToken.user_id == user_id).first()
    if not token:
        raise ValueError("Strava not connected")
    if token.expires_at < int(time.time()) + 60:
        _refresh_token(token, db)
    return token.access_token


def _refresh_token(token: StravaToken, db: Session) -> None:
    resp = httpx.post(STRAVA_TOKEN_URL, data={
        "client_id": settings.strava_client_id,
        "client_secret": settings.strava_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
    })
    resp.raise_for_status()
    data = resp.json()
    token.access_token = data["access_token"]
    token.refresh_token = data["refresh_token"]
    token.expires_at = data["expires_at"]
    db.commit()


# ── Strava API calls ──────────────────────────────────────────────────────────

def fetch_recent_activities(access_token: str, after_epoch: int | None = None) -> list[dict]:
    params = {"per_page": 100, "page": 1}
    if after_epoch:
        params["after"] = after_epoch
    resp = httpx.get(
        f"{STRAVA_API}/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_streams(access_token: str, activity_id: str) -> dict:
    resp = httpx.get(
        f"{STRAVA_API}/activities/{activity_id}/streams",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"keys": "heartrate,velocity_smooth,time,distance", "key_by_type": "true"},
        timeout=30,
    )
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    data = resp.json()
    return {k: v["data"] for k, v in data.items() if isinstance(v, dict) and "data" in v}


def fetch_athlete_max_hr(access_token: str) -> int | None:
    """Return the athlete's max heart rate from their Strava profile, or None if not set."""
    resp = httpx.get(
        f"{STRAVA_API}/athlete",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("max_heartrate") or None


def compute_hr_zones(hr_stream: list[int], max_hr: int = 185) -> list[int]:
    """Return [z1%, z2%, z3%, z4%, z5%] as integers (sum to ~100)."""
    thresholds = [0.60, 0.70, 0.80, 0.90, 1.01]
    zones = [0, 0, 0, 0, 0]
    for hr in hr_stream:
        pct = hr / max_hr
        for i, t in enumerate(thresholds):
            if pct <= t:
                zones[i] += 1
                break
        else:
            zones[4] += 1
    total = sum(zones)
    if total == 0:
        return [0, 0, 0, 0, 0]
    return [round(z * 100 / total) for z in zones]


# ── Match scoring ────────────────────────────────────────────────────────────

def _score_match(workout: PlannedWorkout, actual_km: float, actual_pace: float | None) -> tuple[int, str]:
    """Return (score 0-100, one-line coaching comment)."""
    target_km = workout.target_distance_km

    if not target_km:
        return 75, f"Logged {actual_km:.1f} km — no target distance set for this workout."

    ratio = actual_km / target_km
    pace_str = (
        f" at {int(actual_pace)}:{int((actual_pace % 1) * 60):02d} min/km"
        if actual_pace else ""
    )

    if ratio >= 1.05:
        score, comment = 95, f"Went beyond the plan — {actual_km:.1f} km vs {target_km:.1f} km target{pace_str}. Consider saving energy on non-key sessions."
    elif ratio >= 0.98:
        score, comment = 100, f"Nailed it — {actual_km:.1f} km as planned{pace_str}."
    elif ratio >= 0.92:
        score, comment = 85, f"Almost there — {actual_km:.1f} of {target_km:.1f} km{pace_str}. Solid effort."
    elif ratio >= 0.80:
        score, comment = 65, f"Cut short — {actual_km:.1f} of {target_km:.1f} km{pace_str}. Check how you're recovering."
    else:
        score, comment = 40, f"Well short of target — {actual_km:.1f} of {target_km:.1f} km{pace_str}. Listen to your body and adjust if needed."

    return score, comment


# ── Matching ──────────────────────────────────────────────────────────────────

def _best_match(candidates: list[PlannedWorkout], actual_km: float) -> PlannedWorkout | None:
    non_rest = [w for w in candidates if w.workout_type != "rest"]
    pool = non_rest if non_rest else candidates
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]
    with_dist = [w for w in pool if w.target_distance_km is not None]
    if not with_dist:
        return pool[0]
    return min(with_dist, key=lambda w: abs((w.target_distance_km or 0) - actual_km))


# ── Sync ─────────────────────────────────────────────────────────────────────

def sync_plan_activities(plan_id: int, user_id: int, db: Session) -> dict:
    from models.models import User
    user = db.query(User).filter(User.id == user_id).first()
    user_max_hr = user.max_hr if user else None

    access_token = get_valid_token(user_id, db)

    workouts = (
        db.query(PlannedWorkout)
        .filter(PlannedWorkout.plan_id == plan_id, PlannedWorkout.workout_type != "rest")
        .all()
    )

    workout_by_date: dict[str, list[PlannedWorkout]] = {}
    for w in workouts:
        key = str(w.scheduled_date)
        workout_by_date.setdefault(key, []).append(w)

    # Use earliest workout date as the 'after' filter
    all_dates = sorted(workout_by_date.keys())
    after_epoch = None
    if all_dates:
        d = date_type.fromisoformat(all_dates[0])
        after_epoch = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())

    activities = fetch_recent_activities(access_token, after_epoch=after_epoch)
    print(f"[STRAVA SYNC] plan={plan_id}: fetched {len(activities)} activities (after_epoch={after_epoch})")
    for act in activities:
        print(f"[STRAVA SYNC]   id={act.get('id')} type={act.get('type')} date={act.get('start_date_local', '')[:10]} name={act.get('name')}")

    # Use user's configured max HR, fall back to Strava athlete profile
    max_hr = user_max_hr or fetch_athlete_max_hr(access_token)

    ignored_ids = {
        r.activity_id for r in
        db.query(IgnoredActivity).filter(IgnoredActivity.plan_id == plan_id).all()
    }

    # Filter to running activities, excluding ignored
    run_activities = [
        act for act in activities
        if act.get("type") in ("Run", "VirtualRun", "TrailRun")
        and str(act.get("id")) not in ignored_ids
    ]
    print(f"[STRAVA SYNC] plan={plan_id}: {len(run_activities)} run activities after filtering (ignored_ids={ignored_ids})")

    # Clear all previous activity records for this plan (matched and unmatched)
    db.query(WorkoutActivity).filter(WorkoutActivity.plan_id == plan_id).delete(synchronize_session=False)
    for w in workouts:
        w.completed = False
    db.flush()

    # Match activities to workouts by date, then best distance fit
    matched_workout_ids: set[int] = set()
    act_to_workout: dict[str, PlannedWorkout] = {}
    for act in run_activities:
        act_date = act["start_date_local"][:10]
        candidates = workout_by_date.get(act_date, [])
        # Exclude workouts already claimed by another activity
        available = [w for w in candidates if w.id not in matched_workout_ids]
        workout = _best_match(available, act["distance"] / 1000)
        if workout:
            act_to_workout[str(act["id"])] = workout
            matched_workout_ids.add(workout.id)

    workout_map = {w.id: w for w in workouts}
    synced, skipped, errors = 0, 0, []

    for act in run_activities:
        act_id = str(act["id"])
        actual_km = round(act["distance"] / 1000, 2)
        avg_speed = act.get("average_speed")
        actual_pace = round(1000 / (avg_speed * 60), 2) if avg_speed and avg_speed > 0 else None
        workout = act_to_workout.get(act_id)

        # Fetch streams for matched activities only (avoid excessive API calls)
        streams = {}
        if workout:
            try:
                streams = fetch_streams(access_token, act_id)
                if "heartrate" in streams and max_hr:
                    streams["hr_zones"] = compute_hr_zones(streams["heartrate"], max_hr)
            except Exception as e:
                errors.append(f"Streams fetch failed for activity {act_id}: {e}")

        row = WorkoutActivity(plan_id=plan_id, workout_id=workout.id if workout else None)
        db.add(row)

        row.strava_activity_id = act_id
        row.name = act.get("name")
        row.actual_distance_km = actual_km
        row.actual_duration_sec = act.get("moving_time")
        row.average_hr = act.get("average_heartrate")
        row.average_speed_ms = avg_speed
        row.start_date = datetime.fromisoformat(act["start_date_local"])
        row.streams_data = streams or None

        if workout:
            hr_zones = streams.get("hr_zones") if streams else None
            try:
                from services.claude import generate_match_analysis
                score, comment = generate_match_analysis(
                    workout_type=workout.workout_type,
                    description=workout.description or "",
                    target_distance_km=workout.target_distance_km,
                    target_duration_min=workout.target_duration_minutes,
                    actual_distance_km=actual_km,
                    actual_duration_sec=act.get("moving_time"),
                    actual_pace_min_per_km=actual_pace,
                    average_hr=act.get("average_heartrate"),
                    hr_zones=hr_zones,
                )
            except Exception:
                score, comment = _score_match(workout, actual_km, actual_pace)

            row.match_score = score
            row.match_comment = comment
            workout_map[workout.id].completed = True
            synced += 1
        else:
            skipped += 1

    db.commit()
    return {"synced": synced, "skipped": skipped, "errors": errors}


# ── Rescore ───────────────────────────────────────────────────────────────────

def rescore_plan_activities(plan_id: int, user_id: int, db: Session) -> int:
    """Re-score all matched activities for a plan using stored data (no Strava re-fetch).
    Returns the number of activities rescored."""
    from models.models import User

    user = db.query(User).filter(User.id == user_id).first()
    max_hr = user.max_hr if user else None

    activities = (
        db.query(WorkoutActivity)
        .filter(WorkoutActivity.plan_id == plan_id, WorkoutActivity.workout_id.isnot(None))
        .all()
    )

    rescored = 0
    for wa in activities:
        workout = db.query(PlannedWorkout).filter(PlannedWorkout.id == wa.workout_id).first()
        if not workout:
            continue

        hr_zones = None
        if wa.streams_data:
            if "heartrate" in wa.streams_data and max_hr:
                hr_zones = compute_hr_zones(wa.streams_data["heartrate"], max_hr)
            elif "hr_zones" in wa.streams_data:
                hr_zones = wa.streams_data["hr_zones"]

        actual_pace = (
            round(1000 / (wa.average_speed_ms * 60), 2)
            if wa.average_speed_ms and wa.average_speed_ms > 0
            else None
        )

        try:
            from services.claude import generate_match_analysis
            score, comment = generate_match_analysis(
                workout_type=workout.workout_type,
                description=workout.description or "",
                target_distance_km=workout.target_distance_km,
                target_duration_min=workout.target_duration_minutes,
                actual_distance_km=wa.actual_distance_km,
                actual_duration_sec=wa.actual_duration_sec,
                actual_pace_min_per_km=actual_pace,
                average_hr=wa.average_hr,
                hr_zones=hr_zones,
            )
        except Exception:
            score, comment = _score_match(workout, wa.actual_distance_km or 0, actual_pace)

        wa.match_score = score
        wa.match_comment = comment
        rescored += 1

    db.commit()
    return rescored
