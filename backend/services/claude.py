import json
import re
from datetime import date
from anthropic import Anthropic
from config import settings
from schemas import PlanCreateRequest, ClaudePlanResponse, ChatMessage, CoachChatRequest


def _extract_json(text: str) -> str:
    """Extract the outermost JSON object from Claude's response, stripping any markdown or prose."""
    text = text.strip()
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the outermost { ... } by scanning for matching braces
    start = text.find('{')
    if start == -1:
        return text
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    # Fallback: return from first { to end
    return text[start:]

_client = None
_openai_client = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _call_model(model: str, system: str, messages: list[dict], max_tokens: int) -> str:
    """Unified call that routes to Anthropic or OpenAI based on the model name."""
    if model.startswith("gpt-"):
        from openai import BadRequestError, AuthenticationError
        client = _get_openai_client()
        openai_messages = [{"role": "system", "content": system}] + messages
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=min(max_tokens, 4096),
                messages=openai_messages,
            )
        except BadRequestError as e:
            raise ValueError(f"OpenAI request failed: {e}") from e
        except AuthenticationError as e:
            raise RuntimeError(f"OpenAI authentication failed: {e}") from e
        return response.choices[0].message.content
    else:
        client = _get_client()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return response.content[0].text


SYSTEM_PROMPT = """You are an expert running coach with certifications in exercise physiology and marathon coaching.

Your task is to generate a structured, realistic, week-by-week training plan organized into clearly named training stages.

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
          "type": "<easy|tempo|long_run|intervals|fartlek|hill_repeats|strides|cross_training|rest|race>",
          "description": "<specific actionable instructions, 1-3 sentences>",
          "distance_km": <float — MUST equal the sum of all segment distances in the description (e.g. 2km warmup + 4km tempo + 2km cooldown = 8km). null for rest/cross-training>,
          "duration_minutes": <integer or null>,
          "is_optional": <true|false>
        }
      ]
    }
  ]
}

## Training stages
Structure the plan into named stages. Use the week "theme" field to label the stage for every week. Typical stages:
1. Base Building — easy aerobic volume, no quality work yet
2. Aerobic Development — introduce strides and fartlek; build volume
3. Speed & Strength — intervals, hill repeats, tempo runs; volume plateaus
4. Race-Specific — marathon-pace long runs, goal-pace tempo blocks
5. Peak — highest volume week(s)
6. Taper — progressive volume reduction
7. Race Week — final sharpener + race

## Pace & heart rate requirements (MANDATORY for every workout description)
Every workout description MUST include ALL of the following where applicable:
- Target pace in min/km (e.g. "5:10–5:20 min/km")
- Heart rate zone or % max HR (e.g. "Zone 2, 65–75% max HR")
- RPE on a 1–10 scale (e.g. "RPE 4–5")
- For interval/fartlek sessions: full structure — e.g. "8 × 1 km at 4:20 min/km (Zone 4, 88–92% max HR, RPE 8), 90 sec jog recovery"
- For tempo runs: duration or distance at pace — e.g. "20 min continuous at 4:45 min/km (Zone 3–4, 82–88% max HR, RPE 7)"
- For easy/long runs: pace range and upper HR ceiling — e.g. "5:30–5:50 min/km, keep HR under 75% max HR, RPE 3–4"

## Speed work rules
- Introduce fartlek sessions in Stage 2 (Aerobic Development) to build speed awareness without high stress
- Add structured intervals (track or road) from Stage 3 onward: 400m, 800m, or 1 km repeats at 5K–10K pace
- Hill repeats count as strength-speed work; use them in Stage 2–3
- Strides (4–6 × 100m accelerations) can appear after easy runs from Stage 2 onward
- Never schedule two quality sessions (tempo/intervals/fartlek/hill repeats) on back-to-back days

## Volume & periodization rules
- Apply the 10% rule: increase total weekly km by no more than 10% week-over-week in build phases
- For plans longer than 8 weeks: insert a recovery week (volume reduced by 20-30%) every 4th week
- Taper: reduce volume by 20% two weeks out, 40% the final week before the race
- Race week: the last week ends on the goal_date — mark the race workout as type "race"
- Do NOT include a rest day on the race day itself — the race is the only workout that day
- Long runs on weekends unless runner specifies otherwise
- Rest days must appear at least once per week
- Optional workouts are clearly marked is_optional: true
- Respect the runner's schedule description exactly — honor preferred days, durations, and which runs are optional
"""


