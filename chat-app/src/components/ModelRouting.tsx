"use client";

import React, { useState } from "react";
import { Bot, ChevronDown } from "lucide-react";

export interface RegistryModel {
  model_id: string;
  provider: string;
  capabilities: string[];
  enabled: boolean;
  notes?: string;
}

/**
 * Read-only view of the backend model registry. The /query API has no model
 * parameter, so this is informational, not a selector. Registry order is the
 * routing order: first enabled model is primary, the rest are fallbacks.
 */
export function ModelRouting({ models }: { models: RegistryModel[] | null }) {
  const [open, setOpen] = useState(false);
  const enabled = (models ?? []).filter((m) => m.enabled);
  const primary = enabled[0];
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs font-bold max-w-full"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
      >
        <Bot className="w-4 h-4 shrink-0" />
        <span className="truncate">Model routing{primary ? `: ${primary.model_id}` : models === null ? " (loading)" : " (unavailable)"}</span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--text-subtle)" }} />
      </button>
      {open && (
        <div
          className="absolute top-full left-0 mt-2 w-80 max-w-[85vw] p-2 rounded-2xl decent-card space-y-1 z-50"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
        >
          <p className="text-[10px] font-bold uppercase tracking-wider px-2 py-1" style={{ color: "var(--text-subtle)" }}>
            Model routing (read-only, set by the backend)
          </p>
          {enabled.length === 0 && (
            <p className="text-[11px] px-2 py-1" style={{ color: "var(--text-muted)" }}>
              No enabled models reported by the backend registry.
            </p>
          )}
          {enabled.map((m, i) => (
            <div key={m.model_id} className="p-2 rounded-xl">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-xs font-bold break-all" style={{ color: "var(--text-main)" }}>{m.model_id}</span>
                <span className="text-[9px] font-black uppercase px-1 rounded" style={{ background: "var(--badge-border)", color: "var(--badge-text)" }}>
                  {i === 0 ? "Primary" : `Fallback ${i}`}
                </span>
              </div>
              <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                {m.provider}
                {m.capabilities.length ? ` · ${m.capabilities.join(", ")}` : ""}
              </p>
            </div>
          ))}
          <p className="text-[10px] px-2 pb-1" style={{ color: "var(--text-subtle)" }}>
            The actual model used is shown on each answer when the backend reports it.
          </p>
        </div>
      )}
    </div>
  );
}
