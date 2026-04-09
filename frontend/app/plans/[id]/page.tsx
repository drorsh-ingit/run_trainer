"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import Nav from "../../components/Nav";
import GarminModal from "../../components/GarminModal";
import GeneratingProgress from "../../components/GeneratingProgress";
import { apiFetch, useRequireAuth } from "../../hooks/useAuth";

type WorkoutActivity = {
  strava_activity_id: string;
  name: string | null;
  actual_distance_km: number | null;
  actual_duration_sec: number | null;
  average_hr: number | null;
  average_pace_min_per_km: number | null;
  hr_zones: number[] | null;
  elevation_gain: number | null;
  elevation_loss: number | null;
  has_streams: boolean;
  laps: { lap_index: number; name: string | null; distance: number; moving_time: number; average_speed: number | null; average_heartrate: number | null; elevation_gain: number | null; elevation_loss: number | null; hr_zones: number[] | null }[] | null;
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
  strava_activity_id: string | null;
  activity: WorkoutActivity | null;
};

type Plan = {
  id: number;
  plan_type: string | null;
  goal_distance: number | null;
  goal_date: string | null;
  plan_duration_weeks: number | null;
  schedule_description: string;
  injuries: string;
  additional_notes: string;
  plan_data: { summary: string; total_weeks: number; weeks: unknown[] };
  workouts: Workout[];
  ai_model: string | null;
};

type ChatMsg = { role: "user" | "assistant"; content: string; isPlanUpdate?: boolean; isStatus?: boolean };

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

// Return the ISO date string of the Sunday that starts the Israeli week for a given date
function israeliWeekStart(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() - d.getDay()); // d.getDay(): 0=Sun → subtract 0; 6=Sat → subtract 6
  return d.toISOString().slice(0, 10);
}

const AI_MODELS = [
  { value: "claude-opus-4-6",           label: "Claude Opus 4.6",   badge: "Most capable",  provider: "Anthropic" },
  { value: "claude-sonnet-4-6",         label: "Claude Sonnet 4.6", badge: "Balanced",       provider: "Anthropic" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5",  badge: "Fast & cheap",   provider: "Anthropic" },
  { value: "gpt-4o",                    label: "GPT-4o",            badge: "Most capable",   provider: "OpenAI"    },
  { value: "gpt-4o-mini",               label: "GPT-4o mini",       badge: "Balanced",       provider: "OpenAI"    },
  { value: "gpt-3.5-turbo",             label: "GPT-3.5 Turbo",     badge: "Fast & cheap",   provider: "OpenAI"    },
];

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function isStaleGarminSession(msg: string) {
  return /session expired|reconnect|stale|401|403/i.test(msg);
}

function dayOfWeekFromDate(dateStr: string): string {
  return DAY_NAMES[new Date(dateStr + "T00:00:00").getDay()];
}

function groupByWeek(workouts: Workout[]) {
  const map = new Map<string, Workout[]>();
  for (const w of workouts) {
    const key = israeliWeekStart(w.scheduled_date);
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(w);
  }
  // Sort each week's workouts by date, then sort weeks chronologically and number them
  const sorted = Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [, ws] of sorted) ws.sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date));
  return sorted.map(([, ws], i) => [i + 1, ws] as [number, Workout[]]);
}


