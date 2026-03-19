import base64
import hashlib
from datetime import date as date_type, timedelta

import garth
import requests as _req
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from config import settings
from models.models import GarminSession, PlannedWorkout, TrainingPlan, WorkoutActivity


# ── Token encryption ────────────────────────────────────────────────────────

def _make_fernet() -> Fernet:
    raw = hashlib.sha256(settings.secret_key.encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt_str(plaintext: str) -> str:
    return _make_fernet().encrypt(plaintext.encode()).decode()


def decrypt_str(ciphertext: str) -> str:
    try:
        return _make_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        raise ValueError("Garmin session expired — please reconnect your Garmin account")


# ── Garmin auth ──────────────────────────────────────────────────────────────

def _make_garth_client() -> garth.Client:
    """Return a garth Client, routing through a proxy if GARMIN_PROXY_URL is set."""
    if settings.garmin_proxy_url:
        import urllib.parse
        import urllib3
        import requests as _requests

        parsed = urllib.parse.urlparse(settings.garmin_proxy_url)
        _auth_header = urllib3.make_headers(
            proxy_basic_auth=f"{parsed.username}:{parsed.password}"
        )
        proxy_url = f"http://{parsed.hostname}:{parsed.port}"

        class _ProxyAdapter(_requests.adapters.HTTPAdapter):
            # requests calls this method to build headers for the CONNECT tunnel
            def proxy_headers(self, proxy):
                headers = super().proxy_headers(proxy)
                headers.update(_auth_header)
                return headers

        session = _requests.Session()
        session.proxies = {"http": proxy_url, "https": proxy_url}
        adapter = _ProxyAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return garth.Client(session=session)
    return garth.Client()


def authenticate_garmin(username: str, password: str) -> tuple[str, str]:
    """
    Authenticate with Garmin SSO via garth.
    Returns (token_dump, display_name).
    Raises ValueError on bad credentials.
    """
    import concurrent.futures
    client = _make_garth_client()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.login, username, password)
        try:
            future.result(timeout=40)
        except concurrent.futures.TimeoutError:
            raise ValueError(
                "Garmin authentication timed out. "
                "Garmin is likely blocking this server's IP. "
                "A residential proxy is required."
            )
        except Exception as e:
            raise ValueError(f"Garmin authentication failed: {e}") from e

    token_dump = client.dumps()
    try:
        profile = client.connectapi("/userprofile-service/socialProfile")
        display_name = profile.get("displayName") or username
    except Exception:
        display_name = username
    return token_dump, display_name


def build_garth_client(token_dump: str) -> garth.Client:
    client = _make_garth_client()
    client.loads(token_dump)
    return client


# ── Step / workout format conversion ────────────────────────────────────────

_STEP_TYPE = {
    "warmup":  {"stepTypeId": 1, "stepTypeKey": "warmup"},
    "active":  {"stepTypeId": 3, "stepTypeKey": "interval"},
    "rest":    {"stepTypeId": 4, "stepTypeKey": "recovery"},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
}

_DURATION_TYPE = {
    "TIME":     {"conditionTypeId": 2, "conditionTypeKey": "time"},
    "DISTANCE": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
}

