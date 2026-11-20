"use client";

import React, { useState, useRef, useEffect } from "react";
import { Cpu, Wrench, Layers, ChevronDown, Check, Terminal, Shield } from "lucide-react";

export type AgentMode = "rag" | "react" | "multi_agent";

export interface AgentModeToggleProps {
  mode: AgentMode;
  onChange: (mode: AgentMode) => void;
  disabled?: boolean;
}

interface ModeConfig {
  id: AgentMode;
  label: string;
  badge: string;
  code: string;
  icon: React.ComponentType<{ className?: string }>;
  tagline: string;
  description: string;
  techSpec: string;
}

const MODES: ModeConfig[] = [
  {
    id: "rag",
    label: "Direct RAG",
    badge: "DIRECT",
    code: "DIRECT-VEC // LPU",
    icon: Cpu,
    tagline: "Hybrid Vector Search + Groq LLM",
    description: "ChromaDB + BM25 vector hybrid retrieval with verified source citation extraction.",
    techSpec: "Sub-10ms Vector Engine • Verified Truth Guard",
  },
  {
    id: "react",
    label: "ReAct Agent",
    badge: "REACT",
    code: "REASON-ACT // 6 TOOLS",
    icon: Wrench,
    tagline: "Autonomous Step-by-Step Reasoner",
    description: "ReAct reasoning loop with calculator, memory recall, web search & document summarization.",
    techSpec: "Dynamic Tool Execution • Multi-Turn Reasoner",
  },
  {
    id: "multi_agent",
    label: "Multi-Swarm",
    badge: "SWARM",
    code: "PARALLEL-SWARM // LABS",
    icon: Layers,
    tagline: "Parallel Multi-Agent Swarm",
    description: "Orchestrator routing to specialized Lab, Technical & Research sub-agents in parallel.",
    techSpec: "Distributed Swarm • Domain Specialists",
  },
];

