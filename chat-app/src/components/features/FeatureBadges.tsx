import React from "react";

export type TokenCost = "none" | "low" | "medium" | "high";

export interface FeatureInfo {
  key: string;
  label: string;
  enabled: boolean;
  per_user_opt_in: boolean;
  token_cost: TokenCost;
  token_cost_note: string;
  enable_with: string;
  requires: string;
}

const COST_COLOR: Record<TokenCost, string> = { none: "#10b981", low: "#3b82f6", medium: "#f59e0b", high: "#f43f5e" };

export function StateBadge({ on }: { on: boolean }) {
  return (
    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold border" style={{ color: on ? "#10b981" : "var(--text-subtle)", borderColor: on ? "#10b981" : "var(--border-subtle)" }}>
      {on ? "On" : "Off"}
    </span>
  );
}

export function CostBadge({ cost }: { cost: TokenCost }) {
  const color = COST_COLOR[cost] ?? "var(--text-muted)";
  return (
    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold border" style={{ color, borderColor: color }}>
      Token cost: {cost}
    </span>
  );
}
