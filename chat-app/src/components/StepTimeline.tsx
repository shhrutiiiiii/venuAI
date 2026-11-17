"use client";

import React from "react";
import { Check, ChevronDown, X } from "lucide-react";
import type { TimelineStep } from "@/lib/sse";

/**
 * Collapsible timeline of pipeline steps driven by `{"step":{...}}` stream events.
 * Renders nothing when the backend sent no step events. Unknown step ids are shown as-is.
 * Each step enters with a short rise; the active step has a pulsing node and a connector
 * line that fills as steps complete.
 */
export function StepTimeline({ steps, live }: { steps: TimelineStep[]; live: boolean }) {
  if (!steps.length) return null;
  const running = steps.find((s) => s.status === "running");
  const summary = live && running ? running.label : `${steps.length} step${steps.length === 1 ? "" : "s"}`;
  return (
    <details
      open={live}
      className="group rounded-xl border text-[11px] no-print"
      style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}
    >
      <summary className="m-press px-3 py-2 cursor-pointer font-semibold select-none flex items-center gap-2 list-none [&::-webkit-details-marker]:hidden">
        {live ? (
          <span className="m-dots" style={{ color: "var(--text-main)" }} aria-hidden="true"><i /><i /><i /></span>
        ) : (
          <Check className="w-3.5 h-3.5" style={{ color: "var(--ok)" }} aria-hidden="true" />
        )}
        <span className="truncate flex-1" aria-live="polite">{summary}</span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0 transition-transform group-open:rotate-180" style={{ transitionDuration: "var(--m-base)" }} aria-hidden="true" />
      </summary>
      <ol className="px-3 pb-2.5 pt-0.5 list-none" aria-label="Pipeline steps">
        {steps.map((s, i) => {
          const last = i === steps.length - 1;
          const done = s.status !== "running" && s.status !== "error";
          return (
            <li key={s.id} className="m-rise relative flex items-start gap-2.5 min-w-0 pb-1.5" style={{ animationDuration: "var(--m-base)" }}>
              {!last && (
                <span
                  aria-hidden="true"
                  className="absolute left-[7px] top-4 bottom-0 w-px transition-colors"
                  style={{ background: done ? "var(--ok)" : "var(--border-subtle)", transitionDuration: "var(--m-slow)" }}
                />
              )}
              <span
                aria-hidden="true"
                className="relative mt-0.5 w-[15px] h-[15px] shrink-0 rounded-full flex items-center justify-center border"
                style={{
                  borderColor: s.status === "error" ? "var(--danger)" : done ? "var(--ok)" : "var(--text-main)",
                  background: done ? "var(--ok)" : "transparent",
                  color: "var(--bg-main)",
                }}
              >
                {s.status === "running" ? (
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--text-main)", animation: "m-caret 1s ease-in-out infinite" }} />
                ) : s.status === "error" ? (
                  <X className="w-2.5 h-2.5" style={{ color: "var(--danger)" }} />
                ) : (
                  <Check className="w-2.5 h-2.5" strokeWidth={3} />
                )}
              </span>
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
                <span className="shrink-0 font-mono text-[10px] tabular-nums" style={{ color: "var(--text-subtle)" }}>
                  {s.t_ms} ms
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

export default StepTimeline;