export const AgentModeToggle: React.FC<AgentModeToggleProps> = ({
  mode,
  onChange,
  disabled = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const currentMode = MODES.find((m) => m.id === mode) || MODES[0];
  const CurrentIcon = currentMode.icon;

  // Handle outside clicks to close dropdown
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div className="relative inline-block text-left" ref={dropdownRef}>
      {/* ── Engineering Control Trigger Button ── */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setIsOpen((prev) => !prev)}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        title="Open Command Control System Mode Selector"
        className={`group flex items-center gap-2.5 px-3 py-1.5 rounded-xl border text-xs font-mono transition-all duration-150 select-none ${
          disabled
            ? "opacity-50 cursor-not-allowed"
            : "hover:border-cyan-500/50 active:scale-[0.98] cursor-pointer"
        } ${isOpen ? "ring-1 ring-cyan-500/50 border-cyan-500/60 shadow-lg shadow-cyan-500/10" : ""}`}
        style={{
          background: "var(--bg-card)",
          borderColor: isOpen ? "var(--border-focus)" : "var(--border-subtle)",
          color: "var(--text-main)",
        }}
      >
        {/* Status Indicator Icon */}
        <div className="w-6 h-6 rounded-lg flex items-center justify-center bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 shrink-0 group-hover:bg-cyan-500/20 transition-colors">
          <CurrentIcon className="w-3.5 h-3.5" />
        </div>

        {/* Command Control Stack */}
        <div className="flex flex-col text-left leading-tight">
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] font-mono font-bold tracking-wider uppercase text-slate-400">
              COMMAND CONTROL
            </span>
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
            </span>
          </div>
          <div className="flex items-center gap-1.5 pt-0.5">
            <span className="text-xs font-mono font-extrabold tracking-tight text-white">
              {currentMode.label}
            </span>
            <span className="text-[9px] font-mono px-1 py-0.2 rounded font-black uppercase border border-cyan-500/40 bg-cyan-500/10 text-cyan-400">
              {currentMode.badge}
            </span>
          </div>
        </div>

        {/* Chevron Indicator */}
        <ChevronDown
          className={`w-3.5 h-3.5 text-slate-400 transition-transform duration-200 ml-1 shrink-0 ${
            isOpen ? "rotate-180 text-cyan-400" : "group-hover:text-slate-200"
          }`}
        />
      </button>

      {/* ── Sleek Engineering Dropdown Menu ── */}
      {isOpen && (
        <div
          role="listbox"
          aria-label="Engine Mode Selector"
          className="absolute right-0 top-full mt-2 w-80 sm:w-96 rounded-2xl border shadow-2xl p-2 z-50 backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150"
          style={{
            background: "var(--bg-card)",
            borderColor: "var(--border-hover)",
          }}
        >
          {/* Header Bar */}
          <div className="px-3 pt-2 pb-2.5 border-b border-slate-500/15">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-cyan-400 font-mono text-[10px] font-black uppercase tracking-wider">
                <Terminal className="w-3.5 h-3.5" />
                <span>COMMAND CONTROL // SYSTEM MODES</span>
              </div>
              <span className="text-[9px] font-mono font-extrabold uppercase px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-400">
                ACTIVE PIPELINE
              </span>
            </div>
            <p className="text-[11px] text-slate-400 mt-1 font-mono leading-tight">
              Select inference &amp; reasoning engine topology
            </p>
          </div>

          {/* Mode Option Cards */}
          <div className="py-2 space-y-1.5">
            {MODES.map((item) => {
              const isActive = mode === item.id;
              const IconComp = item.icon;

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    onChange(item.id);
                    setIsOpen(false);
                  }}
                  className={`w-full group text-left p-2.5 rounded-xl border transition-all duration-150 flex items-start gap-3 cursor-pointer ${
                    isActive
                      ? "bg-cyan-500/10 border-cyan-500/50 shadow-md shadow-cyan-500/5"
                      : "border-transparent hover:bg-slate-500/10 hover:border-slate-500/20"
                  }`}
                >
                  {/* Mode Icon */}
                  <div
                    className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 border transition-colors ${
                      isActive
                        ? "bg-cyan-500/20 border-cyan-500/60 text-cyan-300"
                        : "bg-slate-500/10 border-slate-500/20 text-slate-400 group-hover:text-slate-200 group-hover:border-slate-500/40"
                    }`}
                  >
                    <IconComp className="w-4 h-4" />
                  </div>

                  {/* Mode Metadata */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-1">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`text-xs font-mono font-bold tracking-tight ${
                            isActive ? "text-cyan-300" : "text-white group-hover:text-cyan-200"
                          }`}
                        >
                          {item.label}
                        </span>
                        <span className="text-[9px] font-mono px-1 py-0.5 rounded font-black uppercase bg-slate-500/15 border border-slate-500/20 text-slate-300">
                          {item.badge}
                        </span>
                      </div>

                      {/* Active Status Pill */}
                      {isActive ? (
                        <div className="flex items-center gap-1 text-[9px] font-mono font-black text-emerald-400 bg-emerald-500/15 border border-emerald-500/30 px-1.5 py-0.5 rounded">
                          <Check className="w-3 h-3 text-emerald-400" />
                          <span>ENGAGED</span>
                        </div>
                      ) : (
                        <span className="text-[9px] font-mono uppercase text-slate-500 group-hover:text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity">
                          SELECT
                        </span>
                      )}
                    </div>

                    <p className="text-[11px] text-slate-400 font-sans mt-0.5 leading-snug">
                      {item.description}
                    </p>

                    <div className="flex items-center gap-2 mt-1.5 text-[9px] font-mono text-slate-500">
                      <span className="text-cyan-400/80 font-bold">{item.code}</span>
                      <span>•</span>
                      <span className="truncate">{item.techSpec}</span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Tactical Footer */}
          <div className="px-3 py-2 border-t border-slate-500/15 flex items-center justify-between text-[10px] font-mono text-slate-400">
            <div className="flex items-center gap-1 text-[#5ba0cc] dark:text-[#64a9d4] font-semibold">
              <Shield className="w-3 h-3" />
              <span>VENU-AI Core v2.4</span>
            </div>
            <span className="text-slate-500">ESC to close</span>
          </div>
        </div>
      )}
    </div>
  );
};

export const CommandControlDropdown = AgentModeToggle;
export default AgentModeToggle;
