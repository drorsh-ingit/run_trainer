"use client";

import { Suspense } from "react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import Nav from "../components/Nav";
import { apiFetch, useRequireAuth } from "../hooks/useAuth";

type ActivityEntry = {
  strava_activity_id: string;
  date: string;
  name: string | null;
  actual_distance_km: number | null;
  actual_duration_sec: number | null;
  average_hr: number | null;
  average_pace_min_per_km: number | null;
  hr_zones: number[] | null;
};

type WorkoutActivity = {
  strava_activity_id?: string;
  actual_distance_km: number | null;
  actual_duration_sec: number | null;
  average_hr: number | null;
  average_pace_min_per_km: number | null;
  hr_zones: number[] | null;
  match_score: number | null;
  match_comment: string | null;
};

type Workout = {
  id: number;
  week_number: number;
  day_of_week: string;
  scheduled_date: string;
  workout_type: string;
  description: string;
  target_distance_km: number | null;
  target_duration_minutes: number | null;
  is_optional: boolean;
  completed: boolean;
  activity: WorkoutActivity | null;
};

type Plan = {
  id: number;
  goal_distance: number | null;
  goal_date: string | null;
  plan_type: string | null;
  plan_data: { summary: string; total_weeks: number; weeks: unknown[] };
  workouts: Workout[];
};

function RaceDayMarker({ goalDate }: { goalDate: string }) {
  return (
    <div className="rounded-lg px-2 py-1.5 border-2 border-yellow-400 bg-gradient-to-br from-yellow-50 to-amber-100 text-yellow-900 shadow-sm">
      <div className="text-base leading-none mb-1">🏅</div>
      <div className="font-bold text-xs leading-tight">Race Day!</div>
      <div className="text-[10px] opacity-70 mt-0.5">{goalDate}</div>
    </div>
  );
}

const WORKOUT_COLORS: Record<string, string> = {
  easy: "bg-green-100 text-green-800",
  tempo: "bg-orange-100 text-orange-800",
  long_run: "bg-blue-100 text-blue-800",
  intervals: "bg-red-100 text-red-800",
  fartlek: "bg-rose-100 text-rose-800",
  hill_repeats: "bg-purple-100 text-purple-800",
  strides: "bg-yellow-100 text-yellow-800",
  cross_training: "bg-teal-100 text-teal-800",
  rest: "bg-gray-100 text-gray-500",
  race: "bg-pink-100 text-pink-800",
};

const WORKOUT_BORDER: Record<string, string> = {
  easy: "border-green-300",
  tempo: "border-orange-300",
  long_run: "border-blue-300",
  intervals: "border-red-300",
  fartlek: "border-rose-300",
  hill_repeats: "border-purple-300",
  strides: "border-yellow-300",
  cross_training: "border-teal-300",
  rest: "border-gray-200",
  race: "border-pink-400",
};

const TOOLTIP_COLORS: Record<string, string> = {
  easy: "bg-green-50 border-green-200 text-green-900",
  tempo: "bg-orange-50 border-orange-200 text-orange-900",
  long_run: "bg-blue-50 border-blue-200 text-blue-900",
  intervals: "bg-red-50 border-red-200 text-red-900",
  fartlek: "bg-rose-50 border-rose-200 text-rose-900",
  hill_repeats: "bg-purple-50 border-purple-200 text-purple-900",
  strides: "bg-yellow-50 border-yellow-200 text-yellow-900",
  cross_training: "bg-teal-50 border-teal-200 text-teal-900",
  rest: "bg-gray-50 border-gray-200 text-gray-700",
  race: "bg-pink-50 border-pink-300 text-pink-900",
};

function toDateKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function buildCalendarGrid(year: number, month: number): Date[] {
  const firstDay = new Date(year, month, 1);
  const dayOfWeek = firstDay.getDay(); // 0=Sun, 1=Mon, …, 6=Sat — Sun is column 0
  const startDate = new Date(firstDay);
  startDate.setDate(1 - dayOfWeek);
  const cells: Date[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(startDate);
    d.setDate(startDate.getDate() + i);
    cells.push(d);
  }
  return cells;
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function formatDuration(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function WorkoutTooltip({ workout, cellRef }: { workout: Workout; cellRef: React.RefObject<HTMLDivElement | null> }) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    const cell = cellRef.current;
    const tooltip = tooltipRef.current;
    if (!cell || !tooltip) return;

    const cellRect = cell.getBoundingClientRect();
    const tW = 256; // w-64
    const tH = tooltip.offsetHeight || 160;
    const vpW = window.innerWidth;
    const vpH = window.innerHeight;

    // Prefer below, fall back to above
    let top = cellRect.bottom + 6 + window.scrollY;
    if (cellRect.bottom + tH + 6 > vpH) {
      top = cellRect.top - tH - 6 + window.scrollY;
    }

    // Prefer right-aligned to cell left, clamp to viewport
    let left = cellRect.left + window.scrollX;
    if (left + tW > vpW - 8) left = vpW - tW - 8 + window.scrollX;
    if (left < 8) left = 8;

    setPos({ top, left });
  }, [cellRef]);

  const colorClass = TOOLTIP_COLORS[workout.workout_type] ?? "bg-white border-gray-200 text-gray-800";

  return (
    <div
      ref={tooltipRef}
      style={pos ? { position: "fixed", top: pos.top - window.scrollY, left: pos.left - window.scrollX, zIndex: 50 } : { visibility: "hidden", position: "fixed" }}
      className={`w-64 rounded-xl border shadow-lg p-3 text-xs pointer-events-none ${colorClass}`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="font-semibold text-sm capitalize">
          {workout.workout_type.replace(/_/g, " ")}
        </span>
        {workout.completed && (
          <span className="text-[10px] font-medium opacity-60">✓ done</span>
        )}
        {workout.is_optional && !workout.completed && (
          <span className="text-[10px] italic opacity-60">optional</span>
        )}
      </div>

      <div className="flex gap-3 mb-2 opacity-80">
        {workout.target_distance_km != null && (
          <span>{workout.target_distance_km} km planned</span>
        )}
        {workout.target_duration_minutes != null && (
          <span>{workout.target_duration_minutes} min planned</span>
        )}
        <span className="opacity-60">Week {workout.week_number}</span>
      </div>

      {workout.activity && (
        <div className="mb-2 pt-2 border-t border-current border-opacity-20 space-y-0.5 text-[11px]">
          <div className="font-semibold opacity-70 mb-1">Actual</div>
          {workout.activity.actual_distance_km != null && (
            <div>{workout.activity.actual_distance_km} km</div>
          )}
          {workout.activity.actual_duration_sec != null && (
            <div>{formatDuration(workout.activity.actual_duration_sec)} time</div>
          )}
          {workout.activity.average_pace_min_per_km != null && (
            <div>{Math.floor(workout.activity.average_pace_min_per_km)}:{String(Math.round((workout.activity.average_pace_min_per_km % 1) * 60)).padStart(2, "0")} /km avg pace</div>
          )}
          {workout.activity.average_hr != null && (
            <div>{Math.round(workout.activity.average_hr)} bpm avg HR</div>
          )}
          {workout.activity.match_comment && (
            <div className="mt-1 pt-1 border-t border-current border-opacity-20 italic opacity-80">{workout.activity.match_comment}</div>
          )}
        </div>
      )}

      {workout.description && (
        <p className="leading-relaxed opacity-90">{workout.description}</p>
      )}
    </div>
  );
}

