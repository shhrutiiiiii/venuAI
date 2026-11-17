"use client";

import React from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { TimelineStep } from "@/lib/sse";

/**
 * Collapsible timeline of pipeline steps driven by `{"step":{...}}` stream events.
 * Renders nothing when the backend sent no step events. Unknown step ids are shown as-is.
 */
export function StepTimeline({ steps, live }: { steps: TimelineStep[]; live: boolean }) {
  if (!steps.length) return null;
  const running = steps.find((s) => s.status === "running");
  const summary = live && running ? running.label : `${steps.length} step${steps.length === 1 ? "" : "s"}`;
  return (
    <details
      open={live}
      className="rounded-xl border text-[11px] no-print"
      style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}
    >
      <summary className="px-3 py-1.5 cursor-pointer font-bold select-none flex items-center gap-2">
        {live ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />}
        <span className="truncate">{summary}</span>
      </summary>
      <ol className="px-3 pb-2 pt-1 space-y-1 list-none" aria-label="Pipeline steps">
        {steps.map((s) => (
          <li key={s.id} className="flex items-start gap-2 min-w-0">
            {s.status === "running" ? (
              <Loader2 className="w-3.5 h-3.5 mt-0.5 shrink-0 animate-spin" />
            ) : s.status === "error" ? (
              <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-rose-500" />
            ) : (
              <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-500" />
            )}
            <span className="min-w-0 break-words flex-1" style={{ color: "var(--text-main)" }}>
              {s.label}
              {s.detail && Object.keys(s.detail).length > 0 && (
                <span className="ml-1.5 text-[10px]" style={{ color: "var(--text-subtle)" }}>
                  {Object.entries(s.detail)
                    .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
                    .map(([k, v]) => `${k}: ${String(v)}`)
                    .join(" · ")}
                </span>
              )}
            </span>
            {typeof s.t_ms === "number" && (
              <span className="shrink-0 font-mono text-[10px]" style={{ color: "var(--text-subtle)" }}>
                {s.t_ms} ms
              </span>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

export default StepTimeline;
