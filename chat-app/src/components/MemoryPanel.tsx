"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Brain, Trash2, X } from "lucide-react";
import type { MemoryItem } from "@/lib/apiContracts";
import { isUnavailableStatus } from "@/lib/useCapabilities";

const OPT_IN_KEY = "venuai_memory_opt_in";

function readStoredOptIn(): boolean | null {
  try {
    const v = localStorage.getItem(OPT_IN_KEY);
    return v === "1" ? true : v === "0" ? false : null;
  } catch {
    return null;
  }
}

/** Header button + panel. Render only when capabilities.memory is true. */
export function MemoryButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="px-2.5 py-1.5 rounded-xl text-xs font-semibold border transition-all inline-flex items-center gap-1"
        style={{ background: "var(--bg-input)", color: "var(--text-muted)", borderColor: "var(--border-subtle)" }}
        title="Long-term memory"
      >
        <Brain className="w-3.5 h-3.5" /> Memory
      </button>
      {open && <MemoryPanel onClose={() => setOpen(false)} />}
    </>
  );
}

type ListState = { kind: "loading" } | { kind: "ok"; items: MemoryItem[] } | { kind: "unavailable"; message: string };

export function MemoryPanel({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  // The backend has no "read setting" endpoint, so this reflects the last value set from this browser.
  const [optIn, setOptIn] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/memory", { cache: "no-store" });
      if (!r.ok) {
        setList({
          kind: "unavailable",
          message: isUnavailableStatus(r.status) ? "Long-term memory is not enabled on this server." : "Could not load memories.",
        });
        return;
      }
      const data = (await r.json()) as unknown;
      setList({ kind: "ok", items: Array.isArray(data) ? (data as MemoryItem[]) : [] });
    } catch {
      setList({ kind: "unavailable", message: "Could not load memories." });
    }
  }, []);

  useEffect(() => {
    // Initial load; setState happens after the awaited fetch resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOptIn(readStoredOptIn());
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggle = async () => {
    const next = !(optIn ?? false);
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/memory/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: next }) });
      if (!r.ok) {
        setError(isUnavailableStatus(r.status) ? "Memory is disabled on this server (LONG_TERM_MEMORY is off)." : "Could not change the setting.");
      } else {
        setOptIn(next);
        try {
          localStorage.setItem(OPT_IN_KEY, next ? "1" : "0");
        } catch {
          /* storage blocked */
        }
      }
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const forget = async (id?: string) => {
    if (!id && !window.confirm("Forget everything the assistant remembers about you?")) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(id ? `/api/memory/${encodeURIComponent(id)}` : "/api/memory", { method: "DELETE" });
      if (!r.ok) setError("Could not delete.");
      else await load();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex justify-end no-print" role="dialog" aria-modal="true" aria-label="Memory">
      <button type="button" aria-label="Close memory" className="absolute inset-0 m-scrim" onClick={onClose} />
      <aside className="m-drawer-right relative w-full max-w-md h-full overflow-y-auto p-4 space-y-4 border-l text-xs" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-bold flex items-center gap-2">
            <Brain className="w-4 h-4" /> Memory
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="m-press m-hover p-1 rounded-lg">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-3 rounded-xl border space-y-2" style={{ borderColor: "var(--border-subtle)" }}>
          <label className="flex items-center justify-between gap-3 select-none">
            <span className="font-bold">Remember facts and preferences from my chats</span>
            <input type="checkbox" role="switch" checked={optIn === true} disabled={busy} onChange={toggle} />
          </label>
          <p style={{ color: "var(--text-muted)" }}>
            Off by default. {optIn === null ? "Current state is not known to this browser yet; toggling sets it." : "Shows the last value set from this browser."}
          </p>
        </div>

        {error && <p className="text-rose-500 break-words">{error}</p>}
        {list.kind === "loading" && <p style={{ color: "var(--text-muted)" }}>Loading...</p>}
        {list.kind === "unavailable" && <p style={{ color: "var(--text-muted)" }}>{list.message}</p>}
        {list.kind === "ok" && (
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="font-bold">Stored memories ({list.items.length})</h3>
              {list.items.length > 0 && (
                <button type="button" disabled={busy} onClick={() => forget()} className="px-2 py-1 rounded-lg border font-bold text-rose-500 disabled:opacity-50" style={{ borderColor: "rgba(244,63,94,0.4)" }}>
                  Forget all
                </button>
              )}
            </div>
            {list.items.length === 0 && <p style={{ color: "var(--text-muted)" }}>Nothing stored.</p>}
            <ul className="space-y-2 list-none">
              {list.items.map((m) => (
                <li key={m.id} className="flex items-start gap-2 p-2 rounded-xl border" style={{ borderColor: "var(--border-subtle)" }}>
                  <div className="min-w-0 flex-1 break-words">
                    <p>{m.text}</p>
                    <p className="text-[10px] mt-0.5" style={{ color: "var(--text-subtle)" }}>
                      {m.kind}
                      {m.created_at ? ` · ${new Date(m.created_at).toLocaleDateString()}` : ""}
                    </p>
                  </div>
                  <button type="button" disabled={busy} onClick={() => forget(m.id)} aria-label="Delete memory" className="p-1 rounded-lg hover:bg-rose-500/10 text-rose-500 disabled:opacity-50">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </aside>
    </div>
  );
}

export default MemoryPanel;