GENERAL_PLAN_SYSTEM_PROMPT = """You are an expert running coach with certifications in exercise physiology.

Your task is to generate a structured, progressive week-by-week general fitness training plan (no race target).

You MUST respond with valid JSON only. No prose before or after. The JSON must match this exact schema:

{
  "summary": "<2-3 sentence plan overview>",
  "total_weeks": <integer>,
  "weeks": [
    {
      "week_number": <integer starting at 1>,
      "theme": "<short theme label, e.g. Base Building, Aerobic Development, Strength, Maintenance>",
      "total_km": <float>,
      "workouts": [
        {
          "day_of_week": "<Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday>",
          "type": "<easy|tempo|long_run|intervals|fartlek|hill_repeats|strides|cross_training|rest>",
          "description": "<specific actionable instructions, 1-3 sentences>",
          "distance_km": <float or null>,
          "duration_minutes": <integer or null>,
          "is_optional": <true|false>
        }
      ]
    }
  ]
}

Organize into phases: Base Building → Aerobic Development → Speed & Strength → Maintenance.
Apply the 10% rule week-over-week. Insert a recovery week (−20%) every 4th week for plans > 8 weeks.
Every workout description MUST include pace (min/km), HR zone (% max HR), and RPE.
Never schedule two quality sessions back-to-back. Long runs on weekends unless specified.
Rest days at least once per week. No race or taper phases.
"""


def _build_user_prompt(req: PlanCreateRequest, today: date) -> str:
    weeks_available = (req.goal_date - today).days // 7
    return f"""Generate a training plan for this runner:

GOAL RACE: {req.goal_distance_km} km on {req.goal_date} ({weeks_available} weeks from today, {today})
FITNESS LEVEL: {req.fitness_level}
CURRENT WEEKLY VOLUME: {req.current_weekly_km} km/week
WEEKLY SCHEDULE: {req.schedule_description}
PROGRESSION PREFERENCE: {req.gradualness_preference} (conservative = slower build, aggressive = faster build)
GOAL TIME: {req.goal_time if req.goal_time else "Not specified — focus on finishing"}
INJURIES / LIMITATIONS: {req.injuries if req.injuries else "None reported"}
ADDITIONAL NOTES: {req.additional_notes if req.additional_notes else "None"}

The plan must span exactly {weeks_available} weeks (total_weeks = {weeks_available}).
Week 1 starts from the current training base ({req.current_weekly_km} km/week).
The last workout in the final week must be the race itself on {req.goal_date} with type "race".
"""


def _build_general_user_prompt(req: PlanCreateRequest) -> str:
    return f"""Generate a general running fitness plan for this runner:

FITNESS LEVEL: {req.fitness_level}
CURRENT WEEKLY VOLUME: {req.current_weekly_km} km/week
WEEKLY SCHEDULE: {req.schedule_description}
PLAN DURATION: {req.plan_duration_weeks} weeks
PROGRESSION PREFERENCE: {req.gradualness_preference}
INJURIES / LIMITATIONS: {req.injuries if req.injuries else "None reported"}
ADDITIONAL NOTES: {req.additional_notes if req.additional_notes else "None"}

The plan must span exactly {req.plan_duration_weeks} weeks (total_weeks = {req.plan_duration_weeks}).
Week 1 starts from the current training base ({req.current_weekly_km} km/week).
There is no race at the end — focus on building and maintaining fitness.
"""


def generate_plan(req: PlanCreateRequest, model: str = "claude-sonnet-4-6") -> ClaudePlanResponse:
    today = date.today()

    if req.plan_type == "general":
        user_prompt = _build_general_user_prompt(req)
        system = GENERAL_PLAN_SYSTEM_PROMPT
        weeks = req.plan_duration_weeks
    else:
        user_prompt = _build_user_prompt(req, today)
        system = SYSTEM_PROMPT
        weeks = (req.goal_date - today).days // 7

    max_tokens = min(max(weeks * 1800, 16000), 32000)

    raw_text = _extract_json(_call_model(model, system, [{"role": "user", "content": user_prompt}], max_tokens))

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw: {raw_text[:500]}")

    return ClaudePlanResponse.model_validate(data)


CHAT_SYSTEM_PROMPT = """You are an expert running coach helping a runner adjust their existing training plan.

CRITICAL: You MUST respond with ONLY valid JSON. Zero prose. Zero explanation outside JSON. If you output anything other than a raw JSON object, the system will break.

You are in a conversation with the runner. Your goal is to fully understand what they want before making any edits.

Respond in one of exactly two JSON formats:

Format 1 — when you need more information:
{"type": "question", "message": "<your clarifying question or follow-up>"}

Format 2 — when you have enough information to revise the plan:
{"type": "plan", "message": "<brief explanation of changes>", "plan": <complete revised plan JSON>}

## When to use Format 1 (question or conversational reply)
- The request is vague ("make it easier" — easier how?)
- You need specific weeks, dates, or durations
- You need to know constraints (travel, injury, events)
- The request could be interpreted in multiple ways
- The message is a GENERAL QUESTION unrelated to plan adjustments (e.g. about gear, nutrition, syncing with apps/watches, injury advice) — answer it helpfully and conversationally, do NOT modify the plan

## When to apply the change (respond with Format 2)
- The runner's intent is specific and unambiguous
- You have already gathered enough context
- The runner has confirmed or agreed to your proposed approach
- IMPORTANT: Once the runner agrees or confirms something (e.g. "yes that works", "Sunday, Tuesday, Thursday"), immediately output Format 2 with the full revised plan — do NOT ask more questions

## Plan schema (for Format 2 "plan" responses)
The "plan" field must be a COMPLETE plan — never a diff or partial plan:
{
  "summary": "<2-3 sentence overview>",
  "total_weeks": <int>,
  "weeks": [
    {
      "week_number": <int>,
      "theme": "<theme>",
      "total_km": <float>,
      "workouts": [
        {
          "day_of_week": "<Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday>",
          "type": "<easy|tempo|long_run|intervals|fartlek|hill_repeats|strides|cross_training|rest|race>",
          "description": "<instructions with pace min/km, HR zone, RPE>",
          "distance_km": <float or null>,
          "duration_minutes": <int or null>,
          "is_optional": <true|false>
        }
      ]
    }
  ]
}

Keep unchanged weeks exactly as they are. Only modify what the runner asked to change.
"""


