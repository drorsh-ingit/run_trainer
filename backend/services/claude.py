import json
from datetime import date
from anthropic import Anthropic
from config import settings
from schemas import PlanCreateRequest, ClaudePlanResponse

_client = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


SYSTEM_PROMPT = """You are an expert running coach with certifications in exercise physiology and marathon coaching.

Your task is to generate a structured, realistic, week-by-week training plan.

You MUST respond with valid JSON only. No prose before or after. The JSON must match this exact schema:

{
  "summary": "<2-3 sentence plan overview>",
  "total_weeks": <integer>,
  "weeks": [
    {
      "week_number": <integer starting at 1>,
      "theme": "<short theme label, e.g. Base Building, Aerobic Development, Peak, Taper, Race Week>",
      "total_km": <float, sum of all distance_km values in this week>,
      "workouts": [
        {
          "day_of_week": "<Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday>",
          "type": "<easy|tempo|long_run|intervals|hill_repeats|strides|cross_training|rest|race>",
          "description": "<specific actionable instructions, 1-3 sentences>",
          "distance_km": <float or null for rest/cross-training>,
          "duration_minutes": <integer or null>,
          "is_optional": <true|false>
        }
      ]
    }
  ]
}

Coaching rules you MUST follow:
- Apply the 10% rule: increase total weekly km by no more than 10% week-over-week in build phases
- For plans longer than 8 weeks: insert a recovery week (volume reduced by 20-30%) every 4th week
- Taper: reduce volume by 20% the second-to-last week, 40% the final week before the race
- Race week: the last week ends on the goal_date — mark the race workout as type "race"
- Long runs on weekends unless runner specifies otherwise
- Rest days must appear at least once per week
- Optional workouts are clearly marked is_optional: true
- Do not schedule more workouts per week than runs_per_week + optional_runs_per_week (rest/cross-training days do not count against this limit)
- Descriptions must include pace guidance (e.g., "easy conversational pace", "tempo at lactate threshold — roughly 85-90% max HR")
"""


def _build_user_prompt(req: PlanCreateRequest, today: date) -> str:
    weeks_available = (req.goal_date - today).days // 7
    return f"""Generate a training plan for this runner:

GOAL RACE: {req.goal_distance_km} km on {req.goal_date} ({weeks_available} weeks from today, {today})
FITNESS LEVEL: {req.fitness_level}
CURRENT WEEKLY VOLUME: {req.current_weekly_km} km/week
RUNS PER WEEK: {req.runs_per_week} mandatory + {req.optional_runs_per_week} optional
TYPICAL SESSION DURATION: up to {req.typical_session_duration_minutes} minutes
PROGRESSION PREFERENCE: {req.gradualness_preference} (conservative = slower build, aggressive = faster build)
INJURIES / LIMITATIONS: {req.injuries if req.injuries else "None reported"}
ADDITIONAL NOTES: {req.additional_notes if req.additional_notes else "None"}

The plan must span exactly {weeks_available} weeks (total_weeks = {weeks_available}).
Week 1 starts from the current training base ({req.current_weekly_km} km/week).
The last workout in the final week must be the race itself on {req.goal_date} with type "race".
"""


def generate_plan(req: PlanCreateRequest) -> ClaudePlanResponse:
    client = _get_client()
    today = date.today()
    user_prompt = _build_user_prompt(req, today)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw: {raw_text[:500]}")

    return ClaudePlanResponse.model_validate(data)
