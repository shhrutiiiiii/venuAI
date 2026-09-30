"use client";

import React from "react";
import { Cpu, Wrench, Layers } from "lucide-react";

export type AgentMode = "rag" | "react" | "multi_agent";

interface AgentModeToggleProps {
  mode: AgentMode;
  onChange: (mode: AgentMode) => void;
  disabled?: boolean;
}

export const AgentModeToggle: React.FC<AgentModeToggleProps> = ({
  mode,
  onChange,
  disabled = false,
}) => {
  const modes: { id: AgentMode; label: string; icon: React.ReactNode; tooltip: string }[] = [
    {
      id: "rag",
      label: "Direct RAG",
      icon: <Cpu className="w-3.5 h-3.5" />,
      tooltip: "Hybrid ChromaDB + BM25 vector search with Groq LLM verification.",
    },
    {
      id: "react",
      label: "ReAct Tools",
      icon: <Wrench className="w-3.5 h-3.5" />,
      tooltip: "ReAct loop with 6 tools (calculator, memory recall, web search, doc summary).",
    },
    {
      id: "multi_agent",
      label: "Multi-Agent Swarm",
      icon: <Layers className="w-3.5 h-3.5" />,
      tooltip: "Orchestrator routing to specialized Lab/Technical/Search sub-agents.",
    },
  ];

  return (
    <div className="inline-flex items-center p-1 rounded-xl bg-slate-900/90 border border-slate-800 text-xs shadow-inner">
      {modes.map((item) => {
        const isActive = mode === item.id;
        return (
          <button
            key={item.id}
            type="button"
            disabled={disabled}
            onClick={() => onChange(item.id)}
            title={item.tooltip}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all duration-150 ${
              isActive
                ? "bg-purple-600 text-white shadow-md shadow-purple-600/30 font-semibold"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
            } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        );
      })}
    </div>
  );
};
