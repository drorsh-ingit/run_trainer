"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { clearToken, getUsername } from "../hooks/useAuth";

export default function Nav() {
  const router = useRouter();
  const pathname = usePathname();
  const isAdmin = getUsername() === "admin";

  const logout = () => {
    clearToken();
    router.replace("/login");
  };

  const navLink = (href: string, label: string) => {
    const active = pathname === href || pathname.startsWith(href + "/");
    return active ? (
      <span className="text-sm font-medium text-blue-600 cursor-default">{label}</span>
    ) : (
      <Link href={href} className="text-sm text-gray-600 hover:text-gray-900">
        {label}
      </Link>
    );
  };

  return (
    <nav className="bg-white border-b border-gray-200 sticky top-0 z-10">
      <div className="max-w-4xl mx-auto px-4 h-14 flex items-center justify-between">
        <Link href="/dashboard" className="flex items-center gap-2 text-base font-semibold text-gray-900 hover:text-blue-600">
          <img src="/icon.png" alt="" className="w-7 h-7 rounded-md" />
          Run Trainer
        </Link>
        <div className="flex items-center gap-6">
          {navLink("/dashboard", "Dashboard")}
          {navLink("/plans/new", "New Plan")}
          {navLink("/settings", "Settings")}
          {isAdmin && navLink("/admin", "Admin")}
          <button onClick={logout} className="text-sm text-gray-400 hover:text-gray-700">
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}
