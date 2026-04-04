"use client";

import { useEffect, useState } from "react";

type Step = { label: string; detail: string; duration: number };

const GENERATE_STEPS: Step[] = [
  { label: "Reading your profile",      detail: "Analyzing fitness level, schedule, and goals…",              duration: 8 },
  { label: "Planning training phases",  detail: "Structuring base building, speed work, taper, and race week…", duration: 18 },
  { label: "Building weekly volume",    detail: "Applying the 10% rule and inserting recovery weeks…",         duration: 18 },
  { label: "Writing workouts",          detail: "Scheduling daily sessions with pace, HR zones, and RPE…",     duration: 25 },
  { label: "Checking quality rules",    detail: "Ensuring no back-to-back hard sessions, rest days present…",  duration: 18 },
  { label: "Finalising your plan",      detail: "Adding goal-pace long runs and tapering details…",            duration: 999 },
];

const REVISE_STEPS: Step[] = [
  { label: "Reading your feedback",     detail: "Understanding what you'd like to change…",                   duration: 6 },
  { label: "Identifying affected weeks", detail: "Locating the sessions that need adjustment…",               duration: 14 },
  { label: "Revising workouts",         detail: "Rewriting affected sessions while keeping the rest intact…", duration: 20 },
  { label: "Re-checking the plan",      detail: "Validating volume, pacing, and recovery balance…",           duration: 999 },
];

const ASSESS_STEPS: Step[] = [
  { label: "Comparing planned vs actual", detail: "Reviewing distances, paces, HR zones, and missed sessions…",  duration: 10 },
  { label: "Identifying patterns",        detail: "Looking for under/over-training, consistency gaps…",           duration: 15 },
  { label: "Adjusting future weeks",      detail: "Revising workouts based on your actual performance…",          duration: 25 },
  { label: "Writing workout details",     detail: "Adding pace targets, HR zones, and RPE for each session…",     duration: 20 },
  { label: "Finalising revised plan",     detail: "Validating volume progression and recovery balance…",          duration: 999 },
];

function useTicker(steps: Step[], active: boolean) {
  const [stepIdx, setStepIdx] = useState(0);
  const [elapsed, setElapsed] = useState(0); // seconds since step start

  useEffect(() => {
    if (!active) { setStepIdx(0); setElapsed(0); return; }
    const tick = setInterval(() => {
      setElapsed(prev => {
        const next = prev + 1;
        const cap = steps[stepIdx]?.duration ?? 999;
        if (next >= cap && stepIdx < steps.length - 1) {
          setStepIdx(i => i + 1);
          return 0;
        }
        return next;
      });
    }, 1000);
    return () => clearInterval(tick);
  }, [active, stepIdx, steps]);

  // Reset when re-activated
  useEffect(() => {
    if (active) { setStepIdx(0); setElapsed(0); }
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  return { stepIdx, elapsed };
}

// Smooth progress bar that fills up to ~95% then holds
function useProgress(steps: Step[], stepIdx: number, elapsed: number) {
  const totalEstimate = steps.reduce((s, st) => s + (st.duration === 999 ? 20 : st.duration), 0);
  const doneTime = steps.slice(0, stepIdx).reduce((s, st) => s + (st.duration === 999 ? 20 : st.duration), 0);
  const raw = Math.min((doneTime + elapsed) / totalEstimate, 0.95);
  return Math.round(raw * 100);
}

interface Props {
  active: boolean;
  mode?: "generate" | "revise" | "assess";
}

export default function GeneratingProgress({ active, mode = "generate" }: Props) {
  const steps = mode === "assess" ? ASSESS_STEPS : mode === "revise" ? REVISE_STEPS : GENERATE_STEPS;
  const { stepIdx, elapsed } = useTicker(steps, active);
  // Guard against stale stepIdx from a previous mode before the reset effect fires
  const safeIdx = Math.min(stepIdx, steps.length - 1);
  const pct = useProgress(steps, safeIdx, elapsed);

  if (!active) return null;

  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50 p-5 space-y-4">
      {/* Progress bar */}
      <div className="h-1.5 w-full bg-blue-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-1000 ease-linear"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Current step */}
      <div className="flex items-start gap-3">
        <div className="mt-0.5 h-4 w-4 shrink-0 rounded-full border-2 border-blue-400 border-t-transparent animate-spin" />
        <div>
          <p className="text-sm font-medium text-blue-900">{steps[safeIdx].label}</p>
          <p className="text-xs text-blue-600 mt-0.5">{steps[safeIdx].detail}</p>
        </div>
      </div>

      {/* Step list */}
      <ol className="space-y-1.5 pl-1">
        {steps.map((step, i) => {
          const done = i < safeIdx;
          const current = i === safeIdx;
          return (
            <li key={i} className="flex items-center gap-2 text-xs">
              {done ? (
                <span className="text-blue-400">✓</span>
              ) : current ? (
                <span className="text-blue-500 font-bold">›</span>
              ) : (
                <span className="text-blue-200">·</span>
              )}
              <span className={done ? "text-blue-400" : current ? "text-blue-700 font-medium" : "text-blue-300"}>
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