export default function PlanDetailPage() {
  useRequireAuth();
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const weekRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const initialScrollDone = useRef(false);
  const [deleting, setDeleting] = useState(false);
  const [showPullMenu, setShowPullMenu] = useState(false);
  const pullMenuRef = useRef<HTMLDivElement>(null);
  const [pullMenuPos, setPullMenuPos] = useState<{top: number; left: number} | null>(null);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [exportMenuPos, setExportMenuPos] = useState<{top: number; left: number} | null>(null);
  const [exportSubmenu, setExportSubmenu] = useState<"garmin" | "gcal" | null>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);

  // Garmin state
  const [garminStatus, setGarminStatus] = useState<{ connected: boolean; display_name?: string } | null>(null);
  const [showGarminModal, setShowGarminModal] = useState(false);
  const [garminReconnect, setGarminReconnect] = useState(false);
  const [garminPushing, setGarminPushing] = useState(false);
  const [garminPushResult, setGarminPushResult] = useState("");

  // Strava state
  const [stravaStatus, setStravaStatus] = useState<{ connected: boolean; athlete_name?: string } | null>(null);

  // Activity sync state
  const [activitySyncing, setActivitySyncing] = useState(false);
  const [activitySyncResult, setActivitySyncResult] = useState("");
  const [rescoring, setRescoring] = useState(false);

  // Chat state
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState("");
  const [chatModel, setChatModel] = useState<string | null>(null); // null = use plan's model
  const bottomRef = useRef<HTMLDivElement>(null);

  // Assessment state
  const [assessOpen, setAssessOpen] = useState(false);
  const [assessMessages, setAssessMessages] = useState<ChatMsg[]>([]);
  const [assessInput, setAssessInput] = useState("");
  const [assessLoading, setAssessLoading] = useState(false);
  const [assessError, setAssessError] = useState("");
  const [assessPreview, setAssessPreview] = useState<Record<string, unknown> | null>(null);
  const [assessSaving, setAssessSaving] = useState(false);
  const [assessGenerating, setAssessGenerating] = useState(false);
  const [assessReady, setAssessReady] = useState(false);
  const [diffExpanded, setDiffExpanded] = useState<Set<number>>(new Set());
  const [assessModel, setAssessModel] = useState<string | null>(null);
  const assessBottomRef = useRef<HTMLDivElement>(null);
  const assessPanelRef = useRef<HTMLDivElement>(null);

  // Adjust dropdown state
  const [showAdjustMenu, setShowAdjustMenu] = useState(false);
  const [adjustMenuPos, setAdjustMenuPos] = useState<{top: number; left: number} | null>(null);
  const adjustMenuRef = useRef<HTMLDivElement>(null);
  const chatPanelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiFetch(`/plans/${id}`)
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(data => {
        setPlan(data);
        setChatModel(data.ai_model ?? "claude-sonnet-4-6");
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    apiFetch("/garmin/status")
      .then(r => r.json())
      .then(setGarminStatus)
      .catch(() => setGarminStatus({ connected: false }));
    apiFetch("/strava/status")
      .then(r => r.ok ? r.json() : { connected: false })
      .then(setStravaStatus)
      .catch(() => setStravaStatus({ connected: false }));
  }, []);

  // Scroll to the current/next week on initial load only
  useEffect(() => {
    if (!plan || initialScrollDone.current) return;
    initialScrollDone.current = true;
    const todayStr = new Date().toISOString().slice(0, 10);
    const allWeeks = groupByWeek(plan.workouts);
    const target = allWeeks.find(([, ws]) => ws.some(w => w.scheduled_date >= todayStr))
      ?? allWeeks[allWeeks.length - 1];
    if (target) {
      const el = weekRefs.current.get(target[0] as number);
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [plan]);

  // Scroll chat to bottom when new messages arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chatLoading]);

  // Scroll to assessment panel when it opens
  useEffect(() => {
    if (assessOpen) {
      setTimeout(() => assessPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    }
  }, [assessOpen]);

  // Scroll assessment chat to bottom within its own scroll container
  useEffect(() => {
    const el = assessBottomRef.current?.parentElement;
    if (el) el.scrollTop = el.scrollHeight;
  }, [assessMessages, assessLoading]);

  // Close pull menu on outside click
  useEffect(() => {
    if (!showPullMenu) return;
    const handler = (e: MouseEvent) => {
      if (pullMenuRef.current && !pullMenuRef.current.contains(e.target as Node)) {
        setShowPullMenu(false);
        setShowExportMenu(false);
        setExportSubmenu(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showPullMenu]);

  // Close export menu on outside click
  useEffect(() => {
    if (!showExportMenu) return;
    const handler = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setShowExportMenu(false);
        setExportSubmenu(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showExportMenu]);

  // Close adjust menu on outside click
  useEffect(() => {
    if (!showAdjustMenu) return;
    const handler = (e: MouseEvent) => {
      if (adjustMenuRef.current && !adjustMenuRef.current.contains(e.target as Node)) {
        setShowAdjustMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAdjustMenu]);

  const handleGarminPush = useCallback(async (month?: string) => {
    setShowExportMenu(false);
    setGarminPushing(true);
    setGarminPushResult("Starting…");
    const url = month ? `/plans/${id}/garmin-push?month=${month}` : `/plans/${id}/garmin-push`;
    try {
      const res = await apiFetch(url, { method: "POST" });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        const msg = data.detail ?? String(res.status);
        if (isStaleGarminSession(msg)) { setGarminPushResult(""); setGarminPushing(false); setGarminReconnect(true); setShowGarminModal(true); return; }
        setGarminPushResult(`Push failed: ${msg}`);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === "error" && isStaleGarminSession(evt.message ?? "")) {
              setGarminPushResult(""); setGarminPushing(false); setGarminReconnect(true); setShowGarminModal(true); return;
            }
            if (evt.message) setGarminPushResult(evt.message);
            if (evt.type === "done" || evt.type === "error") setGarminPushing(false);
          } catch { /* ignore malformed */ }
        }
      }
    } catch {
      setGarminPushResult("Push failed: network error");
      setGarminPushing(false);
    }
  }, [id]);

  const handleDelete = async () => {
    if (!confirm("Delete this plan? This cannot be undone.")) return;
    setDeleting(true);
    try {
      const res = await apiFetch(`/plans/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      router.replace("/dashboard");
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Delete failed");
      setDeleting(false);
    }
  };

  const handleActivitySync = async () => {
    setActivitySyncing(true);
    setActivitySyncResult("Pulling…");
    try {
      const res = await apiFetch(`/plans/${id}/garmin-sync`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        const msg = data.detail ?? "Pull failed";
        if (isStaleGarminSession(msg)) { setActivitySyncResult(""); setActivitySyncing(false); setGarminReconnect(true); setShowGarminModal(true); return; }
        setActivitySyncResult(msg);
        return;
      }
      const total = data.total ?? data.synced;
      setActivitySyncResult(`Pulled ${total} activit${total !== 1 ? "ies" : "y"} (${data.synced} matched)`);
      const planRes = await apiFetch(`/plans/${id}`);
      if (planRes.ok) setPlan(await planRes.json());
    } catch {
      setActivitySyncResult("Pull failed: network error");
    } finally {
      setActivitySyncing(false);
    }
  };

  const handleStravaConnect = async () => {
    setShowPullMenu(false);
    const res = await apiFetch("/strava/auth-url");
    if (!res.ok) { alert("Failed to get Strava auth URL"); return; }
    const { url } = await res.json();
    window.location.href = url;
  };

  const handleStravaSync = async () => {
    setShowPullMenu(false);
    setActivitySyncing(true);
    setActivitySyncResult("Pulling from Strava…");
    try {
      const res = await apiFetch(`/strava/sync/${id}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) { setActivitySyncResult(data.detail ?? "Pull failed"); return; }
      const newTotal = data.new_total ?? data.total ?? data.synced;
      if (newTotal === 0) {
        setActivitySyncResult("No new activities from Strava");
      } else {
        setActivitySyncResult(`Pulled ${newTotal} new activit${newTotal !== 1 ? "ies" : "y"} from Strava (${data.synced} matched)`);
      }
      const planRes = await apiFetch(`/plans/${id}`);
      if (planRes.ok) setPlan(await planRes.json());
    } catch {
      setActivitySyncResult("Pull failed: network error");
    } finally {
      setActivitySyncing(false);
    }
  };

  const handleRescore = async () => {
    setShowPullMenu(false);
    setRescoring(true);
    setActivitySyncResult("Recalculating scores…");
    try {
      const res = await apiFetch(`/plans/${id}/rescore`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) { setActivitySyncResult(data.detail ?? "Rescore failed"); return; }
      setActivitySyncResult(`Rescored ${data.rescored} activit${data.rescored !== 1 ? "ies" : "y"}`);
      const planRes = await apiFetch(`/plans/${id}`);
      if (planRes.ok) setPlan(await planRes.json());
    } catch {
      setActivitySyncResult("Rescore failed: network error");
    } finally {
      setRescoring(false);
    }
  };

  const handleIgnoreActivity = useCallback(async (activityId: string) => {
    const res = await apiFetch(`/plans/${id}/activities/${activityId}`, { method: "DELETE" });
    if (!res.ok) return;
    const planRes = await apiFetch(`/plans/${id}`);
    if (planRes.ok) setPlan(await planRes.json());
  }, [id]);

  // --- Assessment handlers ---

  const processAssessSSE = async (res: Response) => {
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      const detail = data.detail;
      setAssessError(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${res.status}`);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    setAssessMessages(prev => [...prev, { role: "assistant", content: "Analyzing…", isStatus: true }]);

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === "status") {
            setAssessMessages(prev => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.isStatus) next[next.length - 1] = { ...last, content: evt.message };
              return next;
            });
          } else if (evt.type === "question") {
            setAssessMessages(prev => {
              const next = prev.filter(m => !m.isStatus);
              return [...next, { role: "assistant", content: evt.message }];
            });
          } else if (evt.type === "ready") {
            setAssessReady(true);
            setAssessMessages(prev => {
              const next = prev.filter(m => !m.isStatus);
              return [...next, { role: "assistant", content: evt.message }];
            });
          } else if (evt.type === "plan_preview") {
            setAssessPreview(evt.revised_future_plan);
            setAssessMessages(prev => {
              const next = prev.filter(m => !m.isStatus);
              return [...next, { role: "assistant", content: evt.message, isPlanUpdate: true }];
            });
          } else if (evt.type === "error") {
            setAssessError(evt.message);
            setAssessMessages(prev => prev.filter(m => !m.isStatus));
          }
        } catch { /* ignore malformed */ }
      }
    }
  };

  const handleAssessStart = () => {
    setAssessOpen(true);
    setAssessMessages([]);
    setAssessPreview(null);
    setAssessReady(false);
    setAssessError("");
    if (!assessModel && plan) setAssessModel(plan.ai_model ?? "claude-sonnet-4-6");
  };

  const handleAssessRun = async () => {
    setAssessLoading(true);
    setAssessError("");

    try {
      const res = await apiFetch(`/plans/${id}/assess/start`, {
        method: "POST",
        body: JSON.stringify({ ai_model: assessModel || plan?.ai_model || "claude-sonnet-4-6" }),
      });
      await processAssessSSE(res);
    } catch (err: unknown) {
      setAssessError(err instanceof Error ? err.message : "Something went wrong");
      setAssessMessages(prev => prev.filter(m => !m.isStatus));
    } finally {
      setAssessLoading(false);
    }
  };

  const handleAssessReply = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = assessInput.trim();
    if (!text || assessLoading) return;

    const userMsg: ChatMsg = { role: "user", content: text };
    setAssessMessages(prev => [...prev, userMsg]);
    setAssessInput("");
    setAssessLoading(true);
    setAssessError("");
    setAssessPreview(null);

    try {
      const history = assessMessages.filter(m => !m.isStatus).map(m => ({ role: m.role, content: m.content }));
      const res = await apiFetch(`/plans/${id}/assess/reply`, {
        method: "POST",
        body: JSON.stringify({
          message: text,
          history,
          ai_model: assessModel || plan?.ai_model || "claude-sonnet-4-6",
        }),
      });
      await processAssessSSE(res);
    } catch (err: unknown) {
      setAssessError(err instanceof Error ? err.message : "Something went wrong");
      setAssessMessages(prev => prev.filter(m => !m.isStatus));
    } finally {
      setAssessLoading(false);
    }
  };

  const handleAssessBuild = async () => {
    setAssessGenerating(true);
    setAssessLoading(true);
    setAssessError("");

    try {
      const history = assessMessages.filter(m => !m.isStatus).map(m => ({ role: m.role, content: m.content }));
      const res = await apiFetch(`/plans/${id}/assess/build`, {
        method: "POST",
        body: JSON.stringify({
          message: "",
          history,
          ai_model: assessModel || plan?.ai_model || "claude-sonnet-4-6",
        }),
      });
      await processAssessSSE(res);
    } catch (err: unknown) {
      setAssessError(err instanceof Error ? err.message : "Something went wrong");
      setAssessMessages(prev => prev.filter(m => !m.isStatus));
    } finally {
      setAssessLoading(false);
      setAssessGenerating(false);
    }
  };

  const handleAssessApply = async () => {
    if (!assessPreview) return;
    setAssessSaving(true);
    try {
      const res = await apiFetch(`/plans/${id}/assess/apply`, {
        method: "POST",
        body: JSON.stringify({ revised_plan_data: assessPreview }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setAssessError(data.detail ?? `HTTP ${res.status}`);
        return;
      }
      const updated = await res.json();
      setPlan(updated);
      setAssessOpen(false);
      setAssessMessages([]);
      setAssessPreview(null);
    } catch (err: unknown) {
      setAssessError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setAssessSaving(false);
    }
  };

  const handleAssessContinue = () => {
    setAssessPreview(null);
    setAssessReady(false);
    // Messages stay — user can type follow-up adjustments
  };

  const handleAssessDismiss = () => {
    setAssessOpen(false);
    setAssessMessages([]);
    setAssessPreview(null);
    setAssessReady(false);
    setAssessGenerating(false);
    setAssessInput("");
    setAssessError("");
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || chatLoading) return;

    const userMsg: ChatMsg = { role: "user", content: text };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setChatLoading(true);
    setChatError("");

    try {
      const history = messages.map(m => ({ role: m.role, content: m.content }));
      const res = await apiFetch(`/plans/${id}/chat`, {
        method: "POST",
        body: JSON.stringify({ message: text, history, ai_model: chatModel }),
      });

      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        setChatError(data.detail ?? `HTTP ${res.status}`);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      // Add a placeholder status message that we'll update live
      setMessages(prev => [...prev, { role: "assistant", content: "Thinking…", isStatus: true }]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === "status") {
              setMessages(prev => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.isStatus) next[next.length - 1] = { ...last, content: evt.message };
                return next;
              });
            } else if (evt.type === "question") {
              setMessages(prev => {
                const next = prev.filter(m => !m.isStatus);
                return [...next, { role: "assistant", content: evt.message }];
              });
            } else if (evt.type === "plan") {
              setPlan(evt.plan);
              setMessages(prev => {
                const next = prev.filter(m => !m.isStatus);
                return [...next, { role: "assistant", content: evt.message, isPlanUpdate: true }];
              });
            } else if (evt.type === "error") {
              setChatError(evt.message);
              setMessages(prev => prev.filter(m => !m.isStatus));
            }
          } catch { /* ignore malformed */ }
        }
      }
    } catch (err: unknown) {
      setChatError(err instanceof Error ? err.message : "Something went wrong");
      setMessages(prev => prev.filter(m => !m.isStatus));
    } finally {
      setChatLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Nav />
        <div className="max-w-4xl mx-auto px-4 py-10">
          <p className="text-gray-500 text-sm">Loading plan…</p>
        </div>
      </div>
    );
  }

  if (error || !plan) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Nav />
        <div className="max-w-4xl mx-auto px-4 py-10">
          <p className="text-red-600 text-sm">{error || "Plan not found"}</p>
        </div>
      </div>
    );
  }

  const weeks = groupByWeek(plan.workouts);

  // Derive sorted unique "YYYY-MM" months from all workouts
  const exportMonths = Array.from(
    new Set(plan.workouts.map(w => w.scheduled_date.slice(0, 7)))
  ).sort();

  return (
    <div className="min-h-screen bg-gray-50">
      <Nav />
      <div className="max-w-4xl mx-auto px-4 py-10 space-y-6">

        {/* Header */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 sticky top-14 z-20">
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-4 flex-wrap">
              <Link
                href={`/calendar?plan_id=${plan.id}`}
                className="text-blue-500 hover:text-blue-700 text-sm transition-colors"
              >
                View Calendar
              </Link>
              <div ref={exportMenuRef} className="relative">
                <button
                  onClick={(e) => {
                    setShowPullMenu(false);
                    setExportSubmenu(null);
                    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                    setExportMenuPos({ top: rect.bottom + 4, left: Math.max(8, Math.min(rect.left, window.innerWidth - 184)) });
                    setShowExportMenu(v => !v);
                  }}
                  className="text-blue-500 hover:text-blue-700 text-sm transition-colors"
                >
                  Push Workouts ▾
                </button>
                {showExportMenu && exportMenuPos && (
                  <div style={{ position: "fixed", top: exportMenuPos.top, left: exportMenuPos.left }} className="w-44 bg-white border border-gray-200 rounded-xl shadow-lg z-50 py-1 text-sm">
                    {/* ── Garmin ── */}
                    <div className="relative">
                      <button
                        onClick={() => setExportSubmenu(exportSubmenu === "garmin" ? null : "garmin")}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 flex items-center justify-between"
                      >
                        <span className="flex items-center gap-2">
                          Garmin
                          {garminStatus?.connected && <span className="text-[10px] text-green-500 font-medium">connected</span>}
                        </span>
                        <span className="text-gray-400 text-xs">›</span>
                      </button>
                      {exportSubmenu === "garmin" && (
                        <div className="absolute left-0 top-full mt-1 w-52 bg-white border border-gray-200 rounded-xl shadow-lg py-1 text-sm z-20">
                          {garminStatus?.connected ? (
                            <>
                              <button
                                onClick={() => { setExportSubmenu(null); handleGarminPush(); }}
                                disabled={garminPushing}
                                className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 disabled:text-gray-300"
                              >
                                {garminPushing ? "Pushing…" : "Whole plan"}
                              </button>
                              {exportMonths.map(m => (
                                <button
                                  key={m}
                                  onClick={() => { setExportSubmenu(null); handleGarminPush(m); }}
                                  disabled={garminPushing}
                                  className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 disabled:text-gray-300"
                                >
                                  {new Date(m + "-01").toLocaleString("default", { month: "long", year: "numeric" })}
                                </button>
                              ))}
                              <div className="border-t border-gray-100 mt-1 pt-1">
                                <button
                                  onClick={() => { setShowExportMenu(false); setExportSubmenu(null); apiFetch("/garmin/auth", { method: "DELETE" }).then(() => setGarminStatus({ connected: false })); }}
                                  className="w-full text-left px-4 py-2 hover:bg-gray-50 text-red-400 text-xs"
                                >
                                  Disconnect Garmin
                                </button>
                              </div>
                            </>
                          ) : (
                            <button
                              onClick={() => { setShowExportMenu(false); setExportSubmenu(null); setGarminReconnect(false); setShowGarminModal(true); }}
                              className="w-full text-left px-4 py-2 hover:bg-gray-50 text-blue-600"
                            >
                              Connect Garmin…
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {/* ── Google Calendar ── */}
                    <div className="relative">
                      <button
                        onClick={() => setExportSubmenu(exportSubmenu === "gcal" ? null : "gcal")}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 flex items-center justify-between"
                      >
                        <span>Google Calendar</span>
                        <span className="text-gray-400 text-xs">›</span>
                      </button>
                      {exportSubmenu === "gcal" && (
                        <div className="absolute left-0 top-full mt-1 w-48 bg-white border border-gray-200 rounded-xl shadow-lg py-1 text-sm z-20">
                          <button
                            onClick={() => { setShowExportMenu(false); setExportSubmenu(null); /* TODO: ICS export */ }}
                            className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800"
                          >
                            Download .ics
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <div ref={pullMenuRef} className="relative">
                <button
                  onClick={(e) => {
                    setShowExportMenu(false);
                    setExportSubmenu(null);
                    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                    setPullMenuPos({ top: rect.bottom + 4, left: Math.max(8, Math.min(rect.left, window.innerWidth - 216)) });
                    setShowPullMenu(v => !v);
                  }}
                  className="text-teal-600 hover:text-teal-800 text-sm transition-colors"
                >
                  Pull Activities ▾
                </button>
                {showPullMenu && pullMenuPos && (
                  <div style={{ position: "fixed", top: pullMenuPos.top, left: pullMenuPos.left }} className="w-52 bg-white border border-gray-200 rounded-xl shadow-lg z-50 py-1 text-sm">
                    {/* Strava */}
                    {stravaStatus?.connected ? (
                      <button
                        onClick={handleStravaSync}
                        disabled={activitySyncing}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 disabled:text-gray-300"
                      >
                        <span className="flex items-center justify-between">
                          <span>{activitySyncing ? "Pulling…" : "Pull from Strava"}</span>
                          <span className="text-[10px] text-green-500 font-medium">connected</span>
                        </span>
                      </button>
                    ) : (
                      <button
                        onClick={handleStravaConnect}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50 text-orange-500"
                      >
                        Connect Strava…
                      </button>
                    )}
                    {/* Recalculate scores */}
                    <div className="border-t border-gray-100 mt-1 pt-1">
                      <button
                        onClick={handleRescore}
                        disabled={rescoring}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 disabled:text-gray-300"
                      >
                        {rescoring ? "Recalculating…" : "Recalculate scores"}
                      </button>
                    </div>
                    {/* Disconnect options */}
                    {stravaStatus?.connected && (
                      <div className="border-t border-gray-100 mt-1 pt-1">
                        {stravaStatus?.connected && (
                          <button
                            onClick={() => { setShowPullMenu(false); apiFetch("/strava/disconnect", { method: "DELETE" }).then(() => setStravaStatus({ connected: false })); }}
                            className="w-full text-left px-4 py-2 hover:bg-gray-50 text-red-400 text-xs"
                          >
                            Disconnect Strava
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div ref={adjustMenuRef} className="relative">
                <button
                  onClick={(e) => {
                    setShowPullMenu(false);
                    setShowExportMenu(false);
                    setExportSubmenu(null);
                    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                    setAdjustMenuPos({ top: rect.bottom + 4, left: Math.max(8, Math.min(rect.left, window.innerWidth - 180)) });
                    setShowAdjustMenu(v => !v);
                  }}
                  className="text-amber-600 hover:text-amber-800 text-sm transition-colors"
                >
                  Adjust Plan ▾
                </button>
                {showAdjustMenu && adjustMenuPos && (
                  <div style={{ position: "fixed", top: adjustMenuPos.top, left: adjustMenuPos.left }} className="w-44 bg-white border border-gray-200 rounded-xl shadow-lg z-50 py-1 text-sm">
                    <button
                      onClick={() => {
                        setShowAdjustMenu(false);
                        handleAssessStart();
                      }}
                      disabled={assessLoading}
                      className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800 disabled:text-gray-300"
                    >
                      Re-assess Plan
                    </button>
                    <button
                      onClick={() => {
                        setShowAdjustMenu(false);
                        chatPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
                      }}
                      className="w-full text-left px-4 py-2 hover:bg-gray-50 text-gray-800"
                    >
                      Adjust Plan
                    </button>
                  </div>
                )}
              </div>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="text-gray-300 hover:text-red-500 disabled:text-gray-200 text-sm transition-colors"
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
              </div>
              {garminPushResult && (
                <p className="text-xs text-gray-500">{garminPushResult}</p>
              )}
              {activitySyncResult && (
                <p className="text-xs text-teal-600">{activitySyncResult}</p>
              )}
            </div>
            <h1 className="text-xl font-semibold text-gray-900">
              {plan.plan_type === "general"
                ? `General fitness — ${plan.plan_duration_weeks ?? plan.plan_data?.total_weeks} weeks`
                : `${plan.goal_distance} km — ${plan.goal_date}`}
            </h1>
          </div>

        {/* Description */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6">
          <p className="text-gray-600 text-sm">{plan.plan_data?.summary}</p>
          <p className="text-gray-400 text-xs mt-1">{plan.plan_data?.total_weeks} weeks total</p>
        </div>

        {/* Garmin credentials modal */}
        {showGarminModal && (
          <GarminModal
            reconnect={garminReconnect}
            onClose={() => { setShowGarminModal(false); setGarminReconnect(false); }}
            onConnected={(name) => {
              setGarminStatus({ connected: true, display_name: name });
              setShowGarminModal(false);
              setGarminReconnect(false);
            }}
          />
        )}

        {/* Assessment panel */}
        {assessOpen && (
          <div ref={assessPanelRef} className="bg-white rounded-2xl shadow-sm border-2 border-amber-300 overflow-hidden">
            <div className="px-6 pt-5 pb-3 border-b border-amber-200 bg-amber-50">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-base font-semibold text-amber-900">Plan Assessment</h2>
                  <p className="text-xs text-amber-600 mt-0.5">
                    Analyzing your actual training vs the plan. Changes are only applied when you click Save.
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <select
                    value={assessModel ?? chatModel ?? "claude-sonnet-4-6"}
                    onChange={e => setAssessModel(e.target.value)}
                    className="border border-amber-200 rounded-lg px-2 py-1 text-xs text-gray-600 focus:outline-none focus:ring-1 focus:ring-amber-400"
                  >
                    {AI_MODELS.map(m => (
                      <option key={m.value} value={m.value}>{m.label} · {m.badge}</option>
                    ))}
                  </select>
                  <button
                    onClick={handleAssessDismiss}
                    className="text-gray-400 hover:text-gray-600 text-sm"
                    title="Close assessment"
                  >
                    ✕
                  </button>
                </div>
              </div>
            </div>

            {/* Assessment message history */}
            <div className="px-6 py-4 space-y-3 min-h-[200px] max-h-[60vh] overflow-y-auto">
              {assessMessages.length === 0 && !assessLoading && (
                <div className="flex flex-col items-center gap-3 py-6">
                  <p className="text-sm text-gray-500 text-center">
                    Analyze your actual training vs the plan and get suggestions for adjustments.
                  </p>
                  <button
                    onClick={handleAssessRun}
                    className="bg-amber-600 hover:bg-amber-700 text-white font-medium px-6 py-2 rounded-xl text-sm transition-colors"
                  >
                    Start Assessment
                  </button>
                </div>
              )}
              {assessMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
                      msg.role === "user"
                        ? "bg-amber-600 text-white rounded-br-sm"
                        : msg.isStatus
                        ? "bg-gray-50 text-gray-400 border border-gray-200 rounded-bl-sm italic"
                        : msg.isPlanUpdate
                        ? "bg-green-50 text-green-800 border border-green-200 rounded-bl-sm"
                        : "bg-gray-100 text-gray-800 rounded-bl-sm"
                    }`}
                  >
                    {msg.isStatus && (
                      <span className="inline-block w-2 h-2 rounded-full bg-gray-300 animate-pulse mr-2" />
                    )}
                    {msg.isPlanUpdate && (
                      <p className="text-xs font-semibold text-green-600 mb-1">Suggested plan changes</p>
                    )}
                    {msg.content}
                  </div>
                </div>
              ))}
              {assessGenerating ? (
                <GeneratingProgress active={true} mode="assess" />
              ) : assessLoading && !assessMessages.some(m => m.isStatus) ? (
                <div className="flex justify-start">
                  <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-2.5">
                    <div className="flex gap-1 items-center h-4">
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
                    </div>
                  </div>
                </div>
              ) : null}
              <div ref={assessBottomRef} />
            </div>

            {/* Diff + actions when preview is ready */}
            {assessPreview && plan && (() => {
              type DiffWorkout = { day_of_week: string; type: string; description?: string; distance_km?: number | null; duration_minutes?: number | null; is_optional?: boolean };
              type DiffWeek = { week_number: number; theme?: string; total_km?: number; workouts?: DiffWorkout[] };
              const oldWeeks = (plan.plan_data?.weeks ?? []) as DiffWeek[];
              const newWeeks = ((assessPreview as { weeks?: unknown[] }).weeks ?? []) as DiffWeek[];
              return (
                <>
                  <div className="px-6 py-4 border-t border-amber-200 bg-amber-50 space-y-2 max-h-[50vh] overflow-y-auto">
                    <h3 className="text-sm font-semibold text-amber-900">Proposed Changes</h3>
                    {newWeeks.map(nw => {
                      const ow = oldWeeks.find(w => w.week_number === nw.week_number);
                      const sumKm = (workouts?: { distance_km?: number | null }[]) =>
                        (workouts ?? []).reduce((s, w) => s + (w.distance_km ?? 0), 0);
                      const oldKm = sumKm(ow?.workouts);
                      const newKm = sumKm(nw.workouts);
                      const kmDiff = newKm - oldKm;
                      const isExpanded = diffExpanded.has(nw.week_number);
                      const toggleExpand = () => setDiffExpanded(prev => {
                        const next = new Set(prev);
                        next.has(nw.week_number) ? next.delete(nw.week_number) : next.add(nw.week_number);
                        return next;
                      });

                      return (
                        <div key={nw.week_number} className="border border-amber-200 rounded-lg bg-white">
                          <button
                            onClick={toggleExpand}
                            className="w-full text-left px-3 py-2 flex items-center justify-between text-sm hover:bg-amber-50"
                          >
                            <span className="font-medium text-gray-800">
                              Week {nw.week_number}
                              {nw.theme && <span className="text-gray-400 font-normal ml-1">({nw.theme})</span>}
                            </span>
                            <span className="flex items-center gap-2 text-xs">
                              <span className="text-gray-400">{oldKm.toFixed(1)} km</span>
                              <span className="text-gray-400">&rarr;</span>
                              <span className={kmDiff > 0 ? "text-green-600 font-medium" : kmDiff < 0 ? "text-red-600 font-medium" : "text-gray-500"}>
                                {newKm.toFixed(1)} km
                                {kmDiff !== 0 && ` (${kmDiff > 0 ? "+" : ""}${kmDiff.toFixed(1)})`}
                              </span>
                              <span className="text-gray-300 ml-1">{isExpanded ? "▾" : "▸"}</span>
                            </span>
                          </button>
                          {isExpanded && (() => {
                            const fmtWorkout = (w: DiffWorkout) => {
                              const parts = [w.type.replace(/_/g, " ")];
                              if (w.distance_km != null) parts.push(`${w.distance_km}km`);
                              if (w.duration_minutes != null) parts.push(`${w.duration_minutes}min`);
                              if (w.is_optional) parts.push("optional");
                              return parts.join(" ");
                            };
                            const isChanged = (o: DiffWorkout | undefined, n: DiffWorkout) =>
                              !o || o.type !== n.type || o.distance_km !== n.distance_km
                              || o.duration_minutes !== n.duration_minutes || o.is_optional !== n.is_optional
                              || (o.description ?? "") !== (n.description ?? "");
                            const removedWorkouts = (ow?.workouts ?? []).filter(owk => !(nw.workouts ?? []).some(n => n.day_of_week === owk.day_of_week));
                            return (
                            <div className="px-3 pb-2 space-y-1 border-t border-amber-100">
                              {removedWorkouts.map((owk, ri) => (
                                <details key={`removed-${ri}`} className="text-xs text-red-700 group/d">
                                  <summary className="flex items-center gap-2 py-0.5 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                                    <span className="text-gray-300 w-3 shrink-0 group-open/d:rotate-90 transition-transform text-[10px]">▸</span>
                                    <span className="w-20 shrink-0">{owk.day_of_week}</span>
                                    <span className="text-red-400">{fmtWorkout(owk)}</span>
                                    <span className="text-gray-300">&rarr;</span>
                                    <span className="font-medium">removed</span>
                                  </summary>
                                  {owk.description && (
                                    <div className="pl-24 pb-1 text-[11px] text-red-400">{owk.description}</div>
                                  )}
                                </details>
                              ))}
                              {(nw.workouts ?? []).map((nwk, wi) => {
                                const owk = ow?.workouts?.find(w => w.day_of_week === nwk.day_of_week);
                                const changed = isChanged(owk, nwk);
                                const metricsChanged = !owk || owk.type !== nwk.type || owk.distance_km !== nwk.distance_km || owk.duration_minutes !== nwk.duration_minutes || owk.is_optional !== nwk.is_optional;
                                const descChanged = (owk?.description ?? "") !== (nwk.description ?? "");
                                const hasDesc = nwk.description || owk?.description;
                                return (
                                  <details key={wi} className={`text-xs group/d ${changed ? "text-amber-800" : "text-gray-400"}`}>
                                    <summary className="flex items-center gap-2 py-0.5 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                                      <span className="text-gray-300 w-3 shrink-0 group-open/d:rotate-90 transition-transform text-[10px]">▸</span>
                                      <span className="w-20 shrink-0">{nwk.day_of_week}</span>
                                      {!owk ? (
                                        <>
                                          <span className="text-green-600 font-medium">added: </span>
                                          <span className="font-medium">{fmtWorkout(nwk)}</span>
                                        </>
                                      ) : metricsChanged ? (
                                        <>
                                          <span className="text-gray-400">{fmtWorkout(owk)}</span>
                                          <span className="text-gray-300">&rarr;</span>
                                          <span className="font-medium">{fmtWorkout(nwk)}</span>
                                        </>
                                      ) : descChanged ? (
                                        <>
                                          <span>{fmtWorkout(nwk)}</span>
                                          <span className="text-amber-500 text-[10px]">(details changed)</span>
                                        </>
                                      ) : (
                                        <span>{fmtWorkout(nwk)}</span>
                                      )}
                                    </summary>
                                    {hasDesc && (
                                      <div className="pl-24 pb-1.5 space-y-0.5">
                                        {owk?.description && changed && (
                                          <p className="text-[11px] text-gray-400">{owk.description}</p>
                                        )}
                                        {nwk.description && (
                                          <p className={`text-[11px] ${changed ? "text-amber-700" : "text-gray-400"}`}>{nwk.description}</p>
                                        )}
                                      </div>
                                    )}
                                  </details>
                                );
                              })}
                            </div>);
                          })()}
                        </div>
                      );
                    })}
                  </div>
                  <div className="px-6 py-3 border-t border-green-200 bg-green-50 flex items-center gap-3">
                    <button
                      onClick={() => { if (window.confirm("Are you sure you want to save these changes?")) handleAssessApply(); }}
                      disabled={assessSaving}
                      className="bg-green-600 hover:bg-green-700 disabled:bg-green-300 text-white font-medium px-5 py-2 rounded-xl text-sm transition-colors"
                    >
                      {assessSaving ? "Saving…" : "Save Changes"}
                    </button>
                    <button
                      onClick={handleAssessContinue}
                      disabled={assessSaving}
                      className="bg-amber-500 hover:bg-amber-600 disabled:bg-amber-300 text-white font-medium px-4 py-2 rounded-xl text-sm transition-colors"
                    >
                      Continue Adjusting
                    </button>
                    <button
                      onClick={() => { if (window.confirm("Are you sure you want to dismiss the revised plan?")) handleAssessDismiss(); }}
                      disabled={assessSaving}
                      className="text-gray-500 hover:text-gray-700 text-sm transition-colors"
                    >
                      Dismiss
                    </button>
                    <span className="text-xs text-green-600 ml-auto">
                      Only future workouts will be updated
                    </span>
                  </div>
                </>
              );
            })()}

            {/* Build button when coach is ready, or chat input for Q&A */}
            {!assessPreview && !assessGenerating && (
              <div className="px-4 pb-4">
                {assessError && <p className="text-xs text-red-500 mb-2 px-2">{assessError}</p>}
                {assessReady ? (
                  <div className="flex flex-col items-center gap-2 py-2">
                    <button
                      onClick={handleAssessBuild}
                      disabled={assessLoading}
                      className="bg-amber-600 hover:bg-amber-700 disabled:bg-amber-300 text-white font-medium px-6 py-2.5 rounded-xl text-sm transition-colors"
                    >
                      Build Revised Plan
                    </button>
                    <p className="text-xs text-gray-400">This may take a minute</p>
                  </div>
                ) : (
                  <form onSubmit={handleAssessReply} className="flex gap-2 items-end">
                    <textarea
                      rows={2}
                      value={assessInput}
                      onChange={e => setAssessInput(e.target.value)}
                      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleAssessReply(e as unknown as React.FormEvent); } }}
                      disabled={assessLoading}
                      placeholder="Reply to your coach…"
                      className="flex-1 border border-gray-300 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500 disabled:bg-gray-50 resize-none"
                    />
                    <button
                      type="submit"
                      disabled={assessLoading || !assessInput.trim()}
                      className="bg-amber-600 hover:bg-amber-700 disabled:bg-amber-300 text-white font-medium px-4 py-2 rounded-xl text-sm transition-colors"
                    >
                      Send
                    </button>
                  </form>
                )}
              </div>
            )}
          </div>
        )}

        {/* Weeks */}
        {weeks.map(([weekNum, workouts]) => {
          const isRaceWeek = workouts.some(w => w.workout_type === "race");
          return (
          <div
            key={weekNum}
            ref={el => { if (el) weekRefs.current.set(weekNum, el); }}
            className="rounded-2xl shadow-sm border border-gray-200 bg-white p-6"
          >
            <div className="flex items-center justify-between mb-4">
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                {isRaceWeek ? "🏅 Race Week" : `Week ${weekNum}`}
              </span>
              <span className="text-sm text-gray-400">
                {workouts.reduce((s, w) => s + (w.target_distance_km ?? 0), 0).toFixed(1)} km
              </span>
            </div>
            <div className="space-y-3">
              {workouts.map(w => {
                const isRace = w.workout_type === "race";
                return (
                <div
                  key={w.id}
                  className={`flex gap-3 items-start rounded-xl p-2 -mx-2 ${
                    isRace ? "bg-gradient-to-r from-yellow-100 to-amber-100 border border-yellow-300" : ""
                  }`}
                >
                  <span className="text-xs text-gray-400 w-24 pt-0.5 shrink-0">
                    {dayOfWeekFromDate(w.scheduled_date)}
                    <br />
                    <span className="text-gray-300">{w.scheduled_date}</span>
                  </span>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                      {isRace ? (
                        <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-yellow-400 text-yellow-900">
                          🏅 Race Day!
                        </span>
                      ) : (
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${WORKOUT_COLORS[w.workout_type] ?? "bg-gray-100 text-gray-600"}`}>
                          {w.workout_type.replace(/_/g, " ")}
                        </span>
                      )}
                      {w.target_distance_km != null && <span className="text-xs text-gray-400">{w.target_distance_km} km</span>}
                      {w.target_duration_minutes != null && <span className="text-xs text-gray-400">{w.target_duration_minutes} min</span>}
                      {w.is_optional && <span className="text-xs text-gray-400 italic">optional</span>}
                    </div>
                    <p className="text-sm text-gray-600">{w.description}</p>
                    {w.activity && (
                      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-500">
                        {w.activity.actual_distance_km != null && (
                          <span>
                            <span className="text-gray-400">dist </span>
                            <span className="font-medium text-gray-700">{w.activity.actual_distance_km} km</span>
                            {w.target_distance_km != null && (
                              <span className="text-gray-400"> / {w.target_distance_km} km planned</span>
                            )}
                          </span>
                        )}
                        {w.activity.actual_duration_sec != null && (
                          <span>
                            <span className="text-gray-400">time </span>
                            <span className="font-medium text-gray-700">
                              {Math.floor(w.activity.actual_duration_sec / 60)}:{String(w.activity.actual_duration_sec % 60).padStart(2, "0")}
                            </span>
                          </span>
                        )}
                        {w.activity.average_pace_min_per_km != null && (
                          <span>
                            <span className="text-gray-400">pace </span>
                            <span className="font-medium text-gray-700">
                              {Math.floor(w.activity.average_pace_min_per_km)}:{String(Math.round((w.activity.average_pace_min_per_km % 1) * 60)).padStart(2, "0")} /km
                            </span>
                          </span>
                        )}
                        {w.activity.average_hr != null && (
                          <span>
                            <span className="text-gray-400">avg HR </span>
                            <span className="font-medium text-gray-700">{Math.round(w.activity.average_hr)} bpm</span>
                          </span>
                        )}
                        {(w.activity.elevation_gain != null || w.activity.elevation_loss != null) && (
                          <span>
                            <span className="text-gray-400">elev </span>
                            {w.activity.elevation_gain != null && <span className="font-medium text-green-600">+{Math.round(w.activity.elevation_gain)}m</span>}
                            {w.activity.elevation_gain != null && w.activity.elevation_loss != null && <span className="text-gray-300"> / </span>}
                            {w.activity.elevation_loss != null && <span className="font-medium text-red-500">-{Math.round(w.activity.elevation_loss)}m</span>}
                          </span>
                        )}
                        {w.activity.hr_zones && (
                          <span title={`Z1:${w.activity.hr_zones[0]}% Z2:${w.activity.hr_zones[1]}% Z3:${w.activity.hr_zones[2]}% Z4:${w.activity.hr_zones[3]}% Z5:${w.activity.hr_zones[4]}%`}>
                            <span className="text-gray-400">HR zones </span>
                            {w.activity.hr_zones.map((z, i) => (
                              <span key={i} className={`font-medium ${i === 0 ? "text-blue-500" : i === 1 ? "text-green-500" : i === 2 ? "text-yellow-500" : i === 3 ? "text-orange-500" : "text-red-500"}`}>
                                {z}%{i < 4 ? " " : ""}
                              </span>
                            ))}
                          </span>
                        )}
                      </div>
                    )}
                    {w.activity?.laps && w.activity.laps.length > 1 && (
                      <details className="mt-1.5">
                        <summary className="text-[11px] text-gray-400 cursor-pointer hover:text-gray-600">
                          {w.activity.laps.length} laps
                        </summary>
                        <div className="mt-1 space-y-0.5">
                          {w.activity.laps.map((lap) => {
                            const distKm = (lap.distance / 1000).toFixed(2);
                            const paceVal = lap.average_speed && lap.average_speed > 0 ? 1000 / (lap.average_speed * 60) : null;
                            const paceStr = paceVal ? `${Math.floor(paceVal)}:${String(Math.round((paceVal % 1) * 60)).padStart(2, "0")}` : "—";
                            const timeStr = `${Math.floor(lap.moving_time / 60)}:${String(lap.moving_time % 60).padStart(2, "0")}`;
                            return (
                              <div key={lap.lap_index} className="flex gap-3 text-[11px] text-gray-500 font-mono">
                                <span className="text-gray-300 w-4 text-right">{lap.lap_index}</span>
                                <span className="w-14">{distKm} km</span>
                                <span className="w-14">{paceStr}/km</span>
                                <span className="w-12">{timeStr}</span>
                                {lap.average_heartrate && <span className="w-12">{Math.round(lap.average_heartrate)} bpm</span>}
                                {(lap.elevation_gain != null || lap.elevation_loss != null) && (
                                  <span className="w-20">
                                    {lap.elevation_gain != null && <span className="text-green-600">+{Math.round(lap.elevation_gain)}</span>}
                                    {lap.elevation_loss != null && <span className="text-red-500">/{-Math.round(lap.elevation_loss)}</span>}
                                    <span className="text-gray-400">m</span>
                                  </span>
                                )}
                                {lap.hr_zones && (
                                  <span className="text-[10px] text-gray-400">
                                    {["Z1","Z2","Z3","Z4","Z5"].map((z, i) => lap.hr_zones![i] > 0 ? `${z}:${lap.hr_zones![i]}%` : null).filter(Boolean).join(" ")}
                                  </span>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </details>
                    )}
                    {w.activity?.match_score != null && (
                      <div className="mt-2 flex items-start gap-2">
                        <span className={`shrink-0 text-xs font-semibold px-1.5 py-0.5 rounded-full ${
                          w.activity.match_score >= 90 ? "bg-green-100 text-green-700" :
                          w.activity.match_score >= 70 ? "bg-yellow-100 text-yellow-700" :
                          "bg-red-100 text-red-600"
                        }`}>{w.activity.match_score}%</span>
                        {w.activity.match_comment && (
                          <p className="text-xs text-gray-500 italic">{w.activity.match_comment}</p>
                        )}
                      </div>
                    )}
                    {w.activity && (
                      <button
                        onClick={() => handleIgnoreActivity(w.activity!.strava_activity_id)}
                        className="text-[11px] text-gray-300 hover:text-gray-500 transition-colors"
                        title="Discard this activity and don't pull it again"
                      >
                        ignore
                      </button>
                    )}
                  </div>
                </div>
                );
              })}
            </div>
          </div>
          );
        })}

        {/* Chat panel */}
        <div ref={chatPanelRef} className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="px-6 pt-5 pb-3 border-b border-gray-100">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-gray-900">Adjust Plan</h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  Chat with your coach — it will ask questions until it has enough context, then update the plan.
                </p>
              </div>
              <div className="shrink-0">
                <select
                  value={chatModel ?? "claude-sonnet-4-6"}
                  onChange={e => setChatModel(e.target.value)}
                  className="border border-gray-200 rounded-lg px-2 py-1 text-xs text-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-400"
                >
                  {AI_MODELS.map(m => (
                    <option key={m.value} value={m.value}>{m.label} · {m.badge}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Message history */}
          <div className="px-6 py-4 space-y-3 min-h-[200px] max-h-[60vh] overflow-y-auto">
            {messages.length === 0 && (
              <p className="text-xs text-gray-300 text-center py-4">
                Describe what you&apos;d like to change…
              </p>
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${
                    msg.role === "user"
                      ? "bg-blue-600 text-white rounded-br-sm"
                      : msg.isStatus
                      ? "bg-gray-50 text-gray-400 border border-gray-200 rounded-bl-sm italic"
                      : msg.isPlanUpdate
                      ? "bg-green-50 text-green-800 border border-green-200 rounded-bl-sm"
                      : "bg-gray-100 text-gray-800 rounded-bl-sm"
                  }`}
                >
                  {msg.isStatus && (
                    <span className="inline-block w-2 h-2 rounded-full bg-gray-300 animate-pulse mr-2" />
                  )}
                  {msg.isPlanUpdate && (
                    <p className="text-xs font-semibold text-green-600 mb-1">✓ Plan updated</p>
                  )}
                  {msg.content}
                </div>
              </div>
            ))}
            {chatLoading && !messages.some(m => m.isStatus) && (
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
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="px-4 pb-4">
            {chatError && <p className="text-xs text-red-500 mb-2 px-2">{chatError}</p>}
            <form onSubmit={handleSend} className="flex gap-2 items-end">
              <textarea
                rows={3}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(e as unknown as React.FormEvent); } }}
                disabled={chatLoading}
                placeholder={"e.g. I'll be travelling week 5, make it lighter\n(Shift+Enter for new line)"}
                className="flex-1 border border-gray-300 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 resize-none"
              />
              <button
                type="submit"
                disabled={chatLoading || !input.trim()}
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium px-4 py-2 rounded-xl text-sm transition-colors"
              >
                Send
              </button>
            </form>
          </div>
        </div>

      </div>
    </div>
  );
}
