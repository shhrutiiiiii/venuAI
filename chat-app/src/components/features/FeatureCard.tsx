import React from "react";
import { CostBadge, StateBadge, type FeatureInfo } from "./FeatureBadges";

/** One feature: state + cost badges, honest note, how to enable. `children` = feature-specific controls. */
export function FeatureCard({ feature, children }: { feature: FeatureInfo; children?: React.ReactNode }) {
  return (
    <div className="rounded-2xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <h3 className="text-sm font-bold">{feature.label}</h3>
        <StateBadge on={feature.enabled} />
        <CostBadge cost={feature.token_cost} />
      </div>
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>{feature.token_cost_note}</p>
      <p className="text-[11px] mt-1" style={{ color: "var(--text-subtle)" }}>
        How to enable: <code>{feature.enable_with}</code>
        {feature.requires ? ` (${feature.requires})` : ""}
      </p>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}
