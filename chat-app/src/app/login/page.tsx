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
          <div className="flex items-center gap-2.5">
            <div
              className="flex items-center justify-center w-9 h-9 rounded-xl text-white shadow-sm"
              style={{ background: "var(--primary-accent)" }}
            >
              <MessageCircle className="w-5 h-5" />
            </div>
            <div>
              <span className="text-lg font-bold tracking-tight" style={{ color: "var(--text-main)" }}>
                VenuAI
              </span>
              <span className="block text-[10px] font-medium" style={{ color: "var(--text-subtle)" }}>
                Team Collaboration Platform
              </span>
            </div>
          </div>

          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium"
            style={{
              background: "var(--badge-bg)",
              border: "1px solid var(--badge-border)",
              color: "var(--badge-text)",
            }}
          >
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span>Systems Operational</span>
          </div>
        </div>

        {/* Product showcase content */}
        <div className="space-y-6 max-w-lg my-auto py-6">
          <div className="space-y-3.5">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-black tracking-widest uppercase" style={{ background: "var(--badge-bg)", color: "var(--badge-text)", border: "1px solid var(--badge-border)" }}>
              <Sparkles className="w-3.5 h-3.5" />
              ✦ NEXT-GEN WORKSPACE • NO CAP 🚀
            </div>
            <h1 className="text-4xl xl:text-5xl font-black leading-tight tracking-tight uppercase" style={{ color: "var(--text-main)" }}>
              BUILT DIFFERENT. <br />
              <span className="genz-gradient-text">ZERO NOISE. PURE VIBE.</span>
            </h1>
            <p className="text-sm leading-relaxed font-medium" style={{ color: "var(--text-muted)" }}>
              Hyper-fast channels, instant AI copilot context, and sub-10ms realtime sync engineered for creator teams.
            </p>
          </div>

          {/* Interactive Feature Tabs */}
          <InteractiveFeatureShowcase />

          {/* Humanized Quote Testimonial Card */}
          <div
            className="p-4 rounded-2xl decent-card space-y-2.5 border"
            style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
          >
            <p className="text-xs italic font-medium leading-relaxed" style={{ color: "var(--text-muted)" }}>
              &ldquo;VenuAI is literally unmatched. Sub-10ms response time and the clean monochrome aesthetic make everyday syncs elite.&rdquo;
            </p>
            <div className="flex items-center gap-2.5 pt-0.5">
              <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-slate-700 to-zinc-900 border border-white/20 flex items-center justify-center text-[11px] font-black text-white shrink-0 shadow-sm">
                AR
              </div>
              <div>
                <p className="text-xs font-black" style={{ color: "var(--text-main)" }}>
                  Alex Rivera ✦
                </p>
                <p className="text-[10px] font-medium" style={{ color: "var(--text-subtle)" }}>
                  Creator &amp; Lead Architect @ Studio Mode
                </p>
              </div>
            </div>
          </div>

          {/* Feature Highlights Grid */}
          <div className="grid grid-cols-2 gap-3">
            <div className="p-3.5 rounded-2xl decent-card flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center font-black text-xs shrink-0" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                ⚡
              </div>
              <div>
                <span className="text-xs font-black block tracking-wide uppercase" style={{ color: "var(--text-main)" }}>10ms SPEED</span>
                <p className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>WebSocket engine</p>
              </div>
            </div>

            <div className="p-3.5 rounded-2xl decent-card flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center font-black text-xs shrink-0" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                🛡️
              </div>
              <div>
                <span className="text-xs font-black block tracking-wide uppercase" style={{ color: "var(--text-main)" }}>E2E LOCKED</span>
                <p className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Encrypted sessions</p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer info */}
        <div className="flex items-center justify-between text-xs pt-4 border-t" style={{ borderColor: "var(--border-subtle)", color: "var(--text-subtle)" }}>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            <span>VenuAI Workspace v2.4</span>
          </div>
          <div className="flex items-center gap-1">
            <Users className="w-3.5 h-3.5" />
            <span>50,000+ active team members</span>
          </div>
        </div>
      </div>

      {/* ── Right: Clean Login Form Panel ── */}
      <div className="relative flex-1 flex flex-col items-center justify-center px-5 py-10 sm:px-8 lg:px-10 xl:px-14">
        {/* Mobile header logo */}
        <div className="flex lg:hidden items-center gap-2.5 mb-8">
          <div
            className="flex items-center justify-center w-9 h-9 rounded-xl text-white shadow-sm"
            style={{ background: "var(--primary-accent)" }}
          >
            <MessageCircle className="w-5 h-5" />
          </div>
          <span className="text-lg font-bold" style={{ color: "var(--text-main)" }}>
            VenuAI
          </span>
        </div>

        {/* Form header */}
        <div className="w-full max-w-md mb-6 fade-in-up">
          <h2 className="text-2xl sm:text-3xl font-bold tracking-tight" style={{ color: "var(--text-main)" }}>
            Sign in to VenuAI
          </h2>
          <p className="text-sm mt-1.5 leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Welcome back. Please enter your credentials to access your workspace.
          </p>
        </div>

        {/* Form component */}
        <LoginForm />

        {/* Terms footer */}
        <p className="mt-8 text-xs text-center max-w-xs leading-relaxed" style={{ color: "var(--text-subtle)" }}>
          By signing in, you agree to our{" "}
          <a href="/terms" className="underline underline-offset-2 hover:opacity-80" style={{ color: "var(--text-muted)" }}>
            Terms of Service
          </a>{" "}
          and{" "}
          <a href="/privacy" className="underline underline-offset-2 hover:opacity-80" style={{ color: "var(--text-muted)" }}>
            Privacy Policy
          </a>
          .
        </p>
      </div>
    </main>
  );
}