function WorkoutCell({ workout, isCurrentMonth, onIgnore }: { workout: Workout; isCurrentMonth: boolean; onIgnore?: () => void }) {
  const [hovered, setHovered] = useState(false);
  const cellRef = useRef<HTMLDivElement>(null);
  const isRace = workout.workout_type === "race";

  if (isRace) {
    return (
      <div
        ref={cellRef}
        className="relative"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <div className={`rounded-lg px-2 py-1.5 border-2 border-yellow-400 bg-gradient-to-br from-yellow-50 to-amber-100 text-yellow-900 cursor-default transition-shadow ${!isCurrentMonth ? "opacity-50" : ""} ${hovered ? "shadow-lg" : "shadow-sm"}`}>
          <div className="text-base leading-none mb-1">🏅</div>
          <div className="font-bold text-xs leading-tight">Race Day!</div>
          {workout.target_distance_km != null && (
            <div className="text-[10px] font-semibold mt-0.5 opacity-80">{workout.target_distance_km} km</div>
          )}
        </div>
        {hovered && <WorkoutTooltip workout={workout} cellRef={cellRef} />}
      </div>
    );
  }

  return (
    <div
      ref={cellRef}
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        className={`rounded-md px-1.5 py-1 border text-xs cursor-default transition-shadow ${
          workout.completed
            ? "bg-green-50 border-green-300 text-green-800"
            : WORKOUT_COLORS[workout.workout_type] ?? "bg-gray-100 text-gray-600"
        } ${
          workout.completed ? "" : WORKOUT_BORDER[workout.workout_type] ?? "border-gray-200"
        } ${!isCurrentMonth ? "opacity-50" : ""} ${hovered ? "shadow-md" : ""}`}
      >
        <div className="font-semibold leading-tight capitalize flex items-center gap-1">
          {workout.completed && <span className="text-green-600 text-[10px]">✓</span>}
          {workout.workout_type.replace(/_/g, " ")}
        </div>
        <div className="flex gap-1.5 mt-0.5 flex-wrap">
          {workout.completed && workout.activity?.match_score != null && (
            <span className={`text-[10px] font-semibold ${
              workout.activity.match_score >= 90 ? "text-green-600" :
              workout.activity.match_score >= 70 ? "text-yellow-600" :
              "text-red-500"
            }`}>{workout.activity.match_score}%</span>
          )}
          {workout.completed && workout.activity?.actual_distance_km != null ? (
            <span className="text-[10px] font-medium text-green-700">
              {workout.activity.actual_distance_km} km
            </span>
          ) : workout.target_distance_km != null ? (
            <span className="text-[10px] opacity-75 font-medium">
              {workout.target_distance_km} km
            </span>
          ) : null}
          {!workout.completed && workout.target_duration_minutes != null && (
            <span className="text-[10px] opacity-60">
              {workout.target_duration_minutes} min
            </span>
          )}
        </div>
        {workout.is_optional && !workout.completed && (
          <div className="text-[10px] opacity-50 italic leading-tight mt-0.5">optional</div>
        )}
      </div>
      {hovered && <WorkoutTooltip workout={workout} cellRef={cellRef} />}
      {hovered && workout.completed && workout.activity?.strava_activity_id && onIgnore && (
        <button
          onMouseDown={(e) => { e.stopPropagation(); onIgnore(); }}
          className="absolute top-0.5 right-0.5 text-[10px] text-gray-400 hover:text-red-500 bg-white rounded px-0.5 leading-tight z-10"
          title="Ignore this activity"
        >✕</button>
      )}
    </div>
  );
}

function ActivityTooltip({ activity, cellRef }: { activity: ActivityEntry; cellRef: React.RefObject<HTMLDivElement | null> }) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const pace = activity.average_pace_min_per_km;

  useEffect(() => {
    const cell = cellRef.current;
    const tooltip = tooltipRef.current;
    if (!cell || !tooltip) return;
    const r = cell.getBoundingClientRect();
    const tH = tooltip.offsetHeight || 120;
    const vpW = window.innerWidth;
    const vpH = window.innerHeight;
    let top = r.bottom + 6 + window.scrollY;
    if (r.bottom + tH + 6 > vpH) top = r.top - tH - 6 + window.scrollY;
    let left = r.left + window.scrollX;
    if (left + 208 > vpW - 8) left = vpW - 208 - 8 + window.scrollX;
    setPos({ top, left });
  }, [cellRef]);

  return (
    <div
      ref={tooltipRef}
      style={pos ? { position: "fixed", top: pos.top - window.scrollY, left: pos.left - window.scrollX, zIndex: 50 } : { visibility: "hidden", position: "fixed" }}
      className="w-52 rounded-xl border border-teal-200 bg-teal-50 text-teal-900 shadow-lg p-3 text-xs pointer-events-none space-y-1"
    >
      <div className="font-semibold text-sm mb-1">{activity.name ?? "Run"}</div>
      {activity.actual_distance_km != null && <div>{activity.actual_distance_km} km</div>}
      {activity.actual_duration_sec != null && (
        <div>{formatDuration(activity.actual_duration_sec)} time</div>
      )}
      {pace != null && (
        <div>{Math.floor(pace)}:{String(Math.round((pace % 1) * 60)).padStart(2, "0")}/km avg pace</div>
      )}
      {activity.average_hr != null && <div>{Math.round(activity.average_hr)} bpm avg HR</div>}
    </div>
  );
}