def chat_plan_revision(
    existing_plan: dict,
    req: PlanCreateRequest,
    history: list[ChatMessage],
    message: str,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    Multi-turn conversational plan adjustment.
    Returns {"type": "question", "message": "..."} or
            {"type": "plan", "message": "...", "plan": ClaudePlanResponse}.
    """
    # Strip descriptions to reduce token count — the model only needs structure for reasoning
    def _slim_plan(plan: dict) -> dict:
        slim_weeks = []
        for week in plan.get("weeks", []):
            slim_workouts = [
                {k: w[k] for k in ("day_of_week", "type", "distance_km", "duration_minutes", "is_optional") if k in w}
                for w in week.get("workouts", [])
            ]
            slim_weeks.append({
                "week_number": week.get("week_number"),
                "theme": week.get("theme"),
                "total_km": week.get("total_km"),
                "workouts": slim_workouts,
            })
        return {"summary": plan.get("summary", ""), "total_weeks": plan.get("total_weeks"), "weeks": slim_weeks}

    plan_context = (
        f"Current training plan:\n{json.dumps(_slim_plan(existing_plan))}\n\n"
        f"Runner context:\n"
        f"- Goal: {req.goal_distance_km} km on {req.goal_date}\n"
        f"- Schedule: {req.schedule_description}\n"
        f"- Injuries: {req.injuries or 'None'}\n"
        f"- Notes: {req.additional_notes or 'None'}"
    )

    messages = []
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": message})

    weeks = len(existing_plan.get("weeks", []))
    max_tokens = min(max(weeks * 1800, 16000), 32000)

    raw_text = _extract_json(_call_model(model, CHAT_SYSTEM_PROMPT + "\n\n" + plan_context, messages, max_tokens))

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw: {raw_text[:500]}")

    if data.get("type") == "plan":
        data["plan"] = ClaudePlanResponse.model_validate(data["plan"])

    return data


COACHING_QA_SYSTEM = """You are an expert running coach having an initial consultation with a new athlete.

Your current role: ask thoughtful questions to understand the runner deeply before building their training plan.

You MUST respond with valid JSON only, in one of two formats:

Format 1 — you have more questions:
{"type": "question", "message": "<your message with 2-4 questions>"}

Format 2 — you have enough information to build the plan:
{"type": "ready", "message": "<brief encouraging message, e.g. 'Perfect, I have everything I need — let me build your plan!'>"}

Guidelines:
- First message: warmly acknowledge the runner's goal, then ask your first 3-4 most important questions
- Follow-up messages: ask 1-3 focused follow-up questions based on their answers
- Ask about: recent race times, training history and injury context, lifestyle constraints (work/family/sleep), mental approach to hard training, specific weaknesses they want to address
- Declare ready after 1-2 exchanges (once you have meaningful context beyond the profile data)
- Be warm, specific, and coach-like — not robotic
"""


COACHING_BUILD_SYSTEM = """You are an expert running coach building a training plan based on a coaching consultation.

You MUST respond with valid JSON only. No prose before or after. The JSON must match this exact schema:

{
  "summary": "<3-4 sentence overview covering all four phases and their purpose>",
  "total_weeks": <integer — weeks from today to race day plus 3 recovery weeks>,
  "weeks": [
    {
      "week_number": <integer starting at 1>,
      "theme": "<phase prefix + specific label, e.g. 'Initial: Aerobic Base', 'Progression: Interval Introduction', 'Taper: Final Sharpener', 'Recovery: Active Recovery'>",
      "total_km": <float>,
      "workouts": [
        {
          "day_of_week": "<Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday>",
          "type": "<easy|tempo|long_run|intervals|fartlek|hill_repeats|strides|cross_training|rest|race>",
          "description": "<specific instructions including pace min/km, HR zone, RPE 1-10>",
          "distance_km": <float or null>,
          "duration_minutes": <integer or null>,
          "is_optional": <true|false>
        }
      ]
    }
  ]
}

## Four mandatory phases (use these exact theme prefixes)

Phase 1 — Initial (theme prefix "Initial:"):
Build aerobic endurance, consistency, and injury prevention. Easy paces only. No quality work.
Use athlete's current base as week 1 starting point.

Phase 2 — Progression (theme prefix "Progression:"):
Add intervals, tempo runs, hill workouts. Gradually increase long run distance.
Apply 10% weekly volume rule. Insert a recovery week (−20%) every 4th week.

Phase 3 — Taper (theme prefix "Taper:"):
Reduce volume 20% two weeks out, 40% the week before the race. Maintain intensity but cut volume.
Final week ends on goal_date with a "race" type workout. Do NOT add a rest day on the race day — the race is the only workout that day.

Phase 4 — Recovery (theme prefix "Recovery:"):
3 weeks post-race. Week 1: very easy walking/light jogging, mobility. Week 2: gentle easy runs.
Week 3: return to comfortable running. No intensity.

## Other rules
- Every workout description MUST include pace (min/km), HR zone (% max HR), and RPE
- Never schedule two quality sessions back-to-back
- Long runs on weekends unless runner specifies otherwise
- Rest days at least once per week
- Respect the runner's schedule and preferred days exactly
"""


def start_coaching_session(req: PlanCreateRequest, model: str = "claude-sonnet-4-6") -> dict:
    """
    Sends runner profile to Claude and gets the opening questions.
    Returns {"type": "question", "message": "..."}
    """
    today = date.today()
    weeks_available = (req.goal_date - today).days // 7 if req.goal_date else None

    profile_parts = [
        f"Name: {req.runner_name}" if req.runner_name else None,
        f"Gender: {req.gender}" if req.gender else None,
        f"Age: {req.age}" if req.age else None,
        f"Height: {req.height_cm} cm" if req.height_cm else None,
        f"Weight: {req.weight_kg} kg" if req.weight_kg else None,
        (f"Goal race: {req.goal_race_name or f'{req.goal_distance_km} km'} on {req.goal_date} ({weeks_available} weeks away)" if req.goal_date
         else f"General fitness plan: {req.plan_duration_weeks} weeks"),
        f"Current weekly volume: {req.current_weekly_km} km/week",
        f"Fitness level: {req.fitness_level}",
        f"Preferred schedule: {req.schedule_description}" if req.schedule_description else None,
        f"Goal time: {req.goal_time}" if req.goal_time else None,
        f"Injuries / limitations: {req.injuries}" if req.injuries else None,
        f"Progression preference: {req.gradualness_preference}",
        f"Additional notes: {req.additional_notes}" if req.additional_notes else None,
    ]
    profile = "\n".join(f"- {p}" for p in profile_parts if p)

    name = req.runner_name or "there"
    race_label = req.goal_race_name or f"{req.goal_distance_km} km race" if req.goal_date else "general fitness"

    goal_desc = f"{race_label} on {req.goal_date}" if req.goal_date else f"a {req.plan_duration_weeks}-week general fitness plan"
    intro = (
        f"Dear coach, I'm {name} and I want you to act as my expert running coach. "
        f"Your mission is to get me in the best shape possible to achieve my next goal, "
        f"{goal_desc}. "
        f"Before we build my training, I want you to fully understand my background, habits, and context.\n\n"
        f"Here is my profile:\n{profile}\n\n"
        f"Before we start building my plan, please ask me 5-10 questions that will help you "
        f"perform your mission to the best of your abilities."
    )

    raw = _extract_json(_call_model(model, COACHING_QA_SYSTEM, [{"role": "user", "content": intro}], 1024))

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "question", "message": raw}


def continue_coaching_chat(history: list[ChatMessage], message: str, model: str = "claude-sonnet-4-6") -> dict:
    """
    Continue the Q&A coaching conversation.
    Returns {"type": "question", "message": "..."} or {"type": "ready", "message": "..."}
    """
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": message})

    raw = _extract_json(_call_model(model, COACHING_QA_SYSTEM, messages, 1024))

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "question", "message": raw}


def build_coached_plan(req: PlanCreateRequest, history: list[ChatMessage], model: str = "claude-sonnet-4-6") -> ClaudePlanResponse:
    today = date.today()
    if req.goal_date:
        weeks_available = (req.goal_date - today).days // 7
        total_weeks = weeks_available + 3  # include 3 recovery weeks post-race
    else:
        weeks_available = req.plan_duration_weeks
        total_weeks = req.plan_duration_weeks

    # Build context from conversation
    convo = "\n".join(
        f"{'Runner' if m.role == 'user' else 'Coach'}: {m.content}"
        for m in history
    )

    name = req.runner_name or "the runner"
    race_label = req.goal_race_name or f"{req.goal_distance_km} km race" if req.goal_date else "general fitness"

    if req.goal_date:
        build_prompt = (
            f"Great, thanks for the consultation. Now please build a complete training plan.\n\n"
            f"Here is our conversation:\n{convo}\n\n"
            f"Runner profile:\n"
            f"- Goal: {race_label} on {req.goal_date} ({weeks_available} weeks away)\n"
            f"- Current weekly volume: {req.current_weekly_km} km/week\n"
            f"- Fitness level: {req.fitness_level}\n"
            f"- Schedule: {req.schedule_description}\n"
            f"- Progression preference: {req.gradualness_preference}\n"
            f"- Goal time: {req.goal_time or 'Not specified'}\n"
            f"- Injuries: {req.injuries or 'None'}\n\n"
            f"Now please organize this running plan into four clear phases leading up to my race:\n\n"
            f"Initial phase: build aerobic endurance, consistency, and injury prevention.\n"
            f"Progression phase: add intervals, tempo runs, hill workouts, and gradually increase long run distance.\n"
            f"Taper phase: reduce mileage and intensity to arrive fresh while maintaining sharpness.\n"
            f"Recovery phase: post-race recovery plan with reduced load, mobility, and gentle return to running.\n\n"
            f"Please outline weekly mileage progression and key sessions for each phase and explain the main purpose of the phase.\n\n"
            f"The plan must span exactly {weeks_available} weeks to race day (total_weeks = {total_weeks} including 3 recovery weeks).\n"
            f"Week 1 starts from {req.current_weekly_km} km/week base.\n"
            f"The last workout in week {weeks_available} must be the race on {req.goal_date} with type 'race'.\n"
            f"Weeks {weeks_available + 1} to {total_weeks} are the Recovery phase.\n"
        )
    else:
        build_prompt = (
            f"Great, thanks for the consultation. Now please build a complete training plan.\n\n"
            f"Here is our conversation:\n{convo}\n\n"
            f"Runner profile:\n"
            f"- Goal: {req.plan_duration_weeks}-week general fitness plan\n"
            f"- Current weekly volume: {req.current_weekly_km} km/week\n"
            f"- Fitness level: {req.fitness_level}\n"
            f"- Schedule: {req.schedule_description}\n"
            f"- Progression preference: {req.gradualness_preference}\n"
            f"- Injuries: {req.injuries or 'None'}\n\n"
            f"The plan must span exactly {req.plan_duration_weeks} weeks (total_weeks = {req.plan_duration_weeks}).\n"
            f"Week 1 starts from {req.current_weekly_km} km/week base.\n"
        )

    max_tokens = min(max(total_weeks * 1800, 16000), 32000)

    raw = _extract_json(_call_model(model, COACHING_BUILD_SYSTEM, [{"role": "user", "content": build_prompt}], max_tokens))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw: {raw[:500]}")

    return ClaudePlanResponse.model_validate(data)


ASSESS_QA_PROMPT = """You are an expert running coach reviewing a runner's actual training execution against their planned workouts.

CRITICAL: You MUST respond with ONLY valid JSON. Zero prose outside JSON.

You are in a conversation with the runner. Your job is to:
1. Analyze how well they've been following the plan (distance, pace, HR, consistency)
2. Identify patterns: missed workouts, under/over-training, HR compliance issues
3. Ask 1-3 clarifying questions if needed (injuries? illness? life events?)
4. When you have enough context, declare you're ready to suggest changes

Respond in one of exactly two JSON formats:

Format 1 — when you need more information or want to share analysis first:
{"type": "question", "message": "<your analysis and/or clarifying questions>"}

Format 2 — when you have enough context and are ready to build a revised plan:
{"type": "ready", "message": "<brief summary of what you plan to change and why>"}

## Conversation flow
- First message: Share a concise analysis of planned vs actual performance, highlight key patterns (good and bad), then ask 1-3 targeted questions about any gaps or anomalies you noticed
- Follow-up messages: Refine your understanding based on the runner's answers
- Declare ready after 1-2 exchanges once you have meaningful context
- Be warm, specific, and coach-like — not robotic
"""

ASSESS_BUILD_PROMPT = """You are an expert running coach revising a runner's training plan based on their actual performance data.

You MUST respond with valid JSON only. No prose before or after. The JSON must match this exact schema:

{
  "summary": "<2-3 sentence overview of what changed>",
  "total_weeks": <number of future weeks included>,
  "weeks": [
    {
      "week_number": <MUST match original week numbering — do NOT renumber>,
      "theme": "<theme>",
      "total_km": <float>,
      "workouts": [
        {
          "day_of_week": "<Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday>",
          "type": "<easy|tempo|long_run|intervals|fartlek|hill_repeats|strides|cross_training|rest|race>",
          "description": "<instructions with pace min/km, HR zone, RPE>",
          "distance_km": <float or null>,
          "duration_minutes": <int or null>,
          "is_optional": <true|false>
        }
      ]
    }
  ]
}

## Key rules
- ONLY output the future weeks. Past weeks are locked.
- Preserve original week_number values exactly — do NOT renumber
- Preserve the overall plan goals and race date
- Adjustments should be evidence-based: if the runner is consistently running slower/faster than planned, adjust paces. If they're missing volume, scale back. If they're exceeding expectations, consider progression.
- Keep the plan's structure and weekly schedule pattern unless the runner requests otherwise
- Every workout description MUST include pace (min/km), HR zone (% max HR), and RPE
"""


def _build_comparison_context(workouts_with_activities: list, plan_data: dict, unmatched_activities: list = None) -> str:
    """Build a concise comparison of planned vs actual for past/current workouts."""
    today = date.today()
    lines = ["## Planned vs Actual Performance\n"]

    # Group workouts by week
    weeks: dict[int, list] = {}
    for w in workouts_with_activities:
        # Include past workouts and today's workouts that have a synced activity
        if w.scheduled_date > today or (w.scheduled_date == today and not w.activity):
            continue
        weeks.setdefault(w.week_number, []).append(w)

    if not weeks and not unmatched_activities:
        return "No completed weeks to analyze yet."

    for week_num in sorted(weeks.keys()):
        week_workouts = weeks[week_num]
        # Find theme from plan_data
        theme = ""
        for pw in plan_data.get("weeks", []):
            if pw.get("week_number") == week_num:
                theme = pw.get("theme", "")
                break

        planned_km = sum(w.target_distance_km or 0 for w in week_workouts)
        actual_km = 0
        completed = 0
        total = 0

        week_lines = []
        for w in sorted(week_workouts, key=lambda x: x.scheduled_date):
            if w.workout_type == "rest":
                continue
            total += 1
            act = w.activity
            if act:
                completed += 1
                actual_km += act.actual_distance_km or 0
                pace = None
                if act.average_speed_ms and act.average_speed_ms > 0:
                    pace = round(1000 / (act.average_speed_ms * 60), 2)
                pace_str = f"{int(pace)}:{int((pace % 1) * 60):02d}/km" if pace else "?"
                score_str = f"score:{act.match_score}" if act.match_score is not None else ""
                week_lines.append(
                    f"  {w.day_of_week} {w.workout_type}: "
                    f"planned {w.target_distance_km or '?'}km → actual {act.actual_distance_km or '?'}km, "
                    f"pace {pace_str}, HR {round(act.average_hr) if act.average_hr else '?'}bpm "
                    f"{score_str}"
                )
                if act.match_comment:
                    week_lines.append(f"    Coach note: {act.match_comment}")
            else:
                week_lines.append(
                    f"  {w.day_of_week} {w.workout_type}: "
                    f"planned {w.target_distance_km or '?'}km → MISSED"
                )

        completion = f"{completed}/{total}" if total else "0/0"
        lines.append(f"Week {week_num} ({theme}): {planned_km:.1f}km planned → {actual_km:.1f}km actual, {completion} completed")
        lines.extend(week_lines)
        lines.append("")

    # Unmatched activities — runs done that didn't match any planned workout
    if unmatched_activities:
        lines.append("## Additional runs (not matching any planned workout)\n")
        for act in sorted(unmatched_activities, key=lambda a: a.start_date or a.synced_at):
            pace = None
            if act.average_speed_ms and act.average_speed_ms > 0:
                pace = round(1000 / (act.average_speed_ms * 60), 2)
            pace_str = f"{int(pace)}:{int((pace % 1) * 60):02d}/km" if pace else "?"
            act_date = (act.start_date.strftime("%Y-%m-%d") if act.start_date else "?")
            lines.append(
                f"  {act_date}: {act.name or 'Run'} — "
                f"{act.actual_distance_km or '?'}km, "
                f"pace {pace_str}, HR {round(act.average_hr) if act.average_hr else '?'}bpm"
            )
        lines.append("")

    return "\n".join(lines)


def _extract_future_weeks(plan_data: dict, today: date = None) -> dict:
    """Extract future weeks from plan_data based on approximate week dates."""
    if today is None:
        today = date.today()
    future_weeks = []
    for week in plan_data.get("weeks", []):
        future_weeks.append(week)
    return {
        "summary": plan_data.get("summary", ""),
        "total_weeks": len(future_weeks),
        "weeks": future_weeks,
    }


def assess_plan_chat(
    comparison_data: str,
    future_plan: dict,
    plan_context: str,
    history: list[ChatMessage],
    message: str,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    Q&A phase of plan assessment.
    Returns {"type": "question", "message": "..."} or
            {"type": "ready", "message": "..."}.
    """
    context = (
        f"{comparison_data}\n\n"
        f"## Future plan (weeks to potentially adjust):\n{json.dumps(future_plan)}\n\n"
        f"## Runner context:\n{plan_context}"
    )

    messages = []
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": message})

    raw_response = _call_model(model, ASSESS_QA_PROMPT + "\n\n" + context, messages, 2048)
    raw_text = _extract_json(raw_response)

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"type": "question", "message": raw_response.strip()}


