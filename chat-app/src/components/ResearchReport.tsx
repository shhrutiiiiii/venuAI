"use client";

import React, { useEffect, useState } from "react";
import { Microscope, ListChecks, ShieldCheck, ShieldQuestion, AlertTriangle, Loader2 } from "lucide-react";
import FormattedMarkdown from "./FormattedMarkdown";
import { AnswerActions } from "./AnswerActions";
import { answerToMarkdown } from "@/lib/answerTypes";

export interface SubquestionResult {
  subquestion: string;
  answer: string;
  evidence_coverage: number;
}

/** Shape of backend POST /research (ResearchResponse). */
export interface ResearchData {
  research_id: string;
  question: string;
  plan_is_fallback: boolean;
  subquestion_results: SubquestionResult[];
  synthesized_answer: string;
  evidence_coverage: number;
  critique_passed: boolean;
  truncated: boolean;
  graph_entities_extracted?: number;
  graph_relationships_extracted?: number;
}

const pct = (v: number) => `${Math.round(Math.min(Math.max(v, 0), 1) * 100)}%`;

/** The backend call is synchronous (no per-step events), so only elapsed time is shown, not fake step progress. */
export function ResearchPending({ question }: { question: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="p-3.5 rounded-xl border text-xs space-y-1.5" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
      <div className="flex items-center gap-2 font-bold" style={{ color: "var(--text-main)" }}>
        <Loader2 className="w-4 h-4 animate-spin" /> Deep research in progress ({secs}s)
      </div>
      <p className="break-words">Planning sub-questions, retrieving evidence for each, synthesizing and verifying: &ldquo;{question}&rdquo;</p>
      <p className="text-[10px]">
        This runs as one request and can take up to about 2 minutes. Per-step progress is not available yet.
        {secs > 90 && " Still working — close to the time limit."}
      </p>
    </div>
  );
}

export function researchToMarkdown(d: ResearchData): string {
  const subs = d.subquestion_results
    .map((s, i) => `### ${i + 1}. ${s.subquestion}\n\n${s.answer}\n\n_Evidence coverage: ${pct(s.evidence_coverage)}_`)
    .join("\n\n");
  const body = `## Report\n\n${d.synthesized_answer}\n\n## Research plan and findings\n\n${subs}`;
  return answerToMarkdown({
    question: d.question,
    answer: body,
    footer: [
      `Evidence coverage: ${pct(d.evidence_coverage)}`,
      `Critique: ${d.critique_passed ? "passed" : "did not pass"}`,
      d.plan_is_fallback ? "Plan: fallback (single question), not an LLM-generated plan" : "Plan: LLM-generated",
      d.truncated ? "Note: research hit the time limit and may be incomplete" : "",
    ].filter(Boolean),
  });
}

export function ResearchReport({ data }: { data: ResearchData }) {
  const md = researchToMarkdown(data);
  return (
    <div className="print-scope space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border" style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--badge-text)" }}>
          <Microscope className="w-3 h-3" /> Deep Research
        </span>
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border ${
            data.critique_passed ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/10" : "text-cyan-400 border-cyan-500/30 bg-cyan-500/10"
          }`}
        >
          {data.critique_passed ? <ShieldCheck className="w-3 h-3" /> : <ShieldQuestion className="w-3 h-3" />}
          {data.critique_passed ? "Critique passed" : "Critique not passed"}
        </span>
        <span className="text-[10px] font-bold" style={{ color: "var(--text-muted)" }}>Evidence coverage {pct(data.evidence_coverage)}</span>
      </div>

      {data.truncated && (
        <p className="flex items-start gap-1.5 text-[11px] text-amber-500">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> Research hit the time limit; the report may be incomplete.
        </p>
      )}
      {data.plan_is_fallback && (
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          The planner fell back to a single question, so this is not a multi-step decomposition.
        </p>
      )}

      <div className="rounded-xl border p-3.5" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}>
        <FormattedMarkdown content={data.synthesized_answer} />
      </div>

      <details className="rounded-xl border text-xs" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
        <summary className="px-3.5 py-2.5 cursor-pointer font-bold flex items-center gap-2 select-none">
          <ListChecks className="w-4 h-4 text-purple-400 inline" /> Plan &amp; sub-questions ({data.subquestion_results.length})
        </summary>
        <ol className="p-3.5 pt-1 space-y-3 list-none">
          {data.subquestion_results.map((s, i) => (
            <li key={i} className="space-y-1">
              <div className="font-bold break-words" style={{ color: "var(--text-main)" }}>
                {i + 1}. {s.subquestion}{" "}
                <span className="text-[10px] font-medium" style={{ color: "var(--text-subtle)" }}>coverage {pct(s.evidence_coverage)}</span>
              </div>
              <FormattedMarkdown content={s.answer} />
            </li>
          ))}
        </ol>
      </details>

      <p className="text-[10px]" style={{ color: "var(--text-subtle)" }}>
        Sources are cited inside the report text; the research endpoint does not yet return a structured source list.
      </p>

      <AnswerActions markdown={md} title={`research-${data.question}`} />
    </div>
  );
}