_TARGET_TYPE = {
    "HEART_RATE_ZONE": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
    "PACE":            {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
    "OPEN":            {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
}


def _convert_step(step: dict, order: int) -> dict:
    target_type_key = step.get("target_type", "OPEN")
    result = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": _STEP_TYPE.get(step["step_type"], _STEP_TYPE["active"]),
        "endCondition": _DURATION_TYPE.get(step["duration_type"], _DURATION_TYPE["TIME"]),
        "endConditionValue": step["duration_value"],
        "targetType": _TARGET_TYPE.get(target_type_key, _TARGET_TYPE["OPEN"]),
    }
    if target_type_key == "HEART_RATE_ZONE":
        # Garmin expects a single zone number, not a BPM range
        # Use the lower zone number as the target
        result["zoneNumber"] = step.get("target_low", 1)
    elif target_type_key == "PACE":
        # Garmin pace zone expects speed in m/s, not sec/km
        # sec/km → m/s: speed = 1000 / sec_per_km
        low_sec = step.get("target_low")   # slower pace (higher sec/km)
        high_sec = step.get("target_high") # faster pace (lower sec/km)
        if low_sec and high_sec:
            result["targetValueOne"] = round(1000.0 / low_sec, 4)   # slow end (lower m/s)
            result["targetValueTwo"] = round(1000.0 / high_sec, 4)  # fast end (higher m/s)
    return result


def _fix_step_durations(raw_steps: list[dict], target_minutes: int | None) -> list[dict]:
    """Adjust the active TIME step(s) so total duration matches target_duration_minutes."""
    if not target_minutes or not raw_steps:
        return raw_steps
    target_secs = target_minutes * 60
    # Sum durations of non-active TIME steps (warmup, cooldown, recovery)
    fixed_secs = sum(
        s["duration_value"] for s in raw_steps
        if s.get("duration_type") == "TIME" and s.get("step_type") != "active"
    )
    # Find the single active TIME step (easy/long run has exactly one)
    active_time_steps = [s for s in raw_steps if s.get("step_type") == "active" and s.get("duration_type") == "TIME"]
    if len(active_time_steps) != 1:
        return raw_steps  # intervals or distance-based — don't touch
    remaining = target_secs - fixed_secs
    if remaining > 0:
        active_time_steps[0] = dict(active_time_steps[0], duration_value=remaining)
    # Rebuild list preserving order
    active_replaced = False
    result = []
    for s in raw_steps:
        if s.get("step_type") == "active" and s.get("duration_type") == "TIME" and not active_replaced:
            result.append(active_time_steps[0])
            active_replaced = True
        else:
            result.append(s)
    return result


def build_garmin_workout_payload(workout) -> dict:
    name = (
        f"Week {workout.week_number} – "
        f"{workout.workout_type.replace('_', ' ').title()} "
        f"({workout.scheduled_date})"
    )
    fixed_steps = _fix_step_durations(workout.steps or [], workout.target_duration_minutes)
    steps = [_convert_step(s, i + 1) for i, s in enumerate(fixed_steps)]
    sport_type = {"sportTypeId": 1, "sportTypeKey": "running"}
    return {
        "workoutName": name,
        "description": workout.description[:500] if workout.description else "",
        "sportType": sport_type,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport_type,
            "workoutSteps": steps,
        }],
    }


# ── Cleanup existing Garmin workouts ─────────────────────────────────────────

def delete_garmin_workouts_for_dates(client: garth.Client, dates: set[str]) -> int:
    """Delete all our previously-pushed workouts whose scheduled date is in `dates`.
    Workout names follow the pattern 'Week X – Type (YYYY-MM-DD)'.
    Returns the number of workouts deleted.
    """
    token = str(client.oauth2_token)
    headers = {"Authorization": token, "Content-Type": "application/json"}
    deleted = 0
    start = 0
    while True:
        r = _req.get(
            f"https://connectapi.garmin.com/workout-service/workouts?start={start}&limit=50&myWorkoutsOnly=true",
            headers=headers, timeout=30,
        )
        workouts = r.json() if r.ok else []
        if not workouts:
            break
        found_any = False
        for w in workouts:
            name: str = w.get("workoutName", "")
            if not name.startswith("Week "):
                continue
            # Extract date from "Week X – Type (YYYY-MM-DD)"
            import re
            m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", name)
            if not m or m.group(1) not in dates:
                continue
            found_any = True
            wid = w["workoutId"]
            # Remove calendar schedule(s) first
            r2 = _req.get(
                f"https://connectapi.garmin.com/workout-service/workouts/{wid}/scheduled",
                headers=headers, timeout=30,
            )
            for s in (r2.json() if r2.ok else []):
                _req.delete(
                    f"https://connectapi.garmin.com/workout-service/schedule/{s['workoutScheduleId']}",
                    headers=headers, timeout=30,
                )
            _req.delete(
                f"https://connectapi.garmin.com/workout-service/workout/{wid}",
                headers=headers, timeout=30,
            )
            deleted += 1
        if not found_any:
            start += 50
        if start > 500:
            break
    return deleted


# ── Push to Garmin Connect ───────────────────────────────────────────────────

_WORKOUT_URL = "/workout-service/workout"


