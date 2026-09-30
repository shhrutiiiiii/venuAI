"use client";

import React, { useState } from "react";
import { ChevronDown, ChevronUp, FileText, CheckCircle2, AlertOctagon, Shield } from "lucide-react";

export interface SourceCitation {
  document: string;
  page: number;
  chunk_preview?: string;
}

interface VerificationPanelProps {
  groundingScore: number;
  hallucinationScore: number;
  verificationFlags?: string[];
  sources?: SourceCitation[];
  model?: string;
}

export const VerificationPanel: React.FC<VerificationPanelProps> = ({
  groundingScore,
  hallucinationScore,
  verificationFlags = [],
  sources = [],
  model = "VenuAI RAG Pipeline",
}) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="mt-3 border border-slate-700/60 dark:border-slate-800 rounded-xl bg-slate-900/60 overflow-hidden text-xs text-slate-300">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-3.5 py-2.5 flex items-center justify-between bg-slate-800/40 hover:bg-slate-800/70 transition-colors text-left font-medium"
      >
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-purple-400" />
          <span>Verification & Grounding Audit Panel</span>
          <span className="text-[10px] px-2 py-0.5 rounded-md bg-purple-500/10 text-purple-300 border border-purple-500/20 font-mono">
            {sources.length} sources cited
          </span>
        </div>
        {isOpen ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="p-3.5 space-y-3 bg-slate-950/40 border-t border-slate-800/60">
          {/* Metrics Overview Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-center">
            <div className="p-2 rounded-lg bg-slate-900/80 border border-slate-800">
              <div className="text-[10px] text-slate-400 font-medium">Grounding Score</div>
              <div className="text-base font-bold text-emerald-400">{(groundingScore * 100).toFixed(0)}%</div>
            </div>
            <div className="p-2 rounded-lg bg-slate-900/80 border border-slate-800">
              <div className="text-[10px] text-slate-400 font-medium">Hallucination Risk</div>
              <div className={`text-base font-bold ${hallucinationScore < 0.3 ? "text-emerald-400" : "text-cyan-400"}`}>
                {(hallucinationScore * 100).toFixed(0)}%
              </div>
            </div>
            <div className="col-span-2 sm:col-span-1 p-2 rounded-lg bg-slate-900/80 border border-slate-800">
              <div className="text-[10px] text-slate-400 font-medium">Model Engine</div>
              <div className="text-xs font-semibold text-purple-300 truncate" title={model}>
                {model}
              </div>
            </div>
          </div>

          {/* Verification Policy Flags */}
          {verificationFlags.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold text-slate-400 mb-1.5 flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" /> Policy Checks
              </div>
              <div className="flex flex-wrap gap-1.5">
                {verificationFlags.map((flag, idx) => (
                  <span
                    key={idx}
                    className="px-2 py-0.5 rounded bg-slate-800/90 text-slate-300 border border-slate-700/80 font-mono text-[10px]"
                  >
                    {flag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Cited Document Sources */}
          {sources.length > 0 ? (
            <div>
              <div className="text-[11px] font-semibold text-slate-400 mb-1.5 flex items-center gap-1">
                <FileText className="w-3.5 h-3.5 text-purple-400" /> Grounded Source Citations
              </div>
              <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                {sources.map((src, i) => (
                  <div key={i} className="p-2 rounded bg-slate-900/90 border border-slate-800/80 text-[11px]">
                    <div className="flex justify-between items-center text-purple-300 font-medium">
                      <span className="truncate">{src.document}</span>
                      <span className="shrink-0 text-[10px] text-slate-400">Page {src.page}</span>
                    </div>
                    {src.chunk_preview && (
                      <div className="text-slate-400 text-[10px] mt-1 line-clamp-2 italic">
                        "{src.chunk_preview}"
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-[11px] text-slate-400 italic flex items-center gap-1.5">
              <AlertOctagon className="w-3.5 h-3.5 text-cyan-400" /> Answers generated from preloaded DRDO Master Knowledge Base.
            </div>
          )}
        </div>
      )}
    </div>
  );
};
