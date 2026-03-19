"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Nav from "../components/Nav";
import { apiFetch, useRequireAuth } from "../hooks/useAuth";

type Plan = {
  id: number;
  plan_type: string | null;
  goal_distance: number | null;
  goal_date: string | null;
  plan_duration_weeks: number | null;
  plan_data: { summary: string; total_weeks: number };
  created_at?: string;
};

export default function DashboardPage() {
  useRequireAuth();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [maxHr, setMaxHr] = useState<string>("");
  const [maxHrSaving, setMaxHrSaving] = useState(false);
  const [maxHrMsg, setMaxHrMsg] = useState("");

  useEffect(() => {
    apiFetch("/plans/")
      .then(async res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setPlans)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));

    apiFetch("/auth/me")
      .then(async res => res.ok ? res.json() : null)
      .then(user => { if (user?.max_hr) setMaxHr(String(user.max_hr)); });
  }, []);

  const saveMaxHr = async () => {
    const val = parseInt(maxHr);
    if (!val || val < 100 || val > 250) { setMaxHrMsg("Enter a value between 100 and 250"); return; }
    setMaxHrSaving(true);
    setMaxHrMsg("");
    try {
      const res = await apiFetch("/auth/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ max_hr: val }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMaxHrMsg("Saved!");
    } catch (e: unknown) {
      setMaxHrMsg(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setMaxHrSaving(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, planId: number) => {
    e.preventDefault(); // don't follow the card link
    if (!confirm("Delete this plan? This cannot be undone.")) return;
    setDeletingId(planId);
    try {
      const res = await apiFetch(`/plans/${planId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setPlans(prev => prev.filter(p => p.id !== planId));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <Nav />
      <div className="max-w-4xl mx-auto px-4 py-10">
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold text-gray-900">My Plans</h1>
          <Link
            href="/plans/new"
            className="bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
          >
            + New Plan
          </Link>
        </div>

        <div className="bg-white rounded-2xl border border-gray-200 p-5 mb-6 flex items-center gap-3">
          <span className="text-sm text-gray-600 shrink-0">Max heart rate</span>
          <input
            type="number"
            value={maxHr}
            onChange={e => setMaxHr(e.target.value)}
            placeholder="e.g. 190"
            className="w-28 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <span className="text-sm text-gray-400">bpm</span>
          <button
            onClick={saveMaxHr}
            disabled={maxHrSaving}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium px-3 py-1.5 rounded-lg transition-colors"
          >
            {maxHrSaving ? "Saving…" : "Save"}
          </button>
          {maxHrMsg && <span className="text-sm text-gray-500">{maxHrMsg}</span>}
        </div>

        {loading && <p className="text-gray-500 text-sm">Loading…</p>}
        {error && <p className="text-red-600 text-sm">{error}</p>}

        {!loading && plans.length === 0 && (
          <div className="bg-white rounded-2xl border border-gray-200 p-10 text-center">
            <p className="text-gray-500 mb-4">No plans yet.</p>
            <Link href="/plans/new" className="text-blue-600 hover:underline text-sm">
              Generate your first training plan →
            </Link>
          </div>
        )}

        <div className="space-y-4">
          {plans.map(plan => (
            <div key={plan.id} className="relative group">
              <Link
                href={`/plans/${plan.id}`}
                className="block bg-white rounded-2xl border border-gray-200 p-5 hover:border-blue-300 hover:shadow-sm transition-all"
              >
                <div className="flex items-start justify-between">
                  <div className="pr-10">
                    <p className="text-sm font-semibold text-gray-900">
                      {plan.plan_type === "general"
                        ? `General fitness — ${plan.plan_duration_weeks ?? plan.plan_data?.total_weeks} weeks`
                        : `${plan.goal_distance} km — ${plan.goal_date}`}
                    </p>
                    <p className="text-sm text-gray-500 mt-1 line-clamp-2">
                      {plan.plan_data?.summary}
                    </p>
                  </div>
                  <span className="text-xs text-gray-400 shrink-0">
                    {plan.plan_data?.total_weeks}w
                  </span>
                </div>
              </Link>
              <button
                onClick={e => handleDelete(e, plan.id)}
                disabled={deletingId === plan.id}
                className="absolute top-3 right-10 opacity-0 group-hover:opacity-100 transition-opacity text-gray-300 hover:text-red-500 disabled:text-gray-200 p-1"
                title="Delete plan"
              >
                {deletingId === plan.id ? "…" : "✕"}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
