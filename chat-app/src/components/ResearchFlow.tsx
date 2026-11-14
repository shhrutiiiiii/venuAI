"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Microscope, ShieldCheck, ShieldQuestion } from "lucide-react";
import FormattedMarkdown from "./FormattedMarkdown";
import { AnswerActions } from "./AnswerActions";
import { ExportMenu } from "./ExportMenu";
import { ResearchPlanEditor } from "./ResearchPlanEditor";
import { ResearchProgress } from "./ResearchProgress";
import { normalizeCitations, type Citation } from "@/lib/answerTypes";
import {
  TERMINAL_JOB_STATUSES,
  type ResearchDepth,
  type ResearchJob,
  type ResearchPlanResponse,
  type ResearchReportV2,
  type ResearchSubQuestion,
} from "@/lib/apiContracts";
import { cancelResearchJob, getResearchJob, planResearch, resumeResearchJob, startResearchJob } from "@/lib/researchClient";
import { applyStep, readSse, type TimelineStep } from "@/lib/sse";

const POLL_MS = 2500;

type Stage =
  | { kind: "planning" }
  | { kind: "editing"; plan: ResearchPlanResponse }
  | { kind: "running"; jobId: string }
  | { kind: "cancelled" }
  | { kind: "error"; message: string };

/** Report canvas: markdown with numbered citations, honest verification line, export menu. */
export function ResearchJobReport({ question, job }: { question: string; job: ResearchJob }) {
  const report = job.report as ResearchReportV2 | undefined;
  if (!report) return null;
  const citations: Citation[] = normalizeCitations(report.citations as Citation[], null);
  const partial = job.status === "budget_exceeded";
  const verified = report.verified;
  const grounding = typeof report.grounding_score === "number" ? report.grounding_score : null;
  return (
    <div className="print-scope space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border" style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--badge-text)" }}>
          <Microscope className="w-3 h-3" /> Deep Research
        </span>
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border ${
            verified === true ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/10" : "text-cyan-400 border-cyan-500/30 bg-cyan-500/10"
          }`}
        >
          {verified === true ? <ShieldCheck className="w-3 h-3" /> : <ShieldQuestion className="w-3 h-3" />}
          {verified === true ? "Verified against sources" : verified === false ? "Not verified" : "Verification not available"}
        </span>
        {grounding !== null && <span className="text-[10px] font-bold" style={{ color: "var(--text-muted)" }}>Grounding {Math.round(grounding * 100)}%</span>}
        {report.answer_basis && <span className="text-[10px]" style={{ color: "var(--text-subtle)" }}>basis: {report.answer_basis}</span>}
      </div>
      {partial && (
        <p className="flex items-start gap-1.5 text-[11px] text-amber-500">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> The research budget was exhausted; this report is partial.
        </p>
      )}
      <div className="rounded-xl border p-3.5" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}>
        <FormattedMarkdown content={report.markdown} citations={citations} />
      </div>
      {citations.length > 0 && (
        <details className="rounded-xl border text-xs" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
          <summary className="px-3.5 py-2 cursor-pointer font-bold select-none">Sources ({citations.length})</summary>
          <ol className="p-3.5 pt-1 space-y-2 list-none">
            {citations.map((c) => (
              <li key={c.index} className="break-words">
                <span className="font-bold" style={{ color: "var(--text-main)" }}>
                  [{c.index}] {c.filename}
                  {c.page != null ? `, p. ${c.page}` : ""}
                </span>
                {c.verified === true && <span className="ml-1.5 text-[10px] text-emerald-500">quote verified</span>}
                {c.verified === false && <span className="ml-1.5 text-[10px] text-rose-500">unverified</span>}
                {c.quote && <div className="italic">&ldquo;{c.quote}&rdquo;</div>}
              </li>
            ))}
          </ol>
        </details>
      )}
      <ExportMenu title={`research-${question}`} markdown={report.markdown} citations={report.citations} />
      <AnswerActions markdown={report.markdown} title={`research-${question}`} />
    </div>
  );
}

/**
 * Deep Research v2 flow: plan -> edit -> start job -> live progress (SSE with polling
 * fallback) -> report. If the plan endpoint is missing/disabled, `onFallback` runs the
 * legacy synchronous /research instead.
 */
export function ResearchFlow({
  question,
  depth,
  docIds,
  onFallback,
}: {
  question: string;
  depth: ResearchDepth;
  docIds?: string[];
  onFallback: () => void;
}) {
  const [stage, setStage] = useState<Stage>({ kind: "planning" });
  const [job, setJob] = useState<ResearchJob | null>(null);
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [transport, setTransport] = useState<"live" | "polling">("polling");
  const [pollKey, setPollKey] = useState(0);
  const planStarted = useRef(false);
  const planIdRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (planStarted.current) return;
    planStarted.current = true;
    planResearch(question, depth, docIds).then((r) => {
      if (r.ok) {
        planIdRef.current = r.data.plan_id;
        setStage({ kind: "editing", plan: r.data });
      } else if (r.unavailable) onFallback();
      else setStage({ kind: "error", message: r.message });
    });
  }, [question, depth, docIds, onFallback]);

  const jobId = stage.kind === "running" ? stage.jobId : null;

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const ctl = new AbortController();

    // Live step/progress events (best effort). Polling below stays authoritative.
    (async () => {
      try {
        const res = await fetch(`/api/research/jobs/${encodeURIComponent(jobId)}/events`, { signal: ctl.signal, cache: "no-store" });
        if (!res.ok || !res.body) return;
        setTransport("live");
        await readSse(
          res.body,
          (e) => {
            if (stopped) return;
            if (e.kind === "step") setSteps((prev) => applyStep(prev, e.step));
            else if (e.kind === "progress") setJob((prev) => (prev ? { ...prev, progress: e.progress } : prev));
          },
          ctl.signal
        );
      } catch {
        /* aborted or unsupported: polling continues */
      } finally {
        if (!stopped) setTransport("polling");
      }
    })();

    (async () => {
      let failures = 0;
      while (!stopped) {
        const r = await getResearchJob(jobId);
        if (stopped) return;
        if (r.ok) {
          failures = 0;
          setJob((prev) => ({ ...r.data, progress: r.data.progress ?? prev?.progress }));
          if (TERMINAL_JOB_STATUSES.includes(r.data.status) || r.data.status === "interrupted") return;
        } else if (++failures >= 5 || r.status === 404) {
          setStage({ kind: "error", message: r.status === 404 ? "The research job could not be found." : r.message });
          return;
        }
        await new Promise((res) => setTimeout(res, POLL_MS));
      }
    })();

    return () => {
      stopped = true;
      ctl.abort();
    };
  }, [jobId, pollKey]);

  const start = useCallback(
    async (subs: ResearchSubQuestion[]) => {
      setStage({ kind: "planning" });
      const r = await startResearchJob({ question, depth, plan_id: planIdRef.current, sub_questions: subs, doc_ids: docIds });
      if (r.ok) {
        setJob(null);
        setSteps([]);
        setStage({ kind: "running", jobId: r.data.job_id });
      } else setStage({ kind: "error", message: r.message });
    },
    [question, depth, docIds]
  );

  const cancel = async () => {
    if (jobId) await cancelResearchJob(jobId);
    setStage({ kind: "cancelled" });
  };

  const resume = async () => {
    if (!jobId) return;
    const r = await resumeResearchJob(jobId);
    if (r.ok) setPollKey((k) => k + 1);
    else setStage({ kind: "error", message: r.message });
  };

  if (stage.kind === "planning") {
    return (
      <div className="flex items-center gap-2 text-xs font-bold p-3.5 rounded-xl border" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-main)" }}>
        <Loader2 className="w-4 h-4 animate-spin" /> Planning sub-questions...
      </div>
    );
  }
  if (stage.kind === "editing") {
    return <ResearchPlanEditor initial={stage.plan.sub_questions} budget={stage.plan.budget} onStart={start} onCancel={() => setStage({ kind: "cancelled" })} />;
  }
  if (stage.kind === "cancelled") return <p className="text-xs" style={{ color: "var(--text-muted)" }}>Research cancelled.</p>;
  if (stage.kind === "error") return <p className="text-sm font-medium text-red-500 break-words">{stage.message}</p>;

  // running
  if (job?.status === "done" || job?.status === "budget_exceeded") {
    return job.report ? <ResearchJobReport question={question} job={job} /> : <p className="text-xs" style={{ color: "var(--text-muted)" }}>The job finished without a report.</p>;
  }
  if (job?.status === "failed") return <p className="text-sm font-medium text-red-500 break-words">Research failed{job.error ? `: ${job.error}` : "."}</p>;
  if (job?.status === "cancelled") return <p className="text-xs" style={{ color: "var(--text-muted)" }}>Research was cancelled.</p>;
  if (job?.status === "interrupted") {
    return (
      <div className="p-3.5 rounded-xl border text-xs space-y-2" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
        <p>The research job was interrupted (the server restarted). Completed sub-questions are kept.</p>
        <button type="button" onClick={resume} className="px-3 py-1.5 rounded-xl font-bold" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
          Resume
        </button>
      </div>
    );
  }
  return <ResearchProgress job={job} steps={steps} transport={transport} onCancel={cancel} />;
}

export default ResearchFlow;
