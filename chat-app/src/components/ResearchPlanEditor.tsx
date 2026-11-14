"use client";

import React, { useState } from "react";
import { ListChecks, Plus, Trash2 } from "lucide-react";
import type { ResearchBudget, ResearchSubQuestion } from "@/lib/apiContracts";

/** Editable list of sub-questions proposed by the planner; the user confirms before any research tokens are spent. */
export function ResearchPlanEditor({
  initial,
  budget,
  onStart,
  onCancel,
}: {
  initial: ResearchSubQuestion[];
  budget: ResearchBudget;
  onStart: (subQuestions: ResearchSubQuestion[]) => void;
  onCancel: () => void;
}) {
  const [items, setItems] = useState<ResearchSubQuestion[]>(initial);
  const valid = items.filter((q) => q.text.trim());
  const max = budget.max_subquestions;
  const tooMany = max > 0 && valid.length > max;

  const update = (i: number, text: string) => setItems((prev) => prev.map((q, idx) => (idx === i ? { ...q, text } : q)));
  const remove = (i: number) => setItems((prev) => prev.filter((_, idx) => idx !== i));
  const add = () => setItems((prev) => [...prev, { id: `user-${Date.now()}-${prev.length}`, text: "" }]);

  return (
    <div className="p-3.5 rounded-xl border text-xs space-y-3" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
      <div className="flex items-center gap-2 font-bold" style={{ color: "var(--text-main)" }}>
        <ListChecks className="w-4 h-4 text-purple-400" /> Research plan: review and edit
      </div>
      <ol className="space-y-2 list-none">
        {items.map((q, i) => (
          <li key={q.id} className="space-y-1">
            <div className="flex items-start gap-2">
              <span className="font-bold pt-1.5 w-5 shrink-0 text-right">{i + 1}.</span>
              <textarea
                value={q.text}
                onChange={(e) => update(i, e.target.value)}
                rows={2}
                aria-label={`Sub-question ${i + 1}`}
                className="flex-1 min-w-0 rounded-lg border px-2 py-1.5 text-xs bg-transparent resize-y"
                style={{ borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
              />
              <button type="button" onClick={() => remove(i)} aria-label={`Remove sub-question ${i + 1}`} className="p-1.5 rounded-lg hover:bg-rose-500/10 text-rose-500">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
            {q.rationale && <p className="pl-7 text-[10px] break-words" style={{ color: "var(--text-subtle)" }}>{q.rationale}</p>}
          </li>
        ))}
      </ol>
      <button type="button" onClick={add} className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border font-bold hover:bg-slate-500/10" style={{ borderColor: "var(--border-subtle)" }}>
        <Plus className="w-3 h-3" /> Add sub-question
      </button>
      <p className="text-[10px]" style={{ color: "var(--text-subtle)" }}>
        Budget: up to {budget.max_subquestions} sub-questions, {budget.max_tokens.toLocaleString()} tokens, {budget.max_seconds}s.
        {tooMany && <span className="text-amber-500"> You have more sub-questions than the budget allows.</span>}
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={valid.length === 0 || tooMany}
          onClick={() => onStart(valid.map((q) => ({ ...q, text: q.text.trim() })))}
          className="px-3 py-1.5 rounded-xl font-bold disabled:opacity-40"
          style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}
        >
          Start research
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-1.5 rounded-xl border font-bold" style={{ borderColor: "var(--border-subtle)" }}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export default ResearchPlanEditor;
