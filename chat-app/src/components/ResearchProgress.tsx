"use client";

import React from "react";
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { ResearchJob } from "@/lib/apiContracts";
import type { TimelineStep } from "@/lib/sse";

const PHASE_LABEL: Record<string, string> = {
  planning: "Planning",
  researching: "Researching sub-questions",
  synthesizing: "Writing the report",
  citing: "Adding citations",
  verifying: "Verifying against sources",
};

function SubtaskIcon({ status }: { status: string }) {
  if (status === "done") return <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-500" />;
  if (status === "failed" || status === "error") return <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-rose-500" />;
  if (status === "running") return <Loader2 className="w-3.5 h-3.5 mt-0.5 shrink-0 animate-spin" />;
  return <Circle className="w-3.5 h-3.5 mt-0.5 shrink-0" />;
}

/** Live progress of a research job: phase, per-sub-question status, latest step events. */
export function ResearchProgress({
  job,
  steps,
  transport,
  onCancel,
}: {
  job: ResearchJob | null;
  steps: TimelineStep[];
  transport: "live" | "polling";
  onCancel?: () => void;
}) {
  const progress = job?.progress;
  const total = progress?.total ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round(((progress?.done ?? 0) / total) * 100)) : null;
  const lastStep = steps[steps.length - 1];
  return (
    <div className="p-3.5 rounded-xl border text-xs space-y-3" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
      <div className="flex items-center gap-2 font-bold flex-wrap" style={{ color: "var(--text-main)" }}>
        <Loader2 className="w-4 h-4 animate-spin" />
        <span>{progress ? PHASE_LABEL[progress.phase] ?? progress.phase : job?.status === "queued" ? "Queued" : "Starting"}</span>
        {total > 0 && (
          <span className="font-medium" style={{ color: "var(--text-muted)" }}>
            {progress?.done ?? 0}/{total}
          </span>
        )}
        <span className="text-[10px] font-medium" style={{ color: "var(--text-subtle)" }}>
          ({transport === "live" ? "live updates" : "polling"})
        </span>
      </div>
      {pct !== null && (
        <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-input)" }} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-purple-500" style={{ width: `${pct}%` }} />
        </div>
      )}
      {job?.subtasks && job.subtasks.length > 0 && (
        <ul className="space-y-1.5 list-none">
          {job.subtasks.map((s) => (
            <li key={s.id} className="flex items-start gap-2 min-w-0">
              <SubtaskIcon status={s.status} />
              <span className="min-w-0 flex-1 break-words" style={{ color: "var(--text-main)" }}>
                {s.text}
              </span>
              <span className="shrink-0 text-[10px]" style={{ color: "var(--text-subtle)" }}>
                {typeof s.findings_count === "number" ? `${s.findings_count} findings` : s.status}
              </span>
            </li>
          ))}
        </ul>
      )}
      {lastStep && <p className="text-[10px] break-words">Latest: {lastStep.label}</p>}
      {onCancel && (
        <button type="button" onClick={onCancel} className="px-2.5 py-1 rounded-lg border font-bold hover:bg-slate-500/10" style={{ borderColor: "var(--border-subtle)" }}>
          Cancel research
        </button>
      )}
    </div>
  );
}

export default ResearchProgress;
