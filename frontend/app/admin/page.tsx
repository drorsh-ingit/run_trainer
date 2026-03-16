"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Nav from "../components/Nav";
import { apiFetch, useRequireAuth } from "../hooks/useAuth";

type AdminPlan = {
  id: number;
  username: string;
  goal_distance: number | null;
  goal_date: string | null;
  plan_type: string | null;
  plan_duration_weeks: number | null;
  created_at: string;
};

export default function AdminPage() {
  useRequireAuth();
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch("/admin/plans")
      .then(async res => {
        if (res.status === 403) throw new Error("Admin access required");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setPlans)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      <Nav />
      <div className="max-w-5xl mx-auto px-4 py-10">
        <h1 className="text-2xl font-bold text-gray-900 mb-8">Admin — All Plans</h1>

        {loading && <p className="text-gray-500 text-sm">Loading…</p>}
        {error && <p className="text-red-600 text-sm">{error}</p>}

        {!loading && !error && plans.length === 0 && (
          <p className="text-gray-500 text-sm">No plans in the system.</p>
        )}

        {plans.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">ID</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">User</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Type</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Distance</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Goal Date</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Weeks</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Created</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {plans.map(plan => (
                  <tr key={plan.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3 text-gray-500">{plan.id}</td>
                    <td className="px-4 py-3 font-medium text-gray-900">{plan.username}</td>
                    <td className="px-4 py-3 text-gray-600 capitalize">{plan.plan_type ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">
                      {plan.goal_distance != null ? `${plan.goal_distance} km` : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{plan.goal_date ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{plan.plan_duration_weeks ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-400">
                      {plan.created_at ? plan.created_at.slice(0, 10) : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        href={`/plans/${plan.id}`}
                        className="text-blue-500 hover:text-blue-700 transition-colors"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
