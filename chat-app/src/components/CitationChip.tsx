"use client";

import React, { useState } from "react";
import type { Citation } from "@/lib/answerTypes";

/** Inline [n] chip with hover/click preview (filename, page, quote, verified badge). */
export function CitationChip({ citation, n }: { citation?: Citation; n: number }) {
  const [open, setOpen] = useState(false);
  if (!citation) {
    return <span className="text-[10px] font-bold align-super" style={{ color: "var(--text-subtle)" }}>[{n}]</span>;
  }
  const badge =
    citation.verified === true ? "Quote verified" : citation.verified === false ? "Unverified" : "Source (not quote-checked)";
  const badgeColor =
    citation.verified === true ? "#10b981" : citation.verified === false ? "#f43f5e" : "var(--text-subtle)";
  return (
    <span className="relative inline-block align-baseline" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-expanded={open}
        aria-label={`Citation ${n}: ${citation.filename}`}
        className="m-press mx-0.5 px-1.5 rounded-md text-[10px] font-extrabold border cursor-pointer"
        style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--primary-accent)" }}
      >
        {n}
      </button>
      {(
        <span
          role="tooltip"
          data-open={open}
          aria-hidden={!open}
          className="m-popover absolute z-50 left-0 top-full mt-1 w-64 max-w-[80vw] p-2.5 rounded-xl border text-left text-[11px] shadow-xl whitespace-normal font-normal"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
        >
          <span className="block font-bold truncate">{citation.filename}</span>
          {citation.page != null && <span className="block text-[10px]" style={{ color: "var(--text-subtle)" }}>Page {citation.page}</span>}
          {citation.quote && <span className="block mt-1 italic" style={{ color: "var(--text-muted)" }}>&ldquo;{citation.quote}&rdquo;</span>}
          <span className="block mt-1 text-[10px] font-bold" style={{ color: badgeColor }}>{badge}</span>
        </span>
      )}
    </span>
  );
}
