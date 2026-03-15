"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Nav from "../../components/Nav";
import GeneratingProgress from "../../components/GeneratingProgress";
import { apiFetch, useRequireAuth } from "../../hooks/useAuth";

const FITNESS_LEVELS = ["beginner", "intermediate", "advanced"];
const GRADUALNESS = ["conservative", "moderate", "aggressive"];
const GENDERS = ["", "male", "female", "other"];

const AI_MODELS = [
  { value: "claude-opus-4-6",           label: "Claude Opus 4.6",   badge: "Most capable",  provider: "Anthropic" },
  { value: "claude-sonnet-4-6",         label: "Claude Sonnet 4.6", badge: "Balanced",       provider: "Anthropic" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5",  badge: "Fast & cheap",   provider: "Anthropic" },
  { value: "gpt-4o",                    label: "GPT-4o",            badge: "Most capable",   provider: "OpenAI"    },
  { value: "gpt-4o-mini",               label: "GPT-4o mini",       badge: "Balanced",       provider: "OpenAI"    },
  { value: "gpt-3.5-turbo",             label: "GPT-3.5 Turbo",     badge: "Fast & cheap",   provider: "OpenAI"    },
];

const DAY_OFFSETS: Record<string, number> = {
  Monday: 0, Tuesday: 1, Wednesday: 2, Thursday: 3,
  Friday: 4, Saturday: 5, Sunday: 6,
};

function computeWorkoutDate(weekNumber: number, dayOfWeek: string, goalDate: string): string {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dow = today.getDay(); // 0=Sun, 1=Mon...
  const daysUntilMonday = dow === 1 ? 0 : (8 - dow) % 7;
  const week1Start = new Date(today);
  week1Start.setDate(today.getDate() + daysUntilMonday);
  const weekStart = new Date(week1Start);
  weekStart.setDate(week1Start.getDate() + (weekNumber - 1) * 7);
  const offset = DAY_OFFSETS[dayOfWeek] ?? 0;
  const d = new Date(weekStart);
  d.setDate(weekStart.getDate() + offset);
  return d.toISOString().slice(0, 10);
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

type Workout = {
  day_of_week: string;
  type: string;
  description: string;
  distance_km: number | null;
  duration_minutes: number | null;
  is_optional: boolean;
};

type Week = {
  week_number: number;
  theme: string;
  total_km: number;
  workouts: Workout[];
};

type PreviewPlan = {
  summary: string;
  total_weeks: number;
  weeks: Week[];
};

type ChatMsg = { role: "user" | "assistant"; content: string; isPlanUpdate?: boolean };

type FormState = {
  plan_type: "" | "race" | "general";
  goal_race_name: string;
  goal_distance_km: string;
  goal_date: string;
  plan_duration_weeks: string;
  gender: string;
  age: string;
  height_cm: string;
  weight_kg: string;
  schedule_description: string;
  current_weekly_km: string;
  fitness_level: string;
  injuries: string;
  gradualness_preference: string;
  goal_time: string;
  additional_notes: string;
  ai_model: string;
};

function formToPayload(form: FormState) {
  const isRace = form.plan_type === "race";
  return {
    plan_type: form.plan_type,
    goal_race_name: isRace ? form.goal_race_name : "",
    goal_distance_km: isRace ? parseFloat(form.goal_distance_km) : null,
    goal_date: isRace ? form.goal_date : null,
    plan_duration_weeks: !isRace ? parseInt(form.plan_duration_weeks) : null,
    gender: form.gender,
    age: form.age ? parseInt(form.age) : null,
    height_cm: form.height_cm ? parseFloat(form.height_cm) : null,
    weight_kg: form.weight_kg ? parseFloat(form.weight_kg) : null,
    schedule_description: form.schedule_description,
    current_weekly_km: parseFloat(form.current_weekly_km),
    fitness_level: form.fitness_level,
    injuries: form.injuries,
    gradualness_preference: form.gradualness_preference,
    goal_time: isRace ? (form.goal_time || null) : null,
    additional_notes: form.additional_notes,
    ai_model: form.ai_model,
  };
}

export default function NewPlanPage() {
  useRequireAuth();
  const router = useRouter();

  const [stage, setStage] = useState<"form" | "questions" | "building" | "preview">("form");
  const [form, setForm] = useState<FormState>({
    plan_type: "",
    goal_race_name: "",
    goal_distance_km: "42.2",
    goal_date: "",
    plan_duration_weeks: "12",
    gender: "",
    age: "",
    height_cm: "",
    weight_kg: "",
    schedule_description: "",
    current_weekly_km: "0",
    fitness_level: "intermediate",
    injuries: "",
    gradualness_preference: "moderate",
    goal_time: "",
    additional_notes: "",
    ai_model: "claude-sonnet-4-6",
  });

  // Q&A chat state
  const [qaMessages, setQaMessages] = useState<ChatMsg[]>([]);
  const [qaInput, setQaInput] = useState("");
  const [qaLoading, setQaLoading] = useState(false);
  const [qaError, setQaError] = useState("");
  const [coachReady, setCoachReady] = useState(false);
  const qaBottomRef = useRef<HTMLDivElement>(null);

  // Preview + revision chat state
  const [preview, setPreview] = useState<PreviewPlan | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState("");
  const chatBottomRef = useRef<HTMLDivElement>(null);

  // Loading / error
  const [startLoading, setStartLoading] = useState(false);
  const [startError, setStartError] = useState<{ message: string; raw: string } | null>(null);
  const [building, setBuilding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => { qaBottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [qaMessages, qaLoading]);
  useEffect(() => { chatBottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatMessages, chatLoading]);

  // ── Stage 1: start coaching session ───────────────────────────────────────
  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    setStartLoading(true);
    setStartError(null);

    try {
      const res = await apiFetch("/plans/coach/start", {
        method: "POST",
        body: JSON.stringify(formToPayload(form)),
      });

      if (!res.ok) {
        const rawText = await res.text();
        let message = `HTTP ${res.status} ${res.statusText}`;
        try {
          const data = JSON.parse(rawText);
          if (typeof data.detail === "string") message = data.detail;
        } catch {}
        setStartError({ message, raw: rawText });
        return;
      }

      const data = await res.json();
      setQaMessages([{ role: "assistant", content: data.message }]);
      if (data.type === "ready") setCoachReady(true);
      setStage("questions");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Something went wrong";
      setStartError({ message, raw: message });
    } finally {
      setStartLoading(false);
    }
  };

  // ── Stage 2: Q&A replies ───────────────────────────────────────────────────
  const handleQaReply = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = qaInput.trim();
    if (!text || qaLoading) return;

    const userMsg: ChatMsg = { role: "user", content: text };
    const nextMessages = [...qaMessages, userMsg];
    setQaMessages(nextMessages);
    setQaInput("");
    setQaLoading(true);
    setQaError("");

    try {
      const history = qaMessages.map(m => ({ role: m.role, content: m.content }));
      const res = await apiFetch("/plans/coach/reply", {
        method: "POST",
        body: JSON.stringify({ ...formToPayload(form), message: text, history }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setQaError(data.detail ?? `HTTP ${res.status}`);
        return;
      }

      const data = await res.json();
      setQaMessages(prev => [...prev, { role: "assistant", content: data.message }]);
      if (data.type === "ready") setCoachReady(true);
    } catch (err: unknown) {
      setQaError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setQaLoading(false);
    }
  };

  // ── Stage 3: build plan ────────────────────────────────────────────────────
  const handleBuild = async () => {
    setBuilding(true);
    setStage("building");

    try {
      const history = qaMessages.map(m => ({ role: m.role, content: m.content }));
      const res = await apiFetch("/plans/coach/build", {
        method: "POST",
        body: JSON.stringify({ ...formToPayload(form), message: "", history }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setStartError({ message: data.detail ?? `HTTP ${res.status}`, raw: JSON.stringify(data) });
        setStage("questions");
        return;
      }

      setPreview(await res.json());
      setStage("preview");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Something went wrong";
      setStartError({ message, raw: message });
      setStage("questions");
    } finally {
      setBuilding(false);
    }
  };

  // ── Stage 4: preview chat ──────────────────────────────────────────────────
  const handleChatSend = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = chatInput.trim();
    if (!text || chatLoading || !preview) return;

    const userMsg: ChatMsg = { role: "user", content: text };
    const nextMessages = [...chatMessages, userMsg];
    setChatMessages(nextMessages);
    setChatInput("");
    setChatLoading(true);
    setChatError("");

    try {
      const history = chatMessages.map(m => ({ role: m.role, content: m.content }));
      const res = await apiFetch("/plans/preview/chat", {
        method: "POST",
        body: JSON.stringify({
          current_plan: preview,
          message: text,
          history,
          plan_type: form.plan_type,
          goal_distance_km: form.plan_type === "race" ? parseFloat(form.goal_distance_km) : null,
          goal_date: form.plan_type === "race" ? form.goal_date : null,
          plan_duration_weeks: form.plan_type === "general" ? parseInt(form.plan_duration_weeks) : null,
          schedule_description: form.schedule_description,
          injuries: form.injuries,
          additional_notes: form.additional_notes,
          ai_model: form.ai_model,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setChatError(data.detail ?? `HTTP ${res.status}`);
        return;
      }

      const data = await res.json();
      if (data.type === "question") {
        setChatMessages(prev => [...prev, { role: "assistant", content: data.message }]);
      } else {
        setPreview(data.plan);
        setChatMessages(prev => [...prev, { role: "assistant", content: data.message, isPlanUpdate: true }]);
      }
    } catch (err: unknown) {
      setChatError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setChatLoading(false);
    }
  };

  // ── Stage 4b: save ─────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!preview) return;
    setSaving(true);
    setSaveError("");

    try {
      const res = await apiFetch("/plans/save-preview", {
        method: "POST",
        body: JSON.stringify({ ...formToPayload(form), generated_plan: preview }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSaveError(data.detail ?? `HTTP ${res.status}`);
        return;
      }

      const saved = await res.json();
      router.push(`/plans/${saved.id}`);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSaving(false);
    }
  };

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-50">
      <Nav />
      <div className="max-w-4xl mx-auto px-4 py-10">

        {/* ── Stage 1: form ── */}
        {stage === "form" && (
          <>
            <h1 className="text-2xl font-bold text-gray-900 mb-2">New Training Plan</h1>
            <p className="text-gray-500 text-sm mb-8">
              Tell us about yourself and your goal — your coach will ask a few questions before building your plan.
            </p>

            <form onSubmit={handleStart} className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 space-y-5">

              {/* Plan type */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">What kind of plan do you need?</label>
                <select
                  required
                  value={form.plan_type}
                  onChange={e => setForm({ ...form, plan_type: e.target.value as FormState["plan_type"] })}
                  className={`w-1/2 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${form.plan_type === "" ? "text-gray-400" : "text-gray-900"}`}
                >
                  <option value="" disabled>Select a plan type…</option>
                  <option value="race">Race target — training plan leading to a specific race</option>
                  <option value="general">General fitness — open-ended plan to build &amp; maintain fitness</option>
                </select>
              </div>

              {/* Rest of form — only shown once a plan type is chosen */}
              {form.plan_type !== "" && (<>

              {/* Runner info */}
              {form.plan_type === "race" && (
                <div className="max-w-xs">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Goal race name <span className="text-gray-400 font-normal">(optional)</span></label>
                  <input
                    type="text"
                    value={form.goal_race_name}
                    onChange={e => setForm({ ...form, goal_race_name: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="e.g. Berlin Marathon"
                  />
                </div>
              )}

              {form.plan_type === "race" ? (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Goal distance (km)</label>
                    <input
                      type="number" step="0.1" required
                      value={form.goal_distance_km}
                      onChange={e => setForm({ ...form, goal_distance_km: e.target.value })}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Goal date</label>
                    <input
                      type="date" required
                      value={form.goal_date}
                      onChange={e => setForm({ ...form, goal_date: e.target.value })}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Plan duration (weeks)</label>
                    <select
                      required
                      value={form.plan_duration_weeks}
                      onChange={e => setForm({ ...form, plan_duration_weeks: e.target.value })}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      {[4, 6, 8, 10, 12, 16, 20, 24].map(w => (
                        <option key={w} value={w}>{w} weeks</option>
                      ))}
                    </select>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-4 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Gender <span className="text-gray-400 font-normal">(optional)</span></label>
                  <select
                    value={form.gender}
                    onChange={e => setForm({ ...form, gender: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">Select…</option>
                    {GENDERS.filter(g => g).map(g => <option key={g} value={g}>{g}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Age <span className="text-gray-400 font-normal">(optional)</span></label>
                  <input
                    type="number" step="1" min="10" max="100"
                    value={form.age}
                    onChange={e => setForm({ ...form, age: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="e.g. 35"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Height (cm) <span className="text-gray-400 font-normal">(optional)</span></label>
                  <input
                    type="number" step="1"
                    value={form.height_cm}
                    onChange={e => setForm({ ...form, height_cm: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="e.g. 175"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Weight (kg) <span className="text-gray-400 font-normal">(optional)</span></label>
                  <input
                    type="number" step="0.1"
                    value={form.weight_kg}
                    onChange={e => setForm({ ...form, weight_kg: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="e.g. 70"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Weekly schedule</label>
                <textarea
                  required rows={3}
                  value={form.schedule_description}
                  onChange={e => setForm({ ...form, schedule_description: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder='e.g. "4 runs/week, one optional, 3 weekday runs up to 1 hour, long run on Saturdays"'
                />
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Current weekly km</label>
                  <input
                    type="number" step="1" required
                    value={form.current_weekly_km}
                    onChange={e => setForm({ ...form, current_weekly_km: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Fitness level</label>
                  <select
                    value={form.fitness_level}
                    onChange={e => setForm({ ...form, fitness_level: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {FITNESS_LEVELS.map(l => <option key={l}>{l}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Progression</label>
                  <select
                    value={form.gradualness_preference}
                    onChange={e => setForm({ ...form, gradualness_preference: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {GRADUALNESS.map(g => <option key={g}>{g}</option>)}
                  </select>
                </div>
              </div>

              {form.plan_type === "race" && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Goal time <span className="text-gray-400 font-normal">(optional)</span></label>
                  <input
                    type="text"
                    value={form.goal_time}
                    onChange={e => setForm({ ...form, goal_time: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder='e.g. "sub-4 hours", "3:30", "just finish"'
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Injuries / limitations</label>
                <input
                  type="text"
                  value={form.injuries}
                  onChange={e => setForm({ ...form, injuries: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="e.g. left knee pain, no high-impact for first 4 weeks"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Additional notes</label>
                <input
                  type="text"
                  value={form.additional_notes}
                  onChange={e => setForm({ ...form, additional_notes: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Anything else your coach should know"
                />
              </div>

              </>)} {/* end plan_type conditional */}

              {form.plan_type !== "" && (
                <div className="space-y-2">
                  <button
                    type="submit"
                    disabled={startLoading}
                    className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-medium py-2.5 rounded-lg text-sm transition-colors"
                  >
                    {startLoading ? "Connecting with your coach…" : "Meet Your Coach →"}
                  </button>
                  <div className="flex items-center gap-2">
                    <label className="text-xs text-gray-400 shrink-0">AI model</label>
                    <select
                      value={form.ai_model}
                      onChange={e => setForm({ ...form, ai_model: e.target.value })}
                      className="border border-gray-200 rounded-lg px-2 py-1 text-xs text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      {AI_MODELS.map(m => (
                        <option key={m.value} value={m.value}>
                          {m.label} — {m.badge} · {m.provider}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              )}

              {startError && (
                <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
                  <p className="font-medium">{startError.message}</p>
                </div>
              )}
            </form>
          </>
        )}

        {/* ── Stage 2: Q&A with coach ── */}
        {stage === "questions" && (
          <>
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-2xl font-bold text-gray-900">Coach Consultation</h1>
                <p className="text-gray-500 text-sm mt-1">Answer your coach&apos;s questions — they&apos;ll build your plan when ready.</p>
              </div>
              <button onClick={() => setStage("form")} className="text-sm text-gray-400 hover:text-gray-700">← Back</button>
            </div>

            <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden mb-4">
              <div className="px-6 py-4 space-y-3 min-h-[300px] max-h-[70vh] overflow-y-auto">
                {qaMessages.map((msg, i) => (
                  <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap ${
                      msg.role === "user"
                        ? "bg-blue-600 text-white rounded-br-sm"
                        : "bg-gray-100 text-gray-800 rounded-bl-sm"
                    }`}>
                      {msg.content}
                    </div>
                  </div>
                ))}
                {qaLoading && (
                  <div className="flex justify-start">
                    <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-3">
                      <div className="flex gap-1 items-center h-4">
                        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
                        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
                        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
                      </div>
                    </div>
                  </div>
                )}
                <div ref={qaBottomRef} />
              </div>

              {!coachReady && (
                <div className="px-4 pb-4 border-t border-gray-100 pt-3">
                  {qaError && <p className="text-xs text-red-500 mb-2 px-2">{qaError}</p>}
                  <form onSubmit={handleQaReply} className="flex gap-2 items-end">
                    <textarea
                      rows={3}
                      value={qaInput}
                      onChange={e => setQaInput(e.target.value)}
                      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleQaReply(e as unknown as React.FormEvent); } }}
                      disabled={qaLoading}
                      placeholder="Type your answer… (Shift+Enter for new line)"
                      className="flex-1 border border-gray-300 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 resize-none"
                    />
                    <button
                      type="submit"
                      disabled={qaLoading || !qaInput.trim()}
                      className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium px-4 py-2 rounded-xl text-sm transition-colors"
                    >
                      Send
                    </button>
                  </form>
                </div>
              )}
            </div>

            {coachReady && (
              <div className="flex items-center gap-4">
                <button
                  onClick={handleBuild}
                  className="bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 px-8 rounded-xl text-sm transition-colors"
                >
                  Build My Plan →
                </button>
                {startError && <p className="text-sm text-red-600">{startError.message}</p>}
              </div>
            )}
          </>
        )}

        {/* ── Stage 3: building ── */}
        {stage === "building" && (
          <div className="max-w-lg mx-auto py-16">
            <h1 className="text-2xl font-bold text-gray-900 mb-2 text-center">Building Your Plan</h1>
            <p className="text-gray-500 text-sm text-center mb-8">Your coach is crafting a personalised plan…</p>
            <GeneratingProgress active={building} mode="generate" />
          </div>
        )}

        {/* ── Stage 4: preview ── */}
        {stage === "preview" && preview && (
          <>
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-2xl font-bold text-gray-900">Your Plan</h1>
                <p className="text-gray-500 text-sm mt-1">Chat with your coach to refine it, then save when ready.</p>
              </div>
              <button onClick={() => setStage("questions")} className="text-sm text-gray-400 hover:text-gray-700">← Back to questions</button>
            </div>

            <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 mb-6">
              <p className="text-gray-700 text-sm">{preview.summary}</p>
              <p className="text-gray-400 text-xs mt-1">{preview.total_weeks} weeks total</p>
            </div>

            <div className="space-y-4 mb-6">
              {preview.weeks.map(week => (
                <div key={week.week_number} className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Week {week.week_number}</span>
                      <h3 className="text-sm font-semibold text-gray-900">{week.theme}</h3>
                    </div>
                    <span className="text-sm text-gray-400">{week.total_km} km</span>
                  </div>
                  <div className="space-y-2.5">
                    {week.workouts.map((w, i) => (
                      <div key={i} className="flex gap-3 items-start">
                        <span className="text-xs text-gray-400 w-20 pt-0.5 shrink-0">{w.day_of_week}</span>
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${WORKOUT_COLORS[w.type] ?? "bg-gray-100 text-gray-600"}`}>
                              {w.type.replace(/_/g, " ")}
                            </span>
                            {w.distance_km != null && <span className="text-xs text-gray-400">{w.distance_km} km</span>}
                            {w.duration_minutes != null && <span className="text-xs text-gray-400">{w.duration_minutes} min</span>}
                            {w.is_optional && <span className="text-xs text-gray-400 italic">optional</span>}
                          </div>
                          <p className="text-sm text-gray-600">{w.description}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* Chat panel */}
            <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden mb-4">
              <div className="px-6 pt-5 pb-3 border-b border-gray-100">
                <h2 className="text-base font-semibold text-gray-900">Refine with your coach</h2>
                <p className="text-xs text-gray-400 mt-0.5">Claude will ask questions until it has enough context, then update the preview.</p>
              </div>
              <div className="px-6 py-4 space-y-3 min-h-[200px] max-h-[60vh] overflow-y-auto">
                {chatMessages.length === 0 && (
                  <p className="text-xs text-gray-300 text-center py-4">Describe what you&apos;d like to change…</p>
                )}
                {chatMessages.map((msg, i) => (
                  <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${
                      msg.role === "user"
                        ? "bg-blue-600 text-white rounded-br-sm"
                        : msg.isPlanUpdate
                        ? "bg-green-50 text-green-800 border border-green-200 rounded-bl-sm"
                        : "bg-gray-100 text-gray-800 rounded-bl-sm"
                    }`}>
                      {msg.isPlanUpdate && <p className="text-xs font-semibold text-green-600 mb-1">✓ Preview updated</p>}
                      {msg.content}
                    </div>
                  </div>
                ))}
                {chatLoading && (
                  <div className="flex justify-start">
                    <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-2.5">
                      <div className="flex gap-1 items-center h-4">
                        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
                        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
                        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
                      </div>
                    </div>
                  </div>
                )}
                <div ref={chatBottomRef} />
              </div>
              <div className="px-4 pb-4">
                {chatError && <p className="text-xs text-red-500 mb-2 px-2">{chatError}</p>}
                <form onSubmit={handleChatSend} className="flex gap-2 items-end">
                  <textarea
                    rows={3}
                    value={chatInput}
                    onChange={e => setChatInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChatSend(e as unknown as React.FormEvent); } }}
                    disabled={chatLoading}
                    placeholder={'e.g. "Add more tempo runs"\n(Shift+Enter for new line)'}
                    className="flex-1 border border-gray-300 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 resize-none"
                  />
                  <button
                    type="submit"
                    disabled={chatLoading || !chatInput.trim()}
                    className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium px-4 py-2 rounded-xl text-sm transition-colors"
                  >
                    Send
                  </button>
                </form>
              </div>
            </div>

            <div className="flex items-center gap-4">
              <button
                onClick={handleSave}
                disabled={saving}
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-semibold py-2.5 px-8 rounded-lg text-sm transition-colors"
              >
                {saving ? "Saving…" : "Save Plan"}
              </button>
              {saveError && <p className="text-sm text-red-600">{saveError}</p>}
              <span className="text-xs text-gray-400">Plan won&apos;t be saved until you click this.</span>
            </div>
          </>
        )}

      </div>
    </div>
  );
}
