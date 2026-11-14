"use client";

import React, { useEffect, useState } from "react";
import { ShieldQuestion } from "lucide-react";
import type { PendingApproval } from "@/lib/apiContracts";

function preview(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/** Modal asking the user to allow or deny a tool call the agent wants to make. */
export function ApprovalDialog({
  approval,
  busy,
  error,
  onDecide,
  onDismiss,
}: {
  approval: PendingApproval;
  busy: boolean;
  error?: string | null;
  onDecide: (approve: boolean, remember: boolean) => void;
  onDismiss: () => void;
}) {
  const [remember, setRemember] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onDismiss();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onDismiss]);

  const args = preview(approval.args_preview);
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center p-3 no-print" role="alertdialog" aria-modal="true" aria-labelledby="approval-title">
      <div className="absolute inset-0 m-scrim" />
      <div
        className="m-dialog relative w-full max-w-md rounded-2xl border p-4 space-y-3 text-xs"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
      >
        <h2 id="approval-title" className="text-sm font-bold flex items-center gap-2">
          <ShieldQuestion className="w-4 h-4 text-amber-500" /> Approval required
        </h2>
        <p style={{ color: "var(--text-muted)" }}>
          The assistant wants to run the tool <span className="font-mono font-bold" style={{ color: "var(--text-main)" }}>{approval.tool}</span>. It will not run unless you approve.
        </p>
        {args && (
          <pre className="whitespace-pre-wrap break-words max-h-48 overflow-y-auto text-[11px] p-2 rounded-lg" style={{ background: "var(--bg-input)" }}>
            {args}
          </pre>
        )}
        {approval.description_hash && (
          <p className="text-[10px] font-mono break-all" style={{ color: "var(--text-subtle)" }}>
            tool definition hash: {approval.description_hash}
          </p>
        )}
        <label className="flex items-center gap-2 select-none">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} disabled={busy} />
          Remember this decision for this tool
        </label>
        {error && <p className="text-rose-500 break-words">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => onDecide(false, remember)} className="m-press m-hover px-3 py-1.5 rounded-xl border font-bold disabled:opacity-50" style={{ borderColor: "var(--border-subtle)" }}>
            Deny
          </button>
          <button type="button" disabled={busy} onClick={() => onDecide(true, remember)} className="m-press px-3 py-1.5 rounded-xl font-bold disabled:opacity-50" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
            {busy ? "Working..." : "Approve"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ApprovalDialog;
