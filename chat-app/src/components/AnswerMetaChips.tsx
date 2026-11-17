"use client";

import React from "react";
import { Zap, GitBranch, Timer, RefreshCw } from "lucide-react";
import type { QueryExtras } from "@/lib/apiContracts";

/** "openai/gpt-oss-20b" -> "20b"; falls back to the id tail. */
export function shortModel(id: string): string {
  const m = id.match(/(\d+(?:\.\d+)?b)\b/i);
  return m ? m[1].toLowerCase() : id.split("/").pop() || id;
}

/** Human description of the cascade, or null when no model path was reported. */
export function describeModelPath(path?: string[]): string | null {
  if (!Array.isArray(path) || path.length === 0) return null;
  const names = path.map(shortModel);
  if (names.length === 1) return `answered by ${names[0]}`;
  return `answered by ${names[0]}, escalated to ${names.slice(1).join(" then ")}`;
}

const chip = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold";
const chipStyle = { borderColor: "var(--border-subtle)", color: "var(--text-muted)" };

/** Cache-hit, model-path, latency and corrective-loop chips. Only fields the backend reported are shown. */
export function AnswerMetaChips({ extras }: { extras: QueryExtras }) {
  const path = describeModelPath(extras.model_path);
  const corrective = extras.corrective && extras.corrective.iterations > 0 ? extras.corrective : null;
  const hasLatency = typeof extras.latency_ms === "number";
  if (!extras.cache_hit && !path && !corrective && !hasLatency) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 no-print">
      {extras.cache_hit === true && (
        <span className={chip} style={{ borderColor: "rgba(16,185,129,0.4)", color: "#10b981" }} title="Served from the answer cache">
          <Zap className="w-3 h-3" /> cache hit
        </span>
      )}
      {path && (
        <span className={chip} style={chipStyle} title={(extras.model_path ?? []).join(" -> ")}>
          <GitBranch className="w-3 h-3" /> {path}
        </span>
      )}
      {corrective && (
        <span className={chip} style={chipStyle} title={corrective.actions.join(", ")}>
          <RefreshCw className="w-3 h-3" /> corrected x{corrective.iterations}
          {corrective.actions.includes("abstain") ? " (abstained)" : ""}
        </span>
      )}
      {hasLatency && (
        <span className={chip} style={chipStyle}>
          <Timer className="w-3 h-3" /> {((extras.latency_ms as number) / 1000).toFixed(1)}s
          {typeof extras.ttft_ms === "number" ? ` · first token ${extras.ttft_ms} ms` : ""}
          {extras.usage ? ` · ${extras.usage.total_tokens} tokens` : ""}
        </span>
      )}
    </div>
  );
}

export default AnswerMetaChips;
