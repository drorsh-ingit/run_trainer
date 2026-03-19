import time
import urllib.parse
from datetime import datetime, timezone, date as date_type

import httpx
from sqlalchemy.orm import Session

from config import settings
from models.models import PlannedWorkout, StravaToken, WorkoutActivity

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


def fetch_hr_zones(access_token: str, activity_id: str) -> list[int] | None:
    """Fetch HR zone distribution from Strava and return [z1%, z2%, z3%, z4%, z5%]."""
    resp = httpx.get(
        f"{STRAVA_API}/activities/{activity_id}/zones",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    print(f"[zones] activity={activity_id} status={resp.status_code} body={resp.text[:500]}")
    if resp.status_code in (404, 400):
        return None
    resp.raise_for_status()
    zones_data = resp.json()
    hr_zone = next((z for z in zones_data if z.get("type") == "heartrate"), None)
    if not hr_zone:
        return None
    buckets = hr_zone.get("distribution_buckets", [])
    if not buckets:
        return None
    times = [b.get("time", 0) for b in buckets]
    total = sum(times)
    if total == 0:
        return None
    return [round(t * 100 / total) for t in times]


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

    synced, skipped, errors = 0, 0, []
    for act in activities:
        if act.get("type") not in ("Run", "VirtualRun", "TrailRun"):
            skipped += 1
            continue

        act_date = act["start_date_local"][:10]
        candidates = workout_by_date.get(act_date, [])
        if not candidates:
            skipped += 1
            continue

        workout = _best_match(candidates, act["distance"] / 1000)
        if workout is None:
            skipped += 1
            continue

        # Fetch streams and HR zones (best-effort)
        streams = {}
        try:
            streams = fetch_streams(access_token, str(act["id"]))
        except Exception as e:
            errors.append(f"Streams fetch failed for activity {act['id']}: {e}")
        try:
            hr_zones = fetch_hr_zones(access_token, str(act["id"]))
            if hr_zones:
                streams["hr_zones"] = hr_zones
        except Exception as e:
            errors.append(f"HR zones fetch failed for activity {act['id']}: {e}")

        existing = db.query(WorkoutActivity).filter(WorkoutActivity.workout_id == workout.id).first()
        if not existing:
            existing = WorkoutActivity(workout_id=workout.id, plan_id=plan_id)
            db.add(existing)

        existing.strava_activity_id = str(act["id"])
        existing.name = act.get("name")
        existing.actual_distance_km = round(act["distance"] / 1000, 2)
        existing.actual_duration_sec = act.get("moving_time")
        existing.average_hr = act.get("average_heartrate")
        existing.average_speed_ms = act.get("average_speed")
        existing.start_date = datetime.fromisoformat(act["start_date_local"])
        existing.streams_data = streams or None

        workout.completed = True
        workout.strava_activity_id = str(act["id"])
        synced += 1

    db.commit()
    return {"synced": synced, "skipped": skipped, "errors": errors}