def assess_build_plan(
    comparison_data: str,
    future_plan: dict,
    plan_context: str,
    history: list[ChatMessage],
    model: str = "claude-sonnet-4-6",
) -> ClaudePlanResponse:
    """
    Build the revised future plan based on the assessment conversation.
    Returns a ClaudePlanResponse with only future weeks.
    """
    convo = "\n".join(
        f"{'Runner' if m.role == 'user' else 'Coach'}: {m.content}"
        for m in history
    )

    build_prompt = (
        f"Based on this coaching assessment conversation, build a revised plan for the remaining weeks.\n\n"
        f"## Assessment conversation:\n{convo}\n\n"
        f"{comparison_data}\n\n"
        f"## Current future plan (to revise):\n{json.dumps(future_plan)}\n\n"
        f"## Runner context:\n{plan_context}"
    )

    future_week_count = len(future_plan.get("weeks", []))
    max_tokens = min(max(future_week_count * 1800, 16000), 32000)

    raw_text = _extract_json(_call_model(model, ASSESS_BUILD_PROMPT, [{"role": "user", "content": build_prompt}], max_tokens))

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw: {raw_text[:500]}")

    return ClaudePlanResponse.model_validate(data)


STEPS_SYSTEM = """You are a running coach generating Garmin structured workout steps.

You MUST respond with a single JSON object only. No prose. Keys are workout IDs (as strings), values are arrays of steps.

Schema:
{
  "<workout_id>": [
    {
      "step_type": "<warmup|active|rest|cooldown>",
      "duration_type": "<TIME|DISTANCE>",
      "duration_value": <int — seconds for TIME, meters for DISTANCE>,
      "target_type": "<HEART_RATE_ZONE|PACE|OPEN>",
      "target_low": <int or null>,
      "target_high": <int or null>
    }
  ]
}

Rules:
- rest and cross_training workouts: empty array []
- Flatten repeats (e.g. "8 × 1 km"): N active + (N-1) recovery steps interleaved, no nesting
- Active intervals at distance: duration_type=DISTANCE (meters), target_type=PACE (sec/km, e.g. 4:20=260)
- Active steps at time for structured workouts (tempo, intervals, hill_repeats, fartlek, strides): target_type=PACE
- Active steps for easy/long runs: duration_type=DISTANCE (meters, use distance_km * 1000), target_type=HEART_RATE_ZONE (use the HR zone from the description, target_low=zone number 1-5)
- Recovery between intervals: duration_type=TIME, target_type=OPEN, target_low=null, target_high=null
- Warmup/cooldown: ONLY add them for workouts with meaningful intensity variation (tempo, intervals,
  hill repeats, fartlek). For easy and long runs at uniform effort, use a SINGLE active step for
  the full duration — no warmup or cooldown.
- When warmup/cooldown ARE included: target_type=HEART_RATE_ZONE, target_low=1, target_high=2.
  Use duration_type=DISTANCE (meters) if the description specifies a warmup/cooldown distance (e.g. "2 km warmup" → 2000m).
  Use duration_type=TIME (seconds) if only time is specified or implied.
- CRITICAL: all step duration_values (in seconds) must sum exactly to duration_minutes * 60.
  For single-step workouts: one active step with duration_value = duration_minutes * 60.
  For multi-step: warmup ~5-10% + active (remainder) + cooldown ~5-10%.
"""


