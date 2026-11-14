"use client";

import React from "react";
import { AlertTriangle } from "lucide-react";
import type { Contradiction } from "@/lib/apiContracts";

/** Shown only when the backend reports conflicting claims across the cited documents. */
export function ContradictionNotice({ contradictions }: { contradictions?: Contradiction[] }) {
  const items = (contradictions ?? []).filter((c) => c && c.claim_a && c.claim_b);
  if (!items.length) return null;
  return (
    <div className="p-3 rounded-xl border text-xs space-y-2" role="note" style={{ borderColor: "rgba(245,158,11,0.4)", background: "rgba(245,158,11,0.08)" }}>
      <div className="flex items-center gap-1.5 font-bold text-amber-500">
        <AlertTriangle className="w-4 h-4 shrink-0" /> Sources disagree ({items.length})
      </div>
      <ul className="space-y-2 list-none">
        {items.map((c, i) => (
          <li key={i} className="space-y-0.5 break-words" style={{ color: "var(--text-main)" }}>
            <div>
              <span className="font-bold">{c.doc_a || "Source A"}:</span> {c.claim_a}
            </div>
            <div>
              <span className="font-bold">{c.doc_b || "Source B"}:</span> {c.claim_b}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default ContradictionNotice;
