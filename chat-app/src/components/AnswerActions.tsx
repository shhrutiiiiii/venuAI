"use client";

import React, { useState } from "react";
import { Copy, Check, Download, Printer } from "lucide-react";
import { downloadMarkdown, slugify } from "@/lib/answerTypes";

/** Copy as Markdown / Download .md / Print (print stylesheet in globals.css). */
export function AnswerActions({ markdown, title }: { markdown: string; title: string }) {
  const [copied, setCopied] = useState(false);
  const btn = "inline-flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold hover:bg-slate-500/10 transition-colors";
  const style = { borderColor: "var(--border-subtle)", color: "var(--text-muted)" };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable */
    }
  };

  const print = () => {
    document.querySelectorAll(".print-target").forEach((el) => el.classList.remove("print-target"));
    const el = (document.activeElement as HTMLElement | null)?.closest(".print-scope");
    el?.classList.add("print-target");
    document.body.classList.add("printing-one");
    window.print();
    document.body.classList.remove("printing-one");
    el?.classList.remove("print-target");
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5 no-print">
      <button type="button" onClick={copy} className={btn} style={style}>
        {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
        {copied ? "Copied" : "Copy as Markdown"}
      </button>
      <button type="button" onClick={() => downloadMarkdown(slugify(title), markdown)} className={btn} style={style}>
        <Download className="w-3 h-3" /> Download .md
      </button>
      <button type="button" onClick={print} className={btn} style={style}>
        <Printer className="w-3 h-3" /> Print / PDF
      </button>
    </div>
  );
}
