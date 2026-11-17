"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { FileSearch, CheckCircle2, XCircle, AlertTriangle, HelpCircle, ChevronRight } from "lucide-react";
import PageShell from "@/components/pages/PageShell";
import { EmptyState, ErrorState, Meter, PageSpinner, SkeletonRows } from "@/components/pages/primitives";

interface RecordSummary {
  record_id: string;
  user_id: string;
  question: string;
  source: string;
  evidence_coverage: number;
  unsupported_claim_count: number;
  contradicted_claim_count: number;
  critique_passed: boolean;
  created_at: number;
}

interface EvidenceItem {
  evidence_id: string;
  document_name: string;
  page_number: number;
  content: string;
  modality: string;
  relevance_score: number;
}

interface Claim {
  claim_id: string;
  text: string;
  support_status: string;
  support_score: number;
  evidence: EvidenceItem[];
}

interface RecordDetail extends RecordSummary {
  answer: string;
  claims: Claim[];
}

const STATUS_META: Record<string, { color: string; icon: React.ElementType; label: string }> = {
  supported: { color: "var(--p-ok)", icon: CheckCircle2, label: "Supported" },
  partially_supported: { color: "var(--p-warn)", icon: AlertTriangle, label: "Partially supported" },
  unsupported: { color: "var(--text-subtle)", icon: HelpCircle, label: "Unsupported" },
  contradicted: { color: "var(--p-bad)", icon: XCircle, label: "Contradicted" },
};

export default function EvidenceExplorerPage() {
  const { user, isLoading } = useAuth();
  const [records, setRecords] = useState<RecordSummary[]>([]);
  const [selected, setSelected] = useState<RecordDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/evidence");
      if (res.ok) setRecords(await res.json());
      else setError("The evidence service returned an error. Try again in a moment.");
    } catch {
      setError("The evidence service is unreachable. Check that the backend is running.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const openRecord = async (recordId: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const res = await fetch(`/api/evidence/${encodeURIComponent(recordId)}`);
      if (res.ok) setSelected(await res.json());
      else setDetailError("Could not load that record.");
    } catch {
      setDetailError("Could not load that record. The service is unreachable.");
    } finally {
      setDetailLoading(false);
    }
  };

  if (isLoading) return <PageSpinner />;
  const isAdmin = user?.role === "admin";

  return (
    <PageShell
      current="evidence"
      title="Evidence explorer"
      icon={FileSearch}
      isAdmin={isAdmin}
      onRefresh={load}
      refreshing={loading}
      subtitle={isAdmin
        ? "Every verification record in your organization: which claims were supported, and by what."
        : "Your past answers: which claims were supported, and by what."}
    >
      {error && <ErrorState message={error} onRetry={load} />}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        <section className="lg:col-span-2 p-surface p-4 sm:p-5 space-y-2 min-w-0" aria-label="Past answers">
          <h2 className="text-sm font-bold mb-2">Past answers</h2>
          {loading && records.length === 0 ? (
            <SkeletonRows count={5} />
          ) : records.length === 0 ? (
            <EmptyState title="No answers yet" text="Ask a question in chat or run a research query, then come back to see how each claim was backed up." />
          ) : (
            <ul className="space-y-1.5 max-h-[520px] overflow-y-auto pr-0.5">
              {records.map((r, i) => {
                const active = selected?.record_id === r.record_id;
                return (
                  <li key={r.record_id} className="p-rise" style={{ animationDelay: `${Math.min(i, 10) * 35}ms` }}>
                    <button
                      type="button"
                      onClick={() => openRecord(r.record_id)}
                      aria-pressed={active}
                      className="p-row w-full text-left p-3 rounded-xl"
                      style={{ background: active ? "var(--bg-card-hover)" : "var(--bg-input)", border: `1px solid ${active ? "var(--border-hover)" : "var(--border-subtle)"}` }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold truncate">{r.question}</span>
                        <ChevronRight className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--text-subtle)" }} />
                      </div>
                      <div className="flex items-center flex-wrap gap-x-2 gap-y-1 mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
                        <span className="px-1.5 py-0.5 rounded-full font-semibold" style={{ background: "var(--bg-card)" }}>{r.source}</span>
                        <span>{Math.round(r.evidence_coverage * 100)}% coverage</span>
                        {r.contradicted_claim_count > 0 && <span className="font-semibold" style={{ color: "var(--p-bad)" }}>{r.contradicted_claim_count} contradicted</span>}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="lg:col-span-3 p-surface p-4 sm:p-5 min-w-0" aria-live="polite" aria-label="Answer detail">
          {detailLoading ? (
            <SkeletonRows count={3} />
          ) : detailError ? (
            <ErrorState message={detailError} />
          ) : !selected ? (
            <EmptyState text="Select an answer on the left to see its claim-by-claim evidence." />
          ) : (
            <div key={selected.record_id} className="space-y-4 p-fade-in">
              <div>
                <h2 className="text-sm font-bold break-words">{selected.question}</h2>
                <p className="text-xs mt-1.5 leading-relaxed break-words" style={{ color: "var(--text-muted)" }}>{selected.answer}</p>
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="font-semibold">{Math.round(selected.evidence_coverage * 100)}% evidence coverage</span>
                  <span className="font-medium" style={{ color: selected.critique_passed ? "var(--p-ok)" : "var(--p-bad)" }}>
                    {selected.critique_passed ? "Critique passed" : "Critique flagged issues"}
                  </span>
                </div>
                <Meter label="Evidence coverage" pct={selected.evidence_coverage * 100} />
              </div>

              <div className="space-y-2.5">
                {selected.claims.length === 0 ? (
                  <EmptyState text="No claims were extracted or verified for this answer." />
                ) : (
                  selected.claims.map((claim, i) => {
                    const meta = STATUS_META[claim.support_status] || STATUS_META.unsupported;
                    const Icon = meta.icon;
                    return (
                      <div key={claim.claim_id} className="p-inset p-3 p-rise" style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}>
                        <div className="flex items-start gap-2">
                          <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: meta.color }} />
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium break-words">{claim.text}</p>
                            <span className="text-[11px] font-semibold" style={{ color: meta.color }}>{meta.label}</span>
                          </div>
                        </div>
                        {claim.evidence.length > 0 && (
                          <div className="mt-2 pl-6 space-y-1.5">
                            {claim.evidence.map((e) => (
                              <div key={e.evidence_id} className="text-[11px] p-2 rounded-lg" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                                <span className="font-semibold break-words">{e.document_name}</span>
                                <span style={{ color: "var(--text-subtle)" }}> page {e.page_number}, {e.modality}</span>
                                <p className="mt-0.5 break-words" style={{ color: "var(--text-muted)" }}>{e.content.slice(0, 180)}{e.content.length > 180 ? "…" : ""}</p>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </PageShell>
  );
}
