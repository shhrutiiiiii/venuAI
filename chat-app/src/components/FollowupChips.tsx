"use client";

import React from "react";
import { CornerDownRight } from "lucide-react";

/** Up to 3 suggested follow-up questions; clicking one sends it as the next question. */
export function FollowupChips({ followups, onPick, disabled }: { followups?: string[]; onPick: (q: string) => void; disabled?: boolean }) {
  const items = (followups ?? []).filter((q) => typeof q === "string" && q.trim()).slice(0, 3);
  if (!items.length) return null;
  return (
    <div className="m-stagger flex flex-wrap gap-1.5 no-print" aria-label="Suggested follow-up questions">
      {items.map((q) => (
        <button
          key={q}
          type="button"
          disabled={disabled}
          onClick={() => onPick(q)}
          className="inline-flex items-center gap-1 max-w-full px-2.5 py-1 rounded-full border text-[11px] font-semibold text-left m-press m-hover disabled:opacity-50"
          style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}
        >
          <CornerDownRight className="w-3 h-3 shrink-0" />
          <span className="break-words min-w-0">{q}</span>
        </button>
      ))}
    </div>
  );
}

export default FollowupChips;
