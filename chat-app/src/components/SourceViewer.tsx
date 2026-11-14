"use client";

import React, { useEffect, useMemo, useState } from "react";
import { BookOpen, X } from "lucide-react";
import type { RetrievedChunk } from "@/lib/apiContracts";
import type { Citation } from "@/lib/answerTypes";

/**
 * Find [start, end) of the cited quote inside a chunk's text.
 * Backend offsets are used only if they actually match the quote; otherwise a
 * whitespace/case-insensitive search is tried. null = not located (we never guess).
 */
export function locateQuote(text: string, citation: Citation | undefined): [number, number] | null {
  if (!citation) return null;
  const quote = (citation.quote ?? "").trim();
  const { char_start: s, char_end: e } = citation;
  if (typeof s === "number" && typeof e === "number" && s >= 0 && e > s && e <= text.length) {
    if (!quote || text.slice(s, e).replace(/\s+/g, " ").trim().toLowerCase() === quote.replace(/\s+/g, " ").toLowerCase()) return [s, e];
  }
  if (!quote) return null;
  const idx = text.toLowerCase().indexOf(quote.toLowerCase());
  if (idx !== -1) return [idx, idx + quote.length];
  // whitespace-insensitive fallback
  const pattern = quote
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("\\s+");
  if (!pattern) return null;
  const m = new RegExp(pattern, "i").exec(text);
  return m ? [m.index, m.index + m[0].length] : null;
}

function matchCitation(chunk: RetrievedChunk, citations: Citation[]): Citation | undefined {
  return (
    citations.find((c) => c.chunk_id && c.chunk_id === chunk.id) ||
    citations.find((c) => c.filename === chunk.doc && (c.page ?? null) === (chunk.page ?? null) && !!c.quote)
  );
}

/** Button + modal listing retrieved passages, highlighting the cited quote where it can be located. */
export function SourceViewerButton({ chunks, citations }: { chunks?: RetrievedChunk[]; citations?: Citation[] }) {
  const [open, setOpen] = useState(false);
  if (!chunks || chunks.length === 0) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold hover:bg-slate-500/10 no-print"
        style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}
      >
        <BookOpen className="w-3 h-3" /> Passages ({chunks.length})
      </button>
      {open && <SourceViewer chunks={chunks} citations={citations ?? []} onClose={() => setOpen(false)} />}
    </>
  );
}

export function SourceViewer({ chunks, citations, onClose }: { chunks: RetrievedChunk[]; citations: Citation[]; onClose: () => void }) {
  const [selected, setSelected] = useState(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const chunk = chunks[Math.min(selected, chunks.length - 1)];
  const citation = useMemo(() => matchCitation(chunk, citations), [chunk, citations]);
  const range = useMemo(() => locateQuote(chunk.text, citation), [chunk, citation]);

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-3 no-print" role="dialog" aria-modal="true" aria-label="Source passages">
      <button type="button" aria-label="Close passages" className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div
        className="relative w-full max-w-2xl max-h-[85vh] overflow-hidden rounded-2xl border flex flex-col"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
      >
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b" style={{ borderColor: "var(--border-subtle)" }}>
          <h2 className="text-sm font-bold flex items-center gap-2">
            <BookOpen className="w-4 h-4" /> Retrieved passages
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="p-1 rounded-lg hover:bg-slate-500/10">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex flex-col sm:flex-row min-h-0 flex-1">
          <ul className="sm:w-56 shrink-0 max-h-32 sm:max-h-none overflow-y-auto border-b sm:border-b-0 sm:border-r list-none" style={{ borderColor: "var(--border-subtle)" }}>
            {chunks.map((c, i) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => setSelected(i)}
                  aria-current={i === selected}
                  className={`w-full text-left px-3 py-2 text-[11px] break-words hover:bg-slate-500/10 ${i === selected ? "bg-slate-500/10 font-bold" : ""}`}
                >
                  {c.doc}
                  {c.page != null ? `, p. ${c.page}` : ""}
                </button>
              </li>
            ))}
          </ul>
          <div className="flex-1 min-w-0 overflow-y-auto p-4 space-y-2 text-xs">
            <div className="text-[10px] flex flex-wrap gap-x-3" style={{ color: "var(--text-subtle)" }}>
              {chunk.retriever && <span>retriever: {chunk.retriever}</span>}
              {typeof chunk.score === "number" && <span>score: {chunk.score.toFixed(3)}</span>}
              {citation?.verified === true && <span className="text-emerald-500">quote verified</span>}
            </div>
            <p className="whitespace-pre-wrap break-words leading-relaxed">
              {range ? (
                <>
                  {chunk.text.slice(0, range[0])}
                  <mark className="rounded px-0.5" style={{ background: "rgba(250,204,21,0.35)", color: "inherit" }}>
                    {chunk.text.slice(range[0], range[1])}
                  </mark>
                  {chunk.text.slice(range[1])}
                </>
              ) : (
                chunk.text
              )}
            </p>
            {citation && !range && (
              <p className="text-[10px]" style={{ color: "var(--text-subtle)" }}>
                The cited quote could not be located in this passage text.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default SourceViewer;
