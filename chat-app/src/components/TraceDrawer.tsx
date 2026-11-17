"use client";

import React, { useEffect, useState } from "react";
import { X, Activity } from "lucide-react";
import type { Trace } from "@/lib/apiContracts";

/** Button that opens the trace drawer. Render only when capabilities.traces && trace_id exist. */
export function TraceButton({ traceId }: { traceId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold m-press m-hover no-print"
        style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}
      >
        <Activity className="w-3 h-3" /> Trace
      </button>
      {open && <TraceDrawer traceId={traceId} onClose={() => setOpen(false)} />}
    </>
  );
}

type LoadState = { kind: "loading" } | { kind: "ok"; trace: Trace } | { kind: "unavailable"; message: string };

export function TraceDrawer({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    fetch(`/api/traces/${encodeURIComponent(traceId)}`, { cache: "no-store" })
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) {
          setState({ kind: "unavailable", message: r.status === 404 ? "No trace stored for this answer." : "Traces are not available right now." });
          return;
        }
        setState({ kind: "ok", trace: (await r.json()) as Trace });
      })
      .catch(() => alive && setState({ kind: "unavailable", message: "Traces are not available right now." }));
    return () => {
      alive = false;
    };
  }, [traceId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[80] flex justify-end no-print" role="dialog" aria-modal="true" aria-label="Request trace">
      <button type="button" aria-label="Close trace" className="absolute inset-0 m-scrim" onClick={onClose} />
      <aside
        className="m-drawer-right relative w-full max-w-md h-full overflow-y-auto p-4 space-y-4 border-l text-xs"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
      >
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-bold flex items-center gap-2">
            <Activity className="w-4 h-4" /> Trace
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="p-1 rounded-lg hover:bg-slate-500/10">
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="font-mono text-[10px] break-all" style={{ color: "var(--text-subtle)" }}>
          {traceId}
        </p>
        {state.kind === "loading" && <p style={{ color: "var(--text-muted)" }}>Loading...</p>}
        {state.kind === "unavailable" && <p style={{ color: "var(--text-muted)" }}>{state.message}</p>}
        {state.kind === "ok" && <TraceBody trace={state.trace} />}
      </aside>
    </div>
  );
}

const th = "text-left font-bold pr-2 py-1";
const td = "pr-2 py-1 align-top break-words";

function TraceBody({ trace }: { trace: Trace }) {
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
        {typeof trace.total_ms === "number" && (
          <>
            <dt style={{ color: "var(--text-muted)" }}>Total</dt>
            <dd>{trace.total_ms} ms</dd>
          </>
        )}
        {typeof trace.cache_hit === "boolean" && (
          <>
            <dt style={{ color: "var(--text-muted)" }}>Cache</dt>
            <dd>{trace.cache_hit ? "hit" : "miss"}</dd>
          </>
        )}
        {trace.model_path && trace.model_path.length > 0 && (
          <>
            <dt style={{ color: "var(--text-muted)" }}>Model path</dt>
            <dd className="break-words">{trace.model_path.join(" > ")}</dd>
          </>
        )}
        {trace.prompt_version && (
          <>
            <dt style={{ color: "var(--text-muted)" }}>Prompt</dt>
            <dd className="break-words">{trace.prompt_version}</dd>
          </>
        )}
      </dl>

      {trace.steps && trace.steps.length > 0 && (
        <section>
          <h3 className="font-bold mb-1">Steps</h3>
          <table className="w-full table-fixed">
            <thead>
              <tr>
                <th className={th}>Step</th>
                <th className={th}>Start</th>
                <th className={th}>End</th>
                <th className={th}>Status</th>
              </tr>
            </thead>
            <tbody>
              {trace.steps.map((s, i) => (
                <tr key={`${s.id}-${i}`}>
                  <td className={td}>{s.label || s.id}</td>
                  <td className={td}>{s.t_start_ms ?? "-"}</td>
                  <td className={td}>{s.t_end_ms ?? "-"}</td>
                  <td className={td}>{s.status ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {trace.llm_calls && trace.llm_calls.length > 0 && (
        <section>
          <h3 className="font-bold mb-1">LLM calls</h3>
          <ul className="space-y-1 list-none">
            {trace.llm_calls.map((c, i) => (
              <li key={i} className="break-words">
                <span className="font-mono">{c.model}</span>
                {c.task ? ` (${c.task})` : ""}
                {typeof c.prompt_tokens === "number" || typeof c.completion_tokens === "number"
                  ? ` · ${c.prompt_tokens ?? "?"} in / ${c.completion_tokens ?? "?"} out`
                  : ""}
                {typeof c.latency_ms === "number" ? ` · ${c.latency_ms} ms` : ""}
                {c.cached ? " · cached" : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {trace.retrieved && trace.retrieved.length > 0 && (
        <section>
          <h3 className="font-bold mb-1">Retrieved ({trace.retrieved.length})</h3>
          <ul className="space-y-1 list-none">
            {trace.retrieved.map((r) => (
              <li key={r.id} className="break-words">
                {r.doc || r.id}
                {r.retriever ? ` · ${r.retriever}` : ""}
                {typeof r.score === "number" ? ` · score ${r.score.toFixed(3)}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {trace.verification && Object.keys(trace.verification).length > 0 && (
        <section>
          <h3 className="font-bold mb-1">Verification</h3>
          <pre className="whitespace-pre-wrap break-words text-[10px] p-2 rounded-lg" style={{ background: "var(--bg-input)" }}>
            {JSON.stringify(trace.verification, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}

export default TraceDrawer;
