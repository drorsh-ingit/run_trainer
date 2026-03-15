"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch } from "../../hooks/useAuth";

export default function StravaCallbackPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = useState("Connecting to Strava…");

  useEffect(() => {
    const code = searchParams.get("code");
    const error = searchParams.get("error");

    if (error || !code) {
      setStatus("Strava authorization was denied or failed.");
      setTimeout(() => router.replace("/dashboard"), 3000);
      return;
    }

    apiFetch(`/strava/exchange?code=${encodeURIComponent(code)}`, { method: "POST" })
      .then(async res => {
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.detail ?? `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then(data => {
        setStatus(`Connected as ${data.athlete_name ?? "athlete"}! Redirecting…`);
        setTimeout(() => router.replace("/dashboard"), 1500);
      })
      .catch(e => {
        setStatus(`Connection failed: ${e.message}`);
        setTimeout(() => router.replace("/dashboard"), 3000);
      });
  }, [router, searchParams]);

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-8 max-w-sm w-full text-center">
        <p className="text-gray-700 text-sm">{status}</p>
      </div>
    </div>
  );
}
