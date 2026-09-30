"use client";

import React from "react";
import { ShieldCheck, AlertTriangle, ShieldAlert } from "lucide-react";

interface ConfidenceBadgeProps {
  score: number;
  hallucinationScore?: number;
  className?: string;
}

export const ConfidenceBadge: React.FC<ConfidenceBadgeProps> = ({
  score,
  hallucinationScore,
  className = "",
}) => {
  // Normalize score between 0 and 1
  const validScore = Math.min(Math.max(score || 0, 0), 1);
  const percentage = Math.round(validScore * 100);

  let colorClasses = "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
  let icon = <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />;
  let label = "High Grounding";

  if (validScore < 0.4) {
    colorClasses = "bg-rose-500/10 text-rose-400 border-rose-500/30";
    icon = <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />;
    label = "Low Grounding";
  } else if (validScore < 0.7) {
    colorClasses = "bg-cyan-500/10 text-cyan-400 border-cyan-500/30";
    icon = <AlertTriangle className="w-3.5 h-3.5 text-cyan-400" />;
    label = "Medium Grounding";
  }

  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border shadow-xs transition-all hover:scale-105 cursor-pointer ${colorClasses} ${className}`}
      title={`Grounding Score: ${percentage}% | Hallucination Index: ${(hallucinationScore || 0).toFixed(2)}`}
    >
      {icon}
      <span>{label}</span>
      <span className="font-semibold ml-0.5 opacity-90">{percentage}%</span>
    </div>
  );
};
