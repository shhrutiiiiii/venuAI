"use client";

import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, Lock, ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import "@/app/motion-pages.css";

/** Counts from 0 to `value`; instant under reduced motion. */
export function useCountUp(value: number, duration = 700): number {
  const [n, setN] = useState(0);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !Number.isFinite(value)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setN(value);
      return;
    }
    const start = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      setN(value * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [value, duration]);
  return n;
}

export function CountUp({ value, decimals = 0, suffix = "" }: { value: number; decimals?: number; suffix?: string }) {
  const n = useCountUp(value);
  return <span className="tabular-nums">{n.toFixed(decimals)}{suffix}</span>;
}

export function StatTile({ icon: Icon, label, children, hint, index = 0 }: {
  icon?: React.ElementType; label: string; children: React.ReactNode; hint?: React.ReactNode; index?: number;
}) {
  return (
    <div className="p-surface p-rise p-5 min-w-0" style={{ ["--i" as string]: index, animationDelay: `${index * 45}ms` }}>
      <div className="flex justify-between items-start gap-2 mb-2">
        <span className="text-xs font-semibold" style={{ color: "var(--text-muted)" }}>{label}</span>
        {Icon && <Icon className="w-4 h-4 shrink-0" style={{ color: "var(--text-subtle)" }} />}
      </div>
      <div className="text-2xl sm:text-3xl font-bold truncate">{children}</div>
      {hint && <div className="text-[11px] mt-1.5" style={{ color: "var(--text-subtle)" }}>{hint}</div>}
    </div>
  );
}

export function Skeleton({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={`p-skeleton ${className}`} style={style} aria-hidden="true" />;
}

export function SkeletonTiles({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="p-surface p-5 space-y-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-20" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="p-inset p-3 space-y-2">
          <Skeleton className="h-3.5 w-2/3" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title, text, action }: { title?: string; text: string; action?: React.ReactNode }) {
  return (
    <div className="py-8 px-4 text-center space-y-1.5">
      {title && <p className="text-sm font-semibold">{title}</p>}
      <p className="text-xs max-w-sm mx-auto" style={{ color: "var(--text-muted)" }}>{text}</p>
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="p-surface p-4 flex items-start gap-3" style={{ borderColor: "var(--p-bad)" }}>
      <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" style={{ color: "var(--p-bad)" }} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold">Could not load this data</p>
        <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{message}</p>
      </div>
      {onRetry && <button type="button" onClick={onRetry} className="p-btn">Try again</button>}
    </div>
  );
}

export function PageSpinner() {
  return (
    <div className="min-h-screen flex items-center justify-center" role="status" aria-label="Loading">
      <RefreshCw className="w-5 h-5 animate-spin" style={{ color: "var(--text-muted)" }} />
    </div>
  );
}

export function AdminOnly({ what, role }: { what: string; role?: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="p-surface p-6 max-w-md w-full text-center p-rise">
        <div className="w-11 h-11 rounded-full flex items-center justify-center mx-auto mb-4" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)" }}>
          <Lock className="w-5 h-5" />
        </div>
        <h2 className="text-lg font-bold mb-2">Admin access required</h2>
        <p className="text-sm mb-5" style={{ color: "var(--text-muted)" }}>
          {what} is limited to administrators of your organization{role ? `. You are signed in as ${role}.` : "."}
        </p>
        <Link href="/chat" className="p-btn p-btn-primary p-link"><ArrowLeft className="w-3.5 h-3.5" /> Back to chat</Link>
      </div>
    </div>
  );
}

/** Horizontal bar that grows in; width is a real 0..100 percentage. */
export function Meter({ pct, color = "var(--primary-accent)", index = 0, label }: { pct: number; color?: string; index?: number; label: string }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div
      className="h-2 w-full rounded-full overflow-hidden"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}
      role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(clamped)}
    >
      <div className="h-full rounded-full p-bar-x" style={{ width: `${clamped}%`, background: color, ["--i" as string]: index }} />
    </div>
  );
}
