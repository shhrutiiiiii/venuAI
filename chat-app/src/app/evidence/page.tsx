"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import ThemeToggle from "@/components/ThemeToggle";
import {
  FileSearch,
  ArrowLeft,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  HelpCircle,
  ChevronRight,
  Building2,
} from "lucide-react";
import Link from "next/link";

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
  supported: { color: "#10b981", icon: CheckCircle2, label: "Supported" },
  partially_supported: { color: "#f59e0b", icon: AlertTriangle, label: "Partially supported" },
  unsupported: { color: "#64748b", icon: HelpCircle, label: "Unsupported" },
  contradicted: { color: "#ef4444", icon: XCircle, label: "Contradicted" },
};

export default function EvidenceExplorerPage() {
  const { user, isLoading } = useAuth();
  const [records, setRecords] = useState<RecordSummary[]>([]);
  const [selected, setSelected] = useState<RecordDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/evidence");
      if (res.ok) setRecords(await res.json());
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
    try {
      const res = await fetch(`/api/evidence/${encodeURIComponent(recordId)}`);
      if (res.ok) setSelected(await res.json());
    } finally {
      setDetailLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-main)" }}>
        <RefreshCw className="w-6 h-6 animate-spin" style={{ color: "var(--primary-accent)" }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen p-6 md:p-10" style={{ background: "var(--bg-main)", color: "var(--text-main)" }}>
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6" style={{ borderColor: "var(--border-subtle)" }}>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
              <FileSearch className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold">Evidence Explorer</h1>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                {user?.role === "admin"
                  ? "Every verification record in your organization — which claims were supported, and by what."
                  : "Your own past answers — which claims were supported, and by what."}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={load} disabled={loading} className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            <ThemeToggle />
            <Link href="/workspace" className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
              <Building2 className="w-3.5 h-3.5" /> Workspace
            </Link>
            <Link href="/chat" className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
              <ArrowLeft className="w-4 h-4" /> Back to Chat
            </Link>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
          <div className="lg:col-span-2 rounded-2xl p-5 space-y-2" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <h3 className="text-sm font-bold mb-2">Past answers</h3>
            {records.length === 0 ? (
              <p className="text-xs italic py-6 text-center" style={{ color: "var(--text-subtle)" }}>
                Nothing yet — ask a question in Chat or run a Research query, then come back here.
              </p>
            ) : (
              <div className="space-y-1.5 max-h-[520px] overflow-y-auto">
                {records.map((r) => (
                  <button
                    key={r.record_id}
                    onClick={() => openRecord(r.record_id)}
                    className="w-full text-left p-3 rounded-xl transition-all hover:opacity-90"
                    style={{
                      background: selected?.record_id === r.record_id ? "var(--badge-bg)" : "var(--bg-input)",
                      border: "1px solid var(--border-subtle)",
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold truncate">{r.question}</span>
                      <ChevronRight className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--text-subtle)" }} />
                    </div>
                    <div className="flex items-center gap-2 mt-1.5 text-[10px]" style={{ color: "var(--text-subtle)" }}>
                      <span className="px-1.5 py-0.5 rounded-full font-bold uppercase" style={{ background: "var(--bg-card)" }}>{r.source}</span>
                      <span>{Math.round(r.evidence_coverage * 100)}% coverage</span>
                      {r.contradicted_claim_count > 0 && <span className="text-red-500 font-semibold">{r.contradicted_claim_count} contradicted</span>}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="lg:col-span-3 rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            {detailLoading ? (
              <div className="flex items-center justify-center h-40"><Loader /></div>
            ) : !selected ? (
              <p className="text-xs italic py-10 text-center" style={{ color: "var(--text-subtle)" }}>
                Select an answer on the left to see its claim-by-claim evidence breakdown.
              </p>
            ) : (
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-bold">{selected.question}</h3>
                  <p className="text-xs mt-1.5 leading-relaxed" style={{ color: "var(--text-muted)" }}>{selected.answer}</p>
                </div>

                <div className="flex items-center gap-3 text-xs">
                  <span className="font-semibold" style={{ color: "var(--text-main)" }}>
                    {Math.round(selected.evidence_coverage * 100)}% evidence coverage
                  </span>
                  <span style={{ color: selected.critique_passed ? "#10b981" : "#ef4444" }}>
                    {selected.critique_passed ? "Critique passed" : "Critique flagged issues"}
                  </span>
                </div>

                <div className="space-y-2.5">
                  {selected.claims.length === 0 ? (
                    <p className="text-xs italic" style={{ color: "var(--text-subtle)" }}>No claims were extracted/verified for this answer.</p>
                  ) : (
                    selected.claims.map((claim) => {
                      const meta = STATUS_META[claim.support_status] || STATUS_META.unsupported;
                      const Icon = meta.icon;
                      return (
                        <div key={claim.claim_id} className="p-3 rounded-xl" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                          <div className="flex items-start gap-2">
                            <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: meta.color }} />
                            <div className="flex-1">
                              <p className="text-xs font-medium">{claim.text}</p>
                              <span className="text-[10px] font-bold uppercase" style={{ color: meta.color }}>{meta.label}</span>
                            </div>
                          </div>
                          {claim.evidence.length > 0 && (
                            <div className="mt-2 pl-6 space-y-1.5">
                              {claim.evidence.map((e) => (
                                <div key={e.evidence_id} className="text-[11px] p-2 rounded-lg" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                                  <span className="font-semibold">{e.document_name}</span>
                                  <span style={{ color: "var(--text-subtle)" }}> · p.{e.page_number} · {e.modality}</span>
                                  <p className="mt-0.5" style={{ color: "var(--text-muted)" }}>{e.content.slice(0, 180)}{e.content.length > 180 ? "…" : ""}</p>
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
          </div>
        </div>
      </div>
    </div>
  );
}

function Loader() {
  return <RefreshCw className="w-5 h-5 animate-spin" style={{ color: "var(--primary-accent)" }} />;
}
