"use client";

import { useState } from "react";
import {
  MessageCircle,
  Shield,
  Zap,
  CheckCircle2,
  Lock,
  Bot,
  FolderKanban,
  Sparkles,
  Users,
  ChevronRight,
} from "lucide-react";
import LoginForm from "@/components/login/LoginForm";
import ThemeToggle from "@/components/ThemeToggle";
import ParticleBackground from "@/components/login/ParticleBackground";

// ─── Interactive Feature Preview ─────────────────────────────────────────────

type FeatureTab = "messaging" | "ai" | "files";

function InteractiveFeatureShowcase() {
  const [activeTab, setActiveTab] = useState<FeatureTab>("messaging");
  const [reactions, setReactions] = useState({ fire: 42, bolt: 28, rocket: 64 });
  const [aiPromptText, setAiPromptText] = useState("");
  const [aiResponse, setAiResponse] = useState<string | null>(null);

  const tabs: { id: FeatureTab; label: string; icon: React.ElementType }[] = [
    { id: "messaging", label: "💬 Realtime Feed", icon: MessageCircle },
    { id: "ai", label: "🤖 AI Copilot", icon: Bot },
    { id: "files", label: "📁 File Vault", icon: FolderKanban },
  ];

  const handleAiPromptClick = (prompt: string, response: string) => {
    setAiPromptText(prompt);
    setAiResponse(null);
    setTimeout(() => {
      setAiResponse(response);
    }, 250);
  };

  return (
    <div className="space-y-4">
      {/* Tab Switcher */}
      <div
        className="flex items-center gap-1.5 p-1.5 rounded-2xl"
        style={{
          background: "var(--bg-input)",
          border: "1px solid var(--border-subtle)",
        }}
      >
        {tabs.map((t) => {
          const isActive = activeTab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setActiveTab(t.id)}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 px-3 rounded-xl text-xs font-black transition-all duration-200 ${
                isActive
                  ? "shadow-md scale-[1.02]"
                  : "opacity-70 hover:opacity-100"
              }`}
              style={{
                background: isActive ? "var(--bg-card)" : "transparent",
                color: isActive ? "var(--text-main)" : "var(--text-muted)",
                border: isActive ? "1px solid var(--border-subtle)" : "1px solid transparent",
              }}
            >
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* Dynamic Content Display */}
      <div className="rounded-2xl p-4.5 decent-card space-y-3.5 transition-all duration-250">
        {activeTab === "messaging" && (
          <div className="space-y-3 fade-in-up">
            <div
              className="flex items-center justify-between pb-2.5 border-b text-xs"
              style={{ borderColor: "var(--border-subtle)" }}
            >
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                <span className="font-bold" style={{ color: "var(--text-main)" }}>
                  #engineering-sync
                </span>
              </div>
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-subtle)" }}>
                ⚡ REALTIME WEBSOCKET ACTIVE
              </span>
            </div>

            <div className="space-y-2.5 text-xs">
              <div className="flex items-start gap-2.5">
                <div className="w-6 h-6 rounded-full bg-gradient-to-tr from-purple-600 to-indigo-600 flex items-center justify-center text-[9px] font-black text-white shrink-0 shadow-sm">
                  ML
                </div>
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="font-bold" style={{ color: "var(--text-main)" }}>
                      Maya Lin
                    </span>
                    <span className="text-[10px]" style={{ color: "var(--text-subtle)" }}>
                      10:14 AM
                    </span>
                  </div>
                  <p className="mt-0.5 leading-relaxed font-medium" style={{ color: "var(--text-muted)" }}>
                    Sub-10ms latency verified on global edges. Contrast ratio is elite!
                  </p>
                </div>
              </div>
            </div>

            {/* Interactive Reaction Pills */}
            <div className="flex items-center gap-2 pt-1 border-t" style={{ borderColor: "var(--border-subtle)" }}>
              <span className="text-[10px] font-black uppercase tracking-wider" style={{ color: "var(--text-subtle)" }}>
                TAP TO REACT:
              </span>
              <button
                type="button"
                onClick={() => setReactions((r) => ({ ...r, fire: r.fire + 1 }))}
                className="px-2 py-1 rounded-full text-[11px] font-bold border transition-all active:scale-125"
                style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
              >
                🔥 {reactions.fire}
              </button>
              <button
                type="button"
                onClick={() => setReactions((r) => ({ ...r, bolt: r.bolt + 1 }))}
                className="px-2 py-1 rounded-full text-[11px] font-bold border transition-all active:scale-125"
                style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
              >
                ⚡ {reactions.bolt}
              </button>
              <button
                type="button"
                onClick={() => setReactions((r) => ({ ...r, rocket: r.rocket + 1 }))}
                className="px-2 py-1 rounded-full text-[11px] font-bold border transition-all active:scale-125"
                style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
              >
                🚀 {reactions.rocket}
              </button>
            </div>
          </div>
        )}

        {activeTab === "ai" && (
          <div className="space-y-3 fade-in-up text-xs">
            <div
              className="flex items-center justify-between pb-2.5 border-b font-bold"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
            >
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4" />
                <span>VENU AI COPILOT SIMULATOR</span>
              </div>
              <span className="text-[10px] font-black uppercase" style={{ color: "var(--text-subtle)" }}>
                ONLINE
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => handleAiPromptClick("Summarize #engineering-sync", "Thread Summary: Deployed UI refinements and verified sub-10ms response latency across all staging nodes.")}
                className="px-2.5 py-1 rounded-lg text-[10px] font-bold border transition-all hover:scale-105"
                style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
              >
                ⚡ Summarize thread
              </button>
              <button
                type="button"
                onClick={() => handleAiPromptClick("Check API Health", "System Health: All 24 WebSocket edge clusters operating at 99.99% uptime with zero packet loss.")}
                className="px-2.5 py-1 rounded-lg text-[10px] font-bold border transition-all hover:scale-105"
                style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
              >
                🛡️ Check API status
              </button>
            </div>

            <div
              className="p-3 rounded-xl space-y-1.5 min-h-[50px] transition-all"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}
            >
              <p className="font-bold" style={{ color: "var(--text-main)" }}>
                {aiPromptText ? `🤖 Response to "${aiPromptText}":` : "🤖 Click an AI prompt above to test live assistant response"}
              </p>
              {aiResponse && (
                <p className="leading-relaxed font-medium scale-in" style={{ color: "var(--text-muted)" }}>
                  {aiResponse}
                </p>
              )}
            </div>
          </div>
        )}

        {activeTab === "files" && (
          <div className="space-y-3 fade-in-up text-xs">
            <div
              className="flex items-center justify-between pb-2.5 border-b font-bold uppercase tracking-wider"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-main)" }}
            >
              <span>Shared File Vault</span>
              <span className="text-[10px] font-black">2 SECURE ATTACHMENTS</span>
            </div>

            <div className="space-y-2">
              {[
                { name: "design_system_v2.pdf", size: "2.4 MB", type: "PDF Document" },
                { name: "build_metrics_log.csv", size: "840 KB", type: "Data Log" },
              ].map((f, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between p-2.5 rounded-xl border transition-all hover:scale-[1.01]"
                  style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)" }}
                >
                  <div className="flex items-center gap-2.5">
                    <FolderKanban className="w-4 h-4" style={{ color: "var(--text-main)" }} />
                    <div>
                      <p className="font-bold" style={{ color: "var(--text-main)" }}>
                        {f.name}
                      </p>
                      <p className="text-[10px] font-medium" style={{ color: "var(--text-subtle)" }}>
                        {f.size} · {f.type}
                      </p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4" style={{ color: "var(--text-subtle)" }} />
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function LoginPage() {
  return (
    <main
      className="relative min-h-screen w-full flex overflow-hidden transition-colors duration-250"
      style={{ background: "var(--bg-main)", color: "var(--text-main)" }}
    >
      {/* Animated Particle Background Canvas */}
      <ParticleBackground />

      {/* Soft Ambient Light Accents */}
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-indigo-500/5 rounded-full blur-3xl pointer-events-none" />

      {/* Fixed Theme Toggle Switch Top Right */}
      <div className="absolute top-5 right-5 z-50">
        <ThemeToggle showLabel />
      </div>

      {/* ── Left: Product showcase panel (desktop only) ── */}
      <div className="relative hidden lg:flex lg:w-[50%] xl:w-[52%] flex-col justify-between p-10 xl:p-14 border-r border-slate-500/10">
        {/* Brand header + Status indicator */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center overflow-hidden bg-slate-950 border border-cyan-500/40 shadow-md shrink-0">
              <img src="/drdo-logo.png" alt="DRDO Official Logo" className="w-9 h-9 object-contain" />
            </div>
            <div>
              <span className="text-base font-black tracking-tight text-cyan-400 block uppercase">
                DRDO VenuAI
              </span>
              <span className="block text-[10px] font-bold tracking-wider text-slate-400 uppercase">
                Ministry of Defence • Govt. of India
              </span>
            </div>
          </div>

          <div
            className="flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-bold"
            style={{
              background: "var(--badge-bg)",
              border: "1px solid var(--badge-border)",
              color: "var(--badge-text)",
            }}
          >
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span>DRDO Network Active</span>
          </div>
        </div>

        {/* Product showcase content */}
        <div className="space-y-6 max-w-lg my-auto py-6">
          <div className="space-y-3.5">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-black tracking-widest uppercase border border-cyan-500/40 bg-cyan-500/10 text-cyan-400 shadow-sm">
              <Shield className="w-3.5 h-3.5" />
              <span>DEFENCE R&amp;D NETWORK • SCIENTIFIC PORTAL</span>
            </div>
            <h1 className="text-3xl xl:text-4xl font-black leading-tight tracking-tight uppercase" style={{ color: "var(--text-main)" }}>
              BALASYA MULAM VIJNANAM <br />
              <span className="text-cyan-400 text-2xl xl:text-3xl block pt-1 font-bold">STRENGTH&apos;S ORIGIN IS IN SCIENCE</span>
            </h1>
            <p className="text-xs sm:text-sm leading-relaxed font-medium" style={{ color: "var(--text-muted)" }}>
              Official AI Knowledge Engine for 50+ DRDO Laboratories, Missile Programs, Radar Systems, Armaments, and Technical Research Papers.
            </p>
          </div>

          {/* Interactive Feature Tabs */}
          <InteractiveFeatureShowcase />

          {/* Official DRDO Motto Card */}
          <div
            className="p-4 rounded-2xl decent-card space-y-2.5 border"
            style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
          >
            <p className="text-xs italic font-medium leading-relaxed" style={{ color: "var(--text-muted)" }}>
              &ldquo;DRDO is dedicated to achieving self-reliance in critical defence technologies and equipping India&apos;s Armed Forces with world-class indigenous weapons platforms.&rdquo;
            </p>
            <div className="flex items-center gap-2.5 pt-0.5">
              <div className="w-8 h-8 rounded-xl bg-slate-950 border border-cyan-500/40 flex items-center justify-center text-[11px] font-black text-cyan-400 shrink-0 shadow-sm">
                DRDO
              </div>
              <div>
                <p className="text-xs font-black" style={{ color: "var(--text-main)" }}>
                  Dr. Samir V. Kamat
                </p>
                <p className="text-[10px] font-semibold text-cyan-400">
                  Secretary, Dept. of Defence R&amp;D &amp; Chairman, DRDO
                </p>
              </div>
            </div>
          </div>

          {/* Feature Highlights Grid */}
          <div className="grid grid-cols-2 gap-3">
            <div className="p-3.5 rounded-2xl decent-card flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center font-black text-xs shrink-0" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                🏛️
              </div>
              <div>
                <span className="text-xs font-black block tracking-wide uppercase" style={{ color: "var(--text-main)" }}>50+ LABS</span>
                <p className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Integrated R&amp;D Cluster</p>
              </div>
            </div>

            <div className="p-3.5 rounded-2xl decent-card flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center font-black text-xs shrink-0" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                🛡️
              </div>
              <div>
                <span className="text-xs font-black block tracking-wide uppercase" style={{ color: "var(--text-main)" }}>VERIFIED RAG</span>
                <p className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Grounding Audit Engine</p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer info */}
        <div className="flex items-center justify-between text-xs pt-4 border-t" style={{ borderColor: "var(--border-subtle)", color: "var(--text-subtle)" }}>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-500" />
            <span>DRDO VenuAI Intelligence System v2.4</span>
          </div>
          <div className="flex items-center gap-1 font-semibold text-cyan-400">
            <Shield className="w-3.5 h-3.5" />
            <span>Ministry of Defence</span>
          </div>
        </div>
      </div>

      {/* ── Right: Clean Login Form Panel ── */}
      <div className="relative flex-1 flex flex-col items-center justify-center px-5 py-10 sm:px-8 lg:px-10 xl:px-14">
        {/* Mobile header logo */}
        <div className="flex lg:hidden items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center overflow-hidden bg-slate-950 border border-cyan-500/40 shadow-sm shrink-0">
            <img src="/drdo-logo.png" alt="DRDO Logo" className="w-8 h-8 object-contain" />
          </div>
          <div>
            <span className="text-base font-black tracking-tight text-cyan-400 block uppercase">
              DRDO VenuAI
            </span>
            <span className="block text-[9px] font-bold text-slate-400 uppercase">
              Defence R&amp;D Portal
            </span>
          </div>
        </div>

        {/* Form header */}
        <div className="w-full max-w-md mb-6 fade-in-up">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider mb-2" style={{ background: "var(--badge-bg)", color: "var(--badge-text)", border: "1px solid var(--badge-border)" }}>
            <Lock className="w-3 h-3 text-cyan-500" />
            <span>AUTHORIZED PERSONNEL ONLY</span>
          </div>
          <h2 className="text-2xl sm:text-3xl font-black tracking-tight" style={{ color: "var(--text-main)" }}>
            Scientist &amp; Personnel Sign-In
          </h2>
          <p className="text-xs sm:text-sm mt-1.5 leading-relaxed font-medium" style={{ color: "var(--text-muted)" }}>
            Enter your official credentials to access the DRDO R&amp;D Knowledge Engine.
          </p>
        </div>

        {/* Form component */}
        <LoginForm />

        {/* Terms footer */}
        <p className="mt-8 text-xs text-center max-w-xs leading-relaxed font-medium" style={{ color: "var(--text-subtle)" }}>
          DRDO Scientific R&amp;D Portal • Ministry of Defence, Govt. of India. Unauthorized access is strictly prohibited.
        </p>
      </div>
    </main>
  );
}




