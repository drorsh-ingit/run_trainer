"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "./hooks/useAuth";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    if (getToken()) {
      router.replace("/dashboard");
    } else {
      router.replace("/login");
    }
  }, [router]);

  return null;
}
