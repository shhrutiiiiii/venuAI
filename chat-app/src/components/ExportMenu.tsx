"use client";

import React, { useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";
import type { ExportFormat, ReportCitation } from "@/lib/apiContracts";
import { downloadMarkdown, slugify } from "@/lib/answerTypes";
import { useCapabilities } from "@/lib/useCapabilities";

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function filenameFrom(disposition: string | null, fallback: string): string {
  const m = disposition?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  if (!m) return fallback;
  try {
    return decodeURIComponent(m[1]).replace(/[\\/]/g, "_");
  } catch {
    return fallback;
  }
}

/** Export report as MD (always, client side) and DOCX / PDF (only when the export endpoint exists). */
export function ExportMenu({ title, markdown, citations }: { title: string; markdown: string; citations: ReportCitation[] }) {
  const caps = useCapabilities();
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const btn = "inline-flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold hover:bg-slate-500/10 transition-colors disabled:opacity-50";
  const style = { borderColor: "var(--border-subtle)", color: "var(--text-muted)" };

  const run = async (format: ExportFormat) => {
    setError(null);
    setBusy(format);
    try {
      const r = await fetch("/api/export/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format, title, markdown, citations }),
      });
      if (!r.ok) {
        setError(r.status === 413 ? "Report is too large to export." : `Export failed (${r.status}).`);
        return;
      }
      saveBlob(await r.blob(), filenameFrom(r.headers.get("content-disposition"), `${slugify(title)}.${format}`));
    } catch {
      setError("Export failed.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5 no-print">
      <button type="button" onClick={() => downloadMarkdown(slugify(title), markdown)} className={btn} style={style}>
        <Download className="w-3 h-3" /> Markdown
      </button>
      {caps.export && (
        <>
          <button type="button" disabled={busy !== null} onClick={() => run("docx")} className={btn} style={style}>
            {busy === "docx" ? <Loader2 className="w-3 h-3 animate-spin" /> : <FileText className="w-3 h-3" />} DOCX
          </button>
          <button type="button" disabled={busy !== null} onClick={() => run("pdf")} className={btn} style={style}>
            {busy === "pdf" ? <Loader2 className="w-3 h-3 animate-spin" /> : <FileText className="w-3 h-3" />} PDF
          </button>
        </>
      )}
      {error && (
        <span role="alert" className="text-[10px] text-rose-500">
          {error}
        </span>
      )}
    </div>
  );
}

export default ExportMenu;
