import requests as _req
from services.garmin import encrypt_str, decrypt_str, _fix_step_durations

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
        return f"{m // 1000}km" if m >= 1000 and m % 1000 == 0 else f"{m}mtr"
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
