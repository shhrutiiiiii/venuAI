"use client";

import { Copy, Check, ShieldCheck } from "lucide-react";
import { useState } from "react";

interface DemoCredentialsBadgeProps {
  onUseCredentials?: (email: string, password: string) => void;
}

export default function DemoCredentialsBadge({
  onUseCredentials,
}: DemoCredentialsBadgeProps) {
  const [copiedField, setCopiedField] = useState<"email" | "password" | null>(
    null
  );

  const DEMO_EMAIL = "demo@chat.com";
  const DEMO_PASSWORD = "demo12345";

  const copyToClipboard = async (
    text: string,
    field: "email" | "password"
  ) => {
    await navigator.clipboard.writeText(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  return (
    <div className="rounded-2xl decent-card p-4 space-y-3 fade-in-up">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-cyan-500" />
          <span className="text-xs font-black tracking-wide uppercase" style={{ color: "var(--text-main)" }}>
            ⚡ DRDO DEMO CREDENTIALS
          </span>
        </div>
        <span className="text-[10px] px-2 py-0.5 rounded-full font-black uppercase tracking-wider" style={{ background: "var(--badge-bg)", color: "var(--badge-text)", border: "1px solid var(--badge-border)" }}>
          ONE-CLICK AUTOFILL
        </span>
      </div>

      {/* Quick Auto-Fill Action Buttons */}
      {onUseCredentials && (
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => onUseCredentials(DEMO_EMAIL, DEMO_PASSWORD)}
            className="flex items-center justify-between p-3 rounded-xl text-left transition-all duration-150 border group hover:scale-[1.02]"
            style={{
              background: "var(--bg-input)",
              borderColor: "var(--border-subtle)",
            }}
          >
            <div>
              <span className="text-[10px] uppercase font-black block tracking-wider" style={{ color: "var(--text-main)" }}>
                🔬 DRDO SCIENTIST
              </span>
              <span className="text-[11px] font-semibold block truncate" style={{ color: "var(--text-muted)" }}>
                demo@chat.com
              </span>
            </div>
            <div className="text-[10px] px-2 py-0.5 rounded-lg font-black uppercase tracking-wider transition-colors" style={{ background: "var(--badge-bg)", color: "var(--badge-text)" }}>
              FILL
            </div>
          </button>

          <button
            type="button"
            onClick={() => onUseCredentials("admin@chat.com", "admin12345")}
            className="flex items-center justify-between p-3 rounded-xl text-left transition-all duration-150 border group hover:scale-[1.02]"
            style={{
              background: "var(--bg-input)",
              borderColor: "var(--border-subtle)",
            }}
          >
            <div>
              <span className="text-[10px] uppercase font-black block tracking-wider text-cyan-400">
                🛡️ LAB DIRECTOR
              </span>
              <span className="text-[11px] font-semibold block truncate" style={{ color: "var(--text-muted)" }}>
                admin@chat.com
              </span>
            </div>
            <div className="text-[10px] px-2 py-0.5 rounded-lg font-black uppercase tracking-wider transition-colors" style={{ background: "var(--badge-bg)", color: "var(--badge-text)" }}>
              FILL
            </div>
          </button>
        </div>
      )}

      {/* Copy credential helper */}
      <div className="flex items-center justify-between text-[11px] pt-1" style={{ color: "var(--text-subtle)" }}>
        <button
          type="button"
          onClick={() => copyToClipboard(DEMO_EMAIL, "email")}
          className="flex items-center gap-1 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
        >
          {copiedField === "email" ? (
            <Check className="w-3 h-3 text-emerald-500" />
          ) : (
            <Copy className="w-3 h-3" />
          )}
          <span>Copy Demo User</span>
        </button>

        <button
          type="button"
          onClick={() => copyToClipboard("admin@chat.com", "email")}
          className="flex items-center gap-1 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
        >
          {copiedField === "password" ? (
            <Check className="w-3 h-3 text-emerald-500" />
          ) : (
            <Copy className="w-3 h-3" />
          )}
          <span>Copy Admin</span>
        </button>
      </div>
    </div>
  );
}