def generate_steps_for_workouts(workouts: list[dict]) -> dict[int, list[dict]]:
    """
    Generate Garmin steps for a batch of workouts.
    workouts: list of {"id": int, "type": str, "description": str}
    Returns: {workout_id: [step, ...]}
    """
    client = _get_client()

    batch_prompt = "Generate steps for these workouts:\n" + json.dumps(workouts, indent=2)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=min(len(workouts) * 400 + 1000, 16000),
        system=STEPS_SYSTEM,
        messages=[{"role": "user", "content": batch_prompt}],
    )

    raw = _extract_json(response.content[0].text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid steps JSON: {e}\nRaw: {raw[:500]}")

    return {int(k): v for k, v in data.items()}


_MATCH_SYSTEM = """You are a running coach analyzing how well an athlete executed a planned workout.

IMPORTANT: respond with ONLY a JSON object. No prose, no analysis, no markdown. Just the JSON:
{"score": <integer 0-100>, "comment": "<1-3 sentences of coaching feedback>"}

Scoring guidelines:
- Consider distance completion, pace appropriateness for the workout type, HR zone alignment, and duration

Workout-type rules (apply strictly):
- Easy / recovery runs: HR compliance is the PRIMARY factor (weight ~60% of score). Must stay in Z1-Z2. HR zone distribution matters far more than pace — going too fast is only a minor negative, but sustained Z3+ HR should weigh heavily on the score.
- Long runs (or easy-paced segments within a long run): Same HR emphasis as easy runs — aerobic control (Z2, occasional Z3 at most) is the dominant criterion. Distance completion matters too but never at the expense of HR discipline.
- Tempo / threshold runs: Pace consistency and hitting target distance matter most. Do NOT penalize for running faster than the target pace — reward it slightly if HR and distance were on point. Penalize only erratic pacing or falling well short of distance.
- Intervals / strides / speed work: Effort level and completing the prescribed reps/distance matter most. Do NOT penalize for exceeding target pace — faster is fine. Penalize only if effort was too low or the session was cut short.
- Race-pace runs: Treat like tempo — consistency and distance first, no penalty for being fast.

Score bands:
- 90-100: excellent execution
- 75-89: good, minor deviations
- 60-74: acceptable, some issues
- 40-59: significant deviation from plan
- <40: major issues

When "Planned steps" and "Strava laps" are provided, perform LAP-BY-LAP analysis:
- Align each Strava lap to its corresponding planned step (by order, matching warmup/active/rest/cooldown progression)
- For interval workouts: compare each active lap's pace against that step's target pace range
- For rest/recovery laps: verify the runner slowed down (don't penalize exact pace)
- Warmup/cooldown laps: check they were in an appropriate easy zone
- A runner who hits all interval targets but has a slow overall average should score HIGH
- If laps are present but planned steps are not, use the laps to assess effort distribution and consistency
- If only 1 lap exists (auto-lap or no manual laps), fall back to overall stats analysis

Comment tone: concise, supportive, specific to the data. Mention actual numbers."""


def generate_match_analysis(
    workout_type: str,
    description: str,
    target_distance_km: float | None,
    target_duration_min: int | None,
    actual_distance_km: float,
    actual_duration_sec: int | None,
    actual_pace_min_per_km: float | None,
    average_hr: float | None,
    hr_zones: list[int] | None,
    elevation_gain: float | None = None,
    elevation_loss: float | None = None,
    laps: list[dict] | None = None,
    planned_steps: list[dict] | None = None,
) -> tuple[int, str]:
    """Use Claude to score an activity against its planned workout."""
    client = _get_client()

    def fmt_pace(p):
        if p is None:
            return "unknown"
        return f"{int(p)}:{int((p % 1) * 60):02d} min/km"

    def fmt_zones(z):
        if not z:
            return "unknown"
        labels = ["Z1", "Z2", "Z3", "Z4", "Z5"]
        return ", ".join(f"{l}:{v}%" for l, v in zip(labels, z))

    actual_duration_min = round(actual_duration_sec / 60) if actual_duration_sec else None

    prompt = f"""Planned workout:
- Type: {workout_type}
- Description: {description}
- Target distance: {target_distance_km or "not specified"} km
- Target duration: {target_duration_min or "not specified"} min

Actual activity:
- Distance: {actual_distance_km} km
- Duration: {actual_duration_min or "unknown"} min
- Pace: {fmt_pace(actual_pace_min_per_km)}
- Avg HR: {round(average_hr) if average_hr else "unknown"} bpm
- HR zones: {fmt_zones(hr_zones)}
- Elevation: {f"+{elevation_gain}m / -{elevation_loss}m" if elevation_gain is not None else "unknown"}"""

    if planned_steps:
        steps_lines = []
        for i, s in enumerate(planned_steps):
            dur = s.get("duration_value", "?")
            dur_type = s.get("duration_type", "?")
            dur_display = f"{dur}s" if dur_type == "TIME" else f"{dur}m"
            tgt = s.get("target_type", "OPEN")
            tgt_detail = ""
            if tgt == "PACE" and s.get("target_low"):
                lo = s["target_low"]
                hi = s.get("target_high") or lo
                tgt_detail = f" target {lo // 60}:{lo % 60:02d}-{hi // 60}:{hi % 60:02d} min/km"
            elif tgt == "HEART_RATE_ZONE" and s.get("target_low"):
                tgt_detail = f" target HR zone {s['target_low']}-{s.get('target_high', s['target_low'])}"
            steps_lines.append(
                f"  {i+1}. {s.get('step_type', '?')} | {dur_display} | {tgt}{tgt_detail}"
            )
        prompt += "\n\nPlanned steps:\n" + "\n".join(steps_lines)

    if laps and len(laps) > 1:
        lap_lines = []
        for lap in laps:
            spd = lap.get("average_speed")
            pace_str = f"{int(1000/spd/60)}:{int(1000/spd%60):02d}" if spd and spd > 0 else "?"
            dist_km = round(lap.get("distance", 0) / 1000, 2)
            hr = round(lap["average_heartrate"]) if lap.get("average_heartrate") else "?"
            elev_str = ""
            eg = lap.get("elevation_gain")
            el = lap.get("elevation_loss")
            if eg is not None or el is not None:
                parts = []
                if eg is not None: parts.append(f"+{eg}m")
                if el is not None: parts.append(f"-{el}m")
                elev_str = f", elev {'/'.join(parts)}"
            zones_str = ""
            if lap.get("hr_zones"):
                z = lap["hr_zones"]
                zone_labels = ["Z1", "Z2", "Z3", "Z4", "Z5"]
                zones_str = ", zones " + "/".join(f"{l}:{v}%" for l, v in zip(zone_labels, z))
            lap_lines.append(
                f"  Lap {lap.get('lap_index', '?')}: {dist_km}km, "
                f"pace {pace_str}/km, HR {hr}bpm, "
                f"time {lap.get('moving_time', '?')}s{elev_str}{zones_str}"
            )
        prompt += "\n\nStrava laps:\n" + "\n".join(lap_lines)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=_MATCH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        data = json.loads(_extract_json(response.content[0].text))
        return int(data["score"]), str(data["comment"])
    except Exception:
        return 75, f"Completed {actual_distance_km} km."