function ActivityCell({ activity, isCurrentMonth, onIgnore }: { activity: ActivityEntry; isCurrentMonth: boolean; onIgnore?: () => void }) {
  const [hovered, setHovered] = useState(false);
  const cellRef = useRef<HTMLDivElement>(null);

  return (
    <div
      ref={cellRef}
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className={`rounded-md px-1.5 py-1 border text-xs cursor-default bg-teal-50 border-teal-300 text-teal-800 ${!isCurrentMonth ? "opacity-50" : ""} ${hovered ? "shadow-md" : ""}`}>
        <div className="font-semibold leading-tight">▶ run</div>
        {activity.actual_distance_km != null && (
          <div className="text-[10px] font-medium mt-0.5">{activity.actual_distance_km} km</div>
        )}
      </div>
      {hovered && <ActivityTooltip activity={activity} cellRef={cellRef} />}
      {hovered && onIgnore && (
        <button
          onMouseDown={(e) => { e.stopPropagation(); onIgnore(); }}
          className="absolute top-0.5 right-0.5 text-[10px] text-gray-400 hover:text-red-500 bg-white rounded px-0.5 leading-tight z-10"
          title="Ignore this activity"
        >✕</button>
      )}
    </div>
  );
}


function CalendarPage() {
  useRequireAuth();
  const searchParams = useSearchParams();
  const planId = searchParams.get("plan_id");

  const [plan, setPlan] = useState<Plan | null>(null);
  const [resolvedPlanId, setResolvedPlanId] = useState<string | null>(null);
  const [activities, setActivities] = useState<Map<string, ActivityEntry[]>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [currentMonth, setCurrentMonth] = useState<Date>(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const reloadActivities = async (id: string) => {
    const [planRes, actRes] = await Promise.all([
      apiFetch(`/plans/${id}`),
      apiFetch(`/plans/${id}/activities`),
    ]);
    if (planRes.ok) setPlan(await planRes.json());
    if (actRes.ok) {
      const acts: ActivityEntry[] = await actRes.json();
      const map = new Map<string, ActivityEntry[]>();
      for (const a of acts) {
        if (!map.has(a.date)) map.set(a.date, []);
        map.get(a.date)!.push(a);
      }
      setActivities(map);
    }
  };

  const handleIgnoreActivity = async (activityId: string) => {
    if (!resolvedPlanId) return;
    const res = await apiFetch(`/plans/${resolvedPlanId}/activities/${activityId}`, { method: "DELETE" });
    if (res.ok) reloadActivities(resolvedPlanId);
  };

  useEffect(() => {
    async function load() {
      try {
        let id = planId;
        if (!id) {
          const res = await apiFetch("/plans/");
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const plans: Plan[] = await res.json();
          if (plans.length === 0) throw new Error("No plans found");
          id = String(plans[0].id);
        }
        setResolvedPlanId(id);
        const res = await apiFetch(`/plans/${id}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: Plan = await res.json();
        setPlan(data);

        // Load synced activities (best-effort)
        const actRes = await apiFetch(`/plans/${id}/activities`);
        if (actRes.ok) {
          const acts: ActivityEntry[] = await actRes.json();
          const map = new Map<string, ActivityEntry[]>();
          for (const a of acts) {
            if (!map.has(a.date)) map.set(a.date, []);
            map.get(a.date)!.push(a);
          }
          setActivities(map);
        }

        if (data.workouts.length > 0) {
          const todayStr = toDateKey(new Date());
          const sorted = [...data.workouts].sort((a, b) =>
            a.scheduled_date.localeCompare(b.scheduled_date)
          );
          // Jump to the month of the closest upcoming workout; fall back to the last one
          const target = sorted.find(w => w.scheduled_date >= todayStr) ?? sorted[sorted.length - 1];
          const [y, m] = target.scheduled_date.split("-").map(Number);
          setCurrentMonth(new Date(y, m - 1, 1));
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to load plan");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [planId]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Nav />
        <div className="max-w-5xl mx-auto px-4 py-10">
          <p className="text-gray-500 text-sm">Loading…</p>
        </div>
      </div>
    );
  }

  if (error || !plan) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Nav />
        <div className="max-w-5xl mx-auto px-4 py-10">
          <p className="text-red-600 text-sm">{error || "Plan not found"}</p>
        </div>
      </div>
    );
  }

  const workoutsByDate = new Map<string, Workout>();
  for (const w of plan.workouts) {
    const existing = workoutsByDate.get(w.scheduled_date);
    if (!existing || w.workout_type === "race") {
      workoutsByDate.set(w.scheduled_date, w);
    }
  }

  const year = currentMonth.getFullYear();
  const month = currentMonth.getMonth();
  const cells = buildCalendarGrid(year, month);
  const todayKey = toDateKey(new Date());
  const isRacePlan = !plan.plan_type || plan.plan_type === "race";
  const goalDateKey = isRacePlan ? plan.goal_date : null;

  const prevMonth = () => setCurrentMonth(new Date(year, month - 1, 1));
  const nextMonth = () => setCurrentMonth(new Date(year, month + 1, 1));
  const jumpToRace = () => {
    if (!plan.goal_date) return;
    const [ry, rm] = plan.goal_date.split("-").map(Number);
    setCurrentMonth(new Date(ry, rm - 1, 1));
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <Nav />
      <div className="max-w-5xl mx-auto px-4 py-10 space-y-6">

        <Link
          href={`/plans/${plan.id}`}
          className="text-blue-500 hover:text-blue-700 text-sm transition-colors"
        >
          ← Back to Plan
        </Link>

        <div className="flex items-center justify-between">
          <button
            onClick={prevMonth}
            className="px-3 py-1.5 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-sm text-gray-600 transition-colors"
          >
            ← Prev
          </button>
          <h1 className="text-lg font-semibold text-gray-900">
            {MONTH_NAMES[month]} {year}
          </h1>
          <div className="flex items-center gap-2">
            {isRacePlan && plan.goal_date && (
              <button
                onClick={jumpToRace}
                className="px-3 py-1.5 rounded-lg border border-yellow-300 bg-yellow-50 hover:bg-yellow-100 text-sm font-medium text-yellow-800 transition-colors"
              >
                🏅 Jump to race day
              </button>
            )}
            <button
              onClick={nextMonth}
              className="px-3 py-1.5 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-sm text-gray-600 transition-colors"
            >
              Next →
            </button>
          </div>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
          {/* Day headers */}
          <div className="grid grid-cols-7 border-b border-gray-100">
            {DAY_NAMES.map(d => (
              <div key={d} className="py-2 text-center text-xs font-semibold text-gray-400 uppercase tracking-wide">
                {d}
              </div>
            ))}
          </div>

          {/* Cells */}
          <div className="grid grid-cols-7">
            {cells.map((date, i) => {
              const dateKey = toDateKey(date);
              const workout = workoutsByDate.get(dateKey);
              const dayActivities = activities.get(dateKey) ?? [];
              const isCurrentMonth = date.getMonth() === month;
              const isToday = dateKey === todayKey;
              const isGoalDate = dateKey === goalDateKey;

              return (
                <div
                  key={i}
                  className={`min-h-[104px] p-2 border-b border-r border-gray-100 flex flex-col gap-1.5 ${
                    i % 7 === 6 ? "border-r-0" : ""
                  } ${i >= 35 ? "border-b-0" : ""}`}
                >
                  <span
                    className={`text-xs font-medium self-start w-6 h-6 flex items-center justify-center rounded-full shrink-0 ${
                      isToday
                        ? "bg-blue-600 text-white ring-2 ring-blue-400 ring-offset-1"
                        : isCurrentMonth
                        ? "text-gray-700"
                        : "text-gray-300"
                    }`}
                  >
                    {date.getDate()}
                  </span>

                  {workout && (
                    <WorkoutCell
                      workout={workout}
                      isCurrentMonth={isCurrentMonth}
                      onIgnore={workout.activity?.strava_activity_id ? () => handleIgnoreActivity(workout.activity!.strava_activity_id!) : undefined}
                    />
                  )}
                  {!workout && isGoalDate && goalDateKey && (
                    <RaceDayMarker goalDate={goalDateKey} />
                  )}
                  {dayActivities.map((act, j) => (
                    <ActivityCell
                      key={j}
                      activity={act}
                      isCurrentMonth={isCurrentMonth}
                      onIgnore={() => handleIgnoreActivity(act.strava_activity_id)}
                    />
                  ))}
                </div>
              );
            })}
          </div>
        </div>

      </div>
    </div>
  );
}

export default function CalendarPageWrapper() {
  return (
    <Suspense>
      <CalendarPage />
    </Suspense>
  );
}
