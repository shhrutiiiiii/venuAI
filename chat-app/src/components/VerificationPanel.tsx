"use client";

import React, { useState } from "react";
import { ChevronDown, ChevronUp, FileText, CheckCircle2, AlertOctagon, Shield } from "lucide-react";
import type { Citation } from "@/lib/answerTypes";

interface VerificationPanelProps {
  /** null/undefined => not measured. */
  groundingScore?: number | null;
  hallucinationScore?: number | null;
  verified?: boolean | null;
  reason?: string;
  verificationFlags?: string[];
  citations?: Citation[];
  model?: string;
  /** Real model id reported by the backend, when present. */
  modelUsed?: string;
}

const pct = (v?: number | null) => (typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "Not measured");

export const VerificationPanel: React.FC<VerificationPanelProps> = ({
  groundingScore,
  hallucinationScore,
  verified,
  reason,
  verificationFlags = [],
  citations = [],
  model = "VenuAI RAG Pipeline",
  modelUsed,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const grounded = typeof groundingScore === "number" && verified !== false;

  return (
    <div className="mt-3 border rounded-xl overflow-hidden text-xs" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)", color: "var(--text-muted)" }}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-3.5 py-2.5 flex items-center justify-between hover:bg-slate-500/5 transition-colors text-left font-medium"
      >
        <div className="flex items-center gap-2 min-w-0">
          <Shield className="w-4 h-4 text-purple-400 shrink-0" />
          <span className="truncate">Sources &amp; verification</span>
          <span className="text-[10px] px-2 py-0.5 rounded-md bg-purple-500/10 text-purple-300 border border-purple-500/20 font-mono shrink-0">
            {citations.length} {citations.length === 1 ? "source" : "sources"}
          </span>
        </div>
        {isOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </button>

      {isOpen && (
        <div className="p-3.5 space-y-3 border-t" style={{ borderColor: "var(--border-subtle)" }}>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-center">
            <div className="p-2 rounded-lg border" style={{ borderColor: "var(--border-subtle)" }}>
              <div className="text-[10px] font-medium">Grounding score</div>
              <div className={`text-base font-bold ${grounded ? "text-emerald-400" : ""}`}>
                {grounded ? pct(groundingScore) : "Unverified"}
              </div>
            </div>
            <div className="p-2 rounded-lg border" style={{ borderColor: "var(--border-subtle)" }}>
              <div className="text-[10px] font-medium">Hallucination risk</div>
              <div className="text-base font-bold">{pct(hallucinationScore)}</div>
            </div>
            <div className="col-span-2 sm:col-span-1 p-2 rounded-lg border" style={{ borderColor: "var(--border-subtle)" }}>
              <div className="text-[10px] font-medium">Pipeline</div>
              <div className="text-xs font-semibold text-purple-300 truncate" title={modelUsed || model}>{model}</div>
              {modelUsed && <div className="text-[10px] font-mono truncate" title={modelUsed}>{modelUsed}</div>}
            </div>
          </div>

          {!grounded && (
            <p className="flex items-start gap-1.5 text-[11px]">
              <AlertOctagon className="w-3.5 h-3.5 text-cyan-400 shrink-0 mt-0.5" />
              <span>Not verified{reason ? `: ${reason}` : ": the backend did not report a verification result for this answer."}</span>
            </p>
          )}

          {verificationFlags.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold mb-1.5 flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" /> Reported checks
              </div>
              <div className="flex flex-wrap gap-1.5">
                {verificationFlags.map((flag, idx) => (
                  <span key={idx} className="px-2 py-0.5 rounded border font-mono text-[10px] break-all" style={{ borderColor: "var(--border-subtle)" }}>
                    {flag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {citations.length > 0 ? (
            <div>
              <div className="text-[11px] font-semibold mb-1.5 flex items-center gap-1">
                <FileText className="w-3.5 h-3.5 text-purple-400" /> Sources
              </div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                {citations.map((c) => (
                  <div key={c.index} className="p-2 rounded border text-[11px]" style={{ borderColor: "var(--border-subtle)" }}>
                    <div className="flex justify-between items-center gap-2 font-medium">
                      <span className="truncate"><span className="font-extrabold text-purple-300">[{c.index}]</span> {c.filename}</span>
                      <span className="shrink-0 text-[10px]">{c.page != null ? `p. ${c.page}` : ""}</span>
                    </div>
                    {c.quote && <div className="text-[10px] mt-1 line-clamp-3 italic">&ldquo;{c.quote}&rdquo;</div>}
                    {c.verified !== undefined && (
                      <div className={`text-[10px] mt-1 font-bold ${c.verified ? "text-emerald-400" : "text-rose-400"}`}>
                        {c.verified ? "Quote verified" : "Unverified quote"}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-[11px] italic flex items-center gap-1.5">
              <AlertOctagon className="w-3.5 h-3.5 text-cyan-400" /> No sources were returned for this answer.
            </div>
          )}
        </div>
      )}
    </div>
  );
};
