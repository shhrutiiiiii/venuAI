"use client";

import React from "react";
import Link from "next/link";
import { ArrowLeft, RefreshCw } from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import "@/app/motion-pages.css";

export type PageKey = "workspace" | "evidence" | "security" | "analytics";

const NAV: { key: PageKey; href: string; label: string; adminOnly?: boolean }[] = [
  { key: "workspace", href: "/workspace", label: "Workspace" },
  { key: "evidence", href: "/evidence", label: "Evidence" },
  { key: "security", href: "/security", label: "Security", adminOnly: true },
  { key: "analytics", href: "/analytics", label: "Analytics", adminOnly: true },
];

interface ShellProps {
  current: PageKey;
  title: string;
  subtitle?: React.ReactNode;
  icon: React.ElementType;
  isAdmin: boolean;
  onRefresh?: () => void;
  refreshing?: boolean;
  children: React.ReactNode;
  wide?: boolean;
}

/** Shared header: back-to-chat, sibling-page nav, refresh, theme toggle. */
export default function PageShell({ current, title, subtitle, icon: Icon, isAdmin, onRefresh, refreshing, children, wide }: ShellProps) {
  return (
    <div className="min-h-screen px-4 py-6 sm:px-6 md:px-10 md:py-8" style={{ color: "var(--text-main)" }}>
      <div className={`${wide ? "max-w-7xl" : "max-w-6xl"} mx-auto space-y-6 min-w-0`}>
        <header className="space-y-4 pb-5 border-b" style={{ borderColor: "var(--border-subtle)" }}>
          <div className="flex items-center justify-between gap-3">
            <Link href="/chat" className="p-btn p-link"><ArrowLeft className="w-3.5 h-3.5" /> Back to chat</Link>
            <div className="flex items-center gap-2">
              {onRefresh && (
                <button type="button" onClick={onRefresh} disabled={refreshing} className="p-btn" aria-label="Refresh">
                  <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
                  <span className="hidden sm:inline">Refresh</span>
                </button>
              )}
              <ThemeToggle />
            </div>
          </div>
          <div className="flex items-start gap-3 min-w-0">
            <div className="p-2 rounded-xl shrink-0" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
              <Icon className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h1 className="text-xl sm:text-2xl font-bold leading-tight">{title}</h1>
              {subtitle && <p className="text-xs sm:text-sm mt-1 max-w-2xl" style={{ color: "var(--text-muted)" }}>{subtitle}</p>}
            </div>
          </div>
          <nav aria-label="Sections" className="p-tabs flex gap-1 overflow-x-auto -mx-1 px-1">
            {NAV.filter((n) => !n.adminOnly || isAdmin).map((n) => {
              const active = n.key === current;
              return (
                <Link
                  key={n.key}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className="p-tab p-link px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap"
                  style={{
                    background: active ? "var(--bg-card)" : "transparent",
                    border: `1px solid ${active ? "var(--border-subtle)" : "transparent"}`,
                    color: active ? "var(--text-main)" : "var(--text-muted)",
                  }}
                >
                  {n.label}
                </Link>
              );
            })}
          </nav>
        </header>
        {children}
      </div>
    </div>
  );
}
