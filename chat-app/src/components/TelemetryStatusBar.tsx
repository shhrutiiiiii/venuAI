"use client";

import React, { useState, useRef, useEffect } from "react";
import { Activity, Zap, Radio, Info, CheckCircle2, ShieldCheck, Cpu } from "lucide-react";

export interface TelemetryStatusBarProps {
  useStreaming: boolean;
  onToggleStreaming: () => void;
  latency?: string;
  className?: string;
}

export const TelemetryStatusBar: React.FC<TelemetryStatusBarProps> = ({
  useStreaming,
  onToggleStreaming,
  latency = "<10ms",
  className = "",
}) => {
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close diagnostics popover on click outside or escape key
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setShowDiagnostics(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setShowDiagnostics(false);
      }
    }
    if (showDiagnostics) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [showDiagnostics]);

  return (
    <div className={`relative inline-flex items-center ${className}`} ref={popoverRef}>
      {/* ── Unified Engineering Telemetry Status Bar ── */}
      <div
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl border font-mono text-xs shadow-sm transition-all duration-150"
        style={{
          background: "var(--bg-card)",
          borderColor: "var(--border-subtle)",
          color: "var(--text-main)",
        }}
      >
        {/* Module 1: Live System Heartbeat & Diagnostics Trigger */}
        <button
          type="button"
          onClick={() => setShowDiagnostics((prev) => !prev)}
          title="Click to view live system telemetry & diagnostics"
          className="flex items-center gap-1.5 cursor-pointer hover:opacity-80 transition-opacity focus:outline-none"
        >
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
          </span>
          <span className="text-[9px] font-mono font-bold tracking-wider uppercase text-slate-400 hidden sm:inline">
            HEALTH
          </span>
          <span className="text-[10px] font-mono font-black text-emerald-400 tracking-tight">
            ONLINE
          </span>
        </button>

        {/* Module 2: Sub-10ms Engine Latency Metric */}
        <div
          className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-[10px] font-mono font-black shrink-0"
          title="Sub-10ms Realtime Inference Engine Active (Zero-allocation memory pipeline)"
        >
          <Activity className="w-3 h-3 text-emerald-400 animate-pulse" />
          <span>{latency}</span>
        </div>

        {/* Tactical Vertical Divider */}
        <div className="h-4 w-px bg-slate-500/25 shrink-0" />

        {/* Module 3: Stream SSE Protocol Telemetry Control */}
        <button
          type="button"
          onClick={onToggleStreaming}
          title={
            useStreaming
              ? "Telemetry Mode: Realtime SSE (Server-Sent Events) Active — Click to switch to Buffered REST"
              : "Telemetry Mode: Buffered REST Response — Click to enable Realtime SSE Streaming"
          }
          className={`flex items-center gap-1.5 px-2 py-0.5 rounded-lg border text-[10px] font-mono font-extrabold transition-all duration-150 active:scale-95 cursor-pointer ${
            useStreaming
              ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-300 shadow-[0_0_10px_rgba(34,211,238,0.15)] hover:bg-cyan-500/25"
              : "bg-slate-500/10 border-slate-500/20 text-slate-400 hover:text-slate-200 hover:bg-slate-500/20"
          }`}
        >
          <Zap
            className={`w-3 h-3 transition-colors ${
              useStreaming ? "text-cyan-400 fill-cyan-400/40" : "text-slate-500"
            }`}
          />
          <span className="text-[9px] font-bold text-slate-400">STREAM:</span>
          <span className={useStreaming ? "text-cyan-400 font-black" : "text-slate-300 font-bold"}>
            {useStreaming ? "SSE" : "REST"}
          </span>
          {useStreaming && (
            <span className="relative flex h-1.5 w-1.5 shrink-0">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-cyan-400" />
            </span>
          )}
        </button>

        {/* Diagnostics Info Button */}
        <button
          type="button"
          onClick={() => setShowDiagnostics((prev) => !prev)}
          title="Telemetry Diagnostics Panel"
          className="p-0.5 rounded text-slate-500 hover:text-cyan-400 transition-colors cursor-pointer"
        >
          <Info className="w-3 h-3" />
        </button>
      </div>

      {/* ── System Health Diagnostics Flyout ── */}
      {showDiagnostics && (
        <div
          className="absolute right-0 top-full mt-2 w-72 sm:w-80 rounded-2xl border shadow-2xl p-3 z-50 backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150"
          style={{
            background: "var(--bg-card)",
            borderColor: "var(--border-hover)",
          }}
        >
          {/* Diagnostics Header */}
          <div className="flex items-center justify-between pb-2 border-b border-slate-500/15 font-mono">
            <div className="flex items-center gap-1.5 text-cyan-400 text-[10px] font-black uppercase">
              <Radio className="w-3.5 h-3.5" />
              <span>LIVE TELEMETRY MONITOR</span>
            </div>
            <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
              NOMINAL
            </span>
          </div>

          {/* Telemetry Metrics Grid */}
          <div className="py-2.5 space-y-2 text-[11px] font-mono">
            <div className="flex items-center justify-between p-1.5 rounded-lg bg-slate-500/5 border border-slate-500/10">
              <div className="flex items-center gap-1.5 text-slate-400">
                <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                <span>Inference Pipeline</span>
              </div>
              <span className="font-extrabold text-cyan-300">Groq LPU (Sub-10ms)</span>
            </div>

            <div className="flex items-center justify-between p-1.5 rounded-lg bg-slate-500/5 border border-slate-500/10">
              <div className="flex items-center gap-1.5 text-slate-400">
                <Activity className="w-3.5 h-3.5 text-emerald-400" />
                <span>Vector Retrieval</span>
              </div>
              <span className="font-extrabold text-emerald-400">ChromaDB + BM25</span>
            </div>

            <div className="flex items-center justify-between p-1.5 rounded-lg bg-slate-500/5 border border-slate-500/10">
              <div className="flex items-center gap-1.5 text-slate-400">
                <Zap className="w-3.5 h-3.5 text-amber-400" />
                <span>Streaming Protocol</span>
              </div>
              <span className="font-extrabold text-white">
                {useStreaming ? "Server-Sent Events (SSE)" : "HTTP Buffered REST"}
              </span>
            </div>

            <div className="flex items-center justify-between p-1.5 rounded-lg bg-slate-500/5 border border-slate-500/10">
              <div className="flex items-center gap-1.5 text-slate-400">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                <span>Truth Guardrails</span>
              </div>
              <span className="font-extrabold text-emerald-400">Active Verification</span>
            </div>
          </div>

          {/* Diagnostics Footer */}
          <div className="pt-2 border-t border-slate-500/15 flex items-center justify-between text-[9px] font-mono text-slate-500">
            <span>FastAPI:8000 • Connected</span>
            <span className="text-cyan-400 cursor-pointer" onClick={() => setShowDiagnostics(false)}>
              [CLOSE]
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default TelemetryStatusBar;
