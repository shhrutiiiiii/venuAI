"use client";

import React, { useEffect, useState } from "react";

/** Per-user long-term-memory opt-in. Clickable only when the server flag is on. No call is made until clicked. */
export function MemoryOptIn({ serverOn }: { serverOn: boolean }) {
  const [optIn, setOptIn] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!serverOn) return;
    let alive = true;
    fetch("/api/memory/settings", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && d && setOptIn(d.enabled === true))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [serverOn]);

  const toggle = async () => {
    setError(null);
    const next = !optIn;
    const r = await fetch("/api/memory/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: next }) }).catch(() => null);
    if (r?.ok) setOptIn(next);
    else setError("Could not change the setting.");
  };

  return (
    <div>
      <button
        type="button"
        disabled={!serverOn}
        onClick={toggle}
        aria-pressed={optIn === true}
        title={serverOn ? undefined : "Server flag off. Set LONG_TERM_MEMORY=on"}
        className="px-3 py-1.5 rounded-xl text-xs font-semibold border disabled:opacity-50 disabled:cursor-not-allowed"
        style={{ background: "var(--bg-input)", color: "var(--text-main)", borderColor: "var(--border-subtle)" }}
      >
        {optIn ? "Memory opt-in: on (click to turn off)" : "Opt in to memory"}
      </button>
      {!serverOn && <p className="text-[11px] mt-1" style={{ color: "var(--text-subtle)" }}>Server flag off. Set LONG_TERM_MEMORY=on</p>}
      {error && <p role="status" className="text-[11px] mt-1 text-rose-500">{error}</p>}
    </div>
  );
}

/** Per-document "Build summary" (RAPTOR). Disabled while the server flag is off; only ever runs on click. */
export function RaptorBuildButton({ docId, serverOn }: { docId: string; serverOn: boolean }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);

  const run = async () => {
    setState("busy");
    setMsg(null);
    const r = await fetch(`/api/documents/${encodeURIComponent(docId)}/summaries`, { method: "POST" }).catch(() => null);
    if (r?.ok) {
      const d = (await r.json().catch(() => ({}))) as { summaries_stored?: number };
      setMsg(`${d.summaries_stored ?? 0} summaries stored`);
      setState("done");
    } else {
      setMsg(r?.status === 503 ? "RAPTOR is disabled on this server." : "Could not build summaries.");
      setState("error");
    }
  };

  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={!serverOn || state === "busy"}
        onClick={run}
        title={serverOn ? "Build summaries (uses up to 6 LLM calls)" : "Server flag off. Set RAPTOR=on"}
        className="px-2 py-1 rounded-lg text-[11px] font-semibold border disabled:opacity-50 disabled:cursor-not-allowed"
        style={{ background: "var(--bg-input)", color: "var(--text-main)", borderColor: "var(--border-subtle)" }}
      >
        {state === "busy" ? "Building..." : "Build summary"}
      </button>
      {msg && <span role="status" className="text-[10px]" style={{ color: state === "error" ? "#f43f5e" : "var(--text-muted)" }}>{msg}</span>}
    </span>
  );
}
