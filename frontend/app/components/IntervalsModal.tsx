"use client";

import { useState } from "react";
import { apiFetch } from "../hooks/useAuth";

export default function IntervalsModal({
  onClose,
  onConnected,
  reconnect = false,
}: {
  onClose: () => void;
  onConnected: (athleteName: string) => void;
  reconnect?: boolean;
}) {
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    const res = await apiFetch("/intervals/auth", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    });
    setLoading(false);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(d.detail ?? "Authentication failed");
      return;
    }
    const d = await res.json();
    onConnected(d.athlete_name);
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl shadow-xl p-6 w-full max-w-sm mx-4">
        <h3 className="text-base font-semibold text-gray-900 mb-1">
          {reconnect ? "Reconnect Intervals.icu" : "Connect Intervals.icu"}
        </h3>
        <p className="text-xs text-gray-400 mb-4">
          {reconnect
            ? "Your API key is no longer valid. Enter a new one to reconnect."
            : "Enter your Intervals.icu API key. Find it in Settings → Developer on intervals.icu."}
        </p>
        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            type="password"
            placeholder="API Key"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            required
            className="w-full border border-gray-300 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          {error && <p className="text-xs text-red-500">{error}</p>}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 border border-gray-200 text-gray-600 rounded-xl py-2 text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white rounded-xl py-2 text-sm font-medium"
            >
              {loading ? "Connecting…" : "Connect"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