def push_workout_to_garmin(client: garth.Client, payload: dict, scheduled_date: str | None = None) -> str:
    """Push one workout payload and optionally schedule it on the calendar.
    Returns the Garmin workout ID.
    """
    # garth uses a mobile User-Agent which causes Garmin to silently drop POSTs
    token = str(client.oauth2_token)
    headers = {"Authorization": token, "Content-Type": "application/json"}

    resp = _req.post(
        f"https://connectapi.garmin.com{_WORKOUT_URL}",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        data = data[0] if data else {}
    workout_id = str(data.get("workoutId", ""))

    if workout_id and scheduled_date:
        _req.post(
            f"https://connectapi.garmin.com/workout-service/schedule/{workout_id}",
            headers=headers,
            json={"date": str(scheduled_date)},
            timeout=30,
        )

    return workout_id


# ── Activity sync ─────────────────────────────────────────────────────────────

def build_garminconnect_client(token_dump: str):
    """Build a garminconnect.Garmin client from a stored token dump."""
    from garminconnect import Garmin
    import tempfile, os
    # garminconnect expects tokenstore as a directory path or token string
    api = Garmin()
    api.garth.loads(token_dump)
    api.display_name = ""
    return api


def fetch_activities_in_range(
    api, start: date_type, end: date_type
) -> list[dict]:
    """Return running activities from Garmin Connect between start and end (inclusive)."""
    try:
        activities = api.get_activities_by_date(
            startdate=start.isoformat(),
            enddate=end.isoformat(),
        )
        return activities if isinstance(activities, list) else []
    except Exception as e:
        err = str(e).lower()
        if "401" in err or "403" in err or "unauthorized" in err or "forbidden" in err:
            raise ValueError("Garmin session expired — please reconnect your Garmin account")
        raise


def fetch_activity_streams(api, activity_id: int) -> dict:
    """Return {heartrate: [...], velocity_smooth: [...]} time-series for one activity."""
    try:
        details = api.get_activity_details(activity_id)
    except Exception:
        return {}
    if not details:
        return {}

    idx = {d["key"]: d["metricsIndex"] for d in details.get("metricDescriptors", [])}
    hr_i = idx.get("directHeartRate")
    speed_i = idx.get("directSpeed")

    hr_stream: list[float] = []
    speed_stream: list[float] = []
    for chunk in details.get("activityDetailMetrics", []):
        for sample in chunk.get("metrics", []):
            if hr_i is not None and hr_i < len(sample) and sample[hr_i] is not None:
                hr_stream.append(sample[hr_i])
            if speed_i is not None and speed_i < len(sample) and sample[speed_i] is not None:
                speed_stream.append(sample[speed_i])

    result = {}
    if hr_stream:
        result["heartrate"] = hr_stream
    if speed_stream:
        result["velocity_smooth"] = speed_stream
    return result


def compute_hr_zones(hr_stream: list[float], max_hr: int = 185) -> list[int]:
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


MAX_DAYS_DIFF = 2


def _assign_activities_to_workouts(
    activities: list[dict], workouts: list
) -> dict[int, dict]:
    """
    Optimal greedy assignment of Garmin activities to planned workouts.
    - Only considers pairs where abs(days_diff) <= MAX_DAYS_DIFF
    - Cost = days_diff * 100 + km_diff  (date proximity is primary)
    - Processes cheapest pairs first; replaces weaker matches when a better one is found
    Returns {workout_id: activity_dict}
    """
    valid_pairs: list[tuple[float, int, int]] = []

    for ai, act in enumerate(activities):
        act_date_str = (act.get("startTimeLocal") or "")[:10]
        if not act_date_str:
            continue
        act_date = date_type.fromisoformat(act_date_str)
        actual_km = act.get("distance", 0) / 1000

        for wi, w in enumerate(workouts):
            if w.workout_type == "rest":
                continue
            days_diff = abs((w.scheduled_date - act_date).days)
            if days_diff > MAX_DAYS_DIFF:
                continue
            km_diff = abs((w.target_distance_km or actual_km) - actual_km)
            cost = days_diff * 100 + km_diff
            valid_pairs.append((cost, ai, wi))

    valid_pairs.sort()

    # assignment[wi] = (cost, ai);  reverse[ai] = (cost, wi)
    assignment: dict[int, tuple[float, int]] = {}
    reverse: dict[int, tuple[float, int]] = {}

    for cost, ai, wi in valid_pairs:
        cur_wi = reverse.get(ai)   # workout currently holding this activity
        cur_ai = assignment.get(wi)  # activity currently holding this workout

        if cur_wi is None and cur_ai is None:
            assignment[wi] = (cost, ai)
            reverse[ai] = (cost, wi)
        elif cur_wi is not None and cur_ai is None:
            # Activity already matched — reassign if this is cheaper
            if cost < cur_wi[0]:
                del assignment[cur_wi[1]]
                assignment[wi] = (cost, ai)
                reverse[ai] = (cost, wi)
        elif cur_wi is None and cur_ai is not None:
            # Workout already matched — reassign if this is cheaper
            if cost < cur_ai[0]:
                del reverse[cur_ai[1]]
                assignment[wi] = (cost, ai)
                reverse[ai] = (cost, wi)
        # Both already matched to better options — skip

    return {workouts[wi].id: activities[ai] for wi, (_, ai) in assignment.items()}


def sync_plan_activities(plan_id: int, user_id: int, db: Session) -> dict:
    """Fetch all Garmin running activities in the plan window and match to planned workouts."""
    garmin_session = db.query(GarminSession).filter(GarminSession.user_id == user_id).first()
    if not garmin_session:
        raise ValueError("Garmin not connected")

    token_dump = decrypt_str(garmin_session.token_dump_enc)
    api = build_garminconnect_client(token_dump)

    workouts = (
        db.query(PlannedWorkout)
        .filter(PlannedWorkout.plan_id == plan_id, PlannedWorkout.workout_type != "rest")
        .all()
    )
    if not workouts:
        return {"synced": 0, "skipped": 0, "errors": []}

    # Determine fetch window: plan_start-7 to plan_end+7
    all_dates = sorted(w.scheduled_date for w in workouts)
    fetch_start = all_dates[0] - timedelta(weeks=1)
    fetch_end = all_dates[-1] + timedelta(weeks=1)

    from datetime import datetime as dt
    all_activities = fetch_activities_in_range(api, fetch_start, fetch_end)

    # Filter to running activities only
    run_activities = [
        a for a in all_activities
        if any(k in (a.get("activityType", {}).get("typeKey", "")).lower()
               for k in ("running", "trail", "track"))
        and (a.get("startTimeLocal") or "")[:10]
    ]

    # Clear all previous activity records for this plan (matched and unmatched)
    db.query(WorkoutActivity).filter(WorkoutActivity.plan_id == plan_id).delete(synchronize_session=False)
    for w in workouts:
        w.completed = False
    db.flush()

    # Optimal assignment within 2-day window
    matched = _assign_activities_to_workouts(run_activities, workouts)
    # Build reverse map: activity index → workout_id
    matched_by_act_id = {act.get("activityId"): workout_id for workout_id, act in matched.items()}
    workout_map = {w.id: w for w in workouts}

    synced, errors = 0, []
    for act in run_activities:
        actual_km = act.get("distance", 0) / 1000
        activity_id = act.get("activityId")

        zone_secs = [act.get(f"hrTimeInZone_{i}") or 0.0 for i in range(1, 6)]
        total_z = sum(zone_secs)
        hr_zones = [round(z * 100 / total_z) for z in zone_secs] if total_z > 0 else None

        workout_id = matched_by_act_id.get(activity_id)
        row = WorkoutActivity(plan_id=plan_id, workout_id=workout_id)
        db.add(row)

        row.strava_activity_id = str(activity_id)
        row.name = act.get("activityName")
        row.actual_distance_km = round(actual_km, 2)
        row.actual_duration_sec = int(act.get("movingDuration") or act.get("duration") or 0)
        row.average_hr = act.get("averageHR")
        row.average_speed_ms = act.get("averageSpeed")
        row.streams_data = {"hr_zones": hr_zones} if hr_zones else None
        try:
            row.start_date = dt.fromisoformat(act["startTimeLocal"])
        except Exception:
            row.start_date = None

        if workout_id:
            workout_map[workout_id].completed = True
            synced += 1

    skipped = len(run_activities) - synced

    db.commit()

    # Persist refreshed token
    try:
        garmin_session.token_dump_enc = encrypt_str(api.garth.dumps())
        db.commit()
    except Exception:
        pass

    return {"synced": synced, "skipped": skipped, "errors": errors}
