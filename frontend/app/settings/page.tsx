"use client";

import { useEffect, useState } from "react";
import Nav from "../components/Nav";
import GarminModal from "../components/GarminModal";
import { apiFetch, useRequireAuth } from "../hooks/useAuth";

export default function SettingsPage() {
  useRequireAuth();

  const [maxHr, setMaxHr] = useState<string>("");
  const [maxHrSaving, setMaxHrSaving] = useState(false);
  const [maxHrMsg, setMaxHrMsg] = useState("");

  const [stravaStatus, setStravaStatus] = useState<{ connected: boolean; athlete_name?: string } | null>(null);
  const [garminStatus, setGarminStatus] = useState<{ connected: boolean; display_name?: string } | null>(null);
  const [showGarminModal, setShowGarminModal] = useState(false);

  useEffect(() => {
    apiFetch("/auth/me")
      .then(r => r.ok ? r.json() : null)
      .then(user => { if (user?.max_hr) setMaxHr(String(user.max_hr)); });

    apiFetch("/strava/status")
      .then(r => r.ok ? r.json() : { connected: false })
      .then(setStravaStatus)
      .catch(() => setStravaStatus({ connected: false }));

    apiFetch("/garmin/status")
      .then(r => r.json())
      .then(setGarminStatus)
      .catch(() => setGarminStatus({ connected: false }));
  }, []);

  const saveMaxHr = async () => {
    const val = parseInt(maxHr);
    if (!val || val < 100 || val > 250) { setMaxHrMsg("Enter a value between 100 and 250"); return; }
    setMaxHrSaving(true);
    setMaxHrMsg("");
    try {
      const res = await apiFetch("/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_hr: val }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMaxHrMsg("Saved!");
    } catch (e: unknown) {
      setMaxHrMsg(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setMaxHrSaving(false);
    }
  };

  const handleStravaConnect = async () => {
    const res = await apiFetch("/strava/auth-url");
    if (!res.ok) { alert("Failed to get Strava auth URL"); return; }
    const { url } = await res.json();
    window.location.href = url;
  };

  const handleStravaDisconnect = async () => {
    await apiFetch("/strava/disconnect", { method: "DELETE" });
    setStravaStatus({ connected: false });
  };

  const handleGarminDisconnect = async () => {
    await apiFetch("/garmin/auth", { method: "DELETE" });
    setGarminStatus({ connected: false });
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <Nav />
      {showGarminModal && (
        <GarminModal
          onClose={() => setShowGarminModal(false)}
          onConnected={displayName => {
            setGarminStatus({ connected: true, display_name: displayName });
            setShowGarminModal(false);
          }}
        />
      )}
      <div className="max-w-2xl mx-auto px-4 py-10 space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>

        {/* Max HR */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Heart Rate</h2>
          <div className="flex items-center gap-3">
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
          <p className="text-xs text-gray-400 mt-3">Used to calculate HR zone distribution when scoring activity matches.</p>
        </div>

        {/* Strava */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Strava</h2>
          {stravaStatus === null ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : stravaStatus.connected ? (
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-800">
                  Connected{stravaStatus.athlete_name ? ` as ${stravaStatus.athlete_name}` : ""}
                </p>
                <span className="inline-block mt-1 text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full">Connected</span>
              </div>
              <button
                onClick={handleStravaDisconnect}
                className="text-sm text-red-400 hover:text-red-600 transition-colors"
              >
                Disconnect
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">Not connected</p>
                <span className="inline-block mt-1 text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">Disconnected</span>
              </div>
              <button
                onClick={handleStravaConnect}
                className="bg-orange-500 hover:bg-orange-600 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
              >
                Connect Strava
              </button>
            </div>
          )}
        </div>

        {/* Garmin */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Garmin</h2>
          {garminStatus === null ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : garminStatus.connected ? (
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-800">
                  Connected{garminStatus.display_name ? ` as ${garminStatus.display_name}` : ""}
                </p>
                <span className="inline-block mt-1 text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full">Connected</span>
              </div>
              <button
                onClick={handleGarminDisconnect}
                className="text-sm text-red-400 hover:text-red-600 transition-colors"
              >
                Disconnect
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-500">Not connected</p>
                <span className="inline-block mt-1 text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">Disconnected</span>
              </div>
              <button
                onClick={() => setShowGarminModal(true)}
                className="bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
              >
                Connect Garmin
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
