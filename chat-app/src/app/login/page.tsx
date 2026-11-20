"use client";

import { Suspense } from "react";
import {
  Shield,
  CheckCircle2,
  Lock,
} from "lucide-react";
import LoginForm from "@/components/login/LoginForm";
import ThemeToggle from "@/components/ThemeToggle";
import ParticleBackground from "@/components/login/ParticleBackground";

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function LoginPage() {
  return (
    <main
      className="relative min-h-screen w-full flex overflow-hidden transition-colors duration-250"
      style={{ color: "var(--text-main)" }}
    >
      {/* Soft Ambient Light Accents */}
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-[#5ba0cc]/5 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-[#5ba0cc]/3 rounded-full blur-3xl pointer-events-none" />

      {/* Fixed Theme Toggle Switch Top Right */}
      <div className="absolute top-5 right-5 z-50">
        <ThemeToggle showLabel />
      </div>

      {/* ── Left: Product showcase panel (desktop only) ── */}
      <div className="relative hidden lg:flex lg:w-[50%] xl:w-[52%] flex-col justify-between p-10 xl:p-14 border-r border-slate-500/10">
        {/* Brand header + Status indicator */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center overflow-hidden bg-slate-950 border border-[#5ba0cc]/30 shadow-sm shrink-0">
              <img src="/drdo-logo.png" alt="DRDO Official Logo" className="w-9 h-9 object-contain" />
            </div>
            <div>
              <span className="text-base font-black tracking-tight text-[#5ba0cc] dark:text-[#64a9d4] block uppercase">
                VENU-AI
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
        <div className="space-y-10 max-w-lg my-auto py-6">
          <div className="space-y-3.5">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-bold tracking-widest uppercase border border-[#5ba0cc]/30 bg-[#5ba0cc]/10 text-[#5ba0cc] dark:text-[#64a9d4]">
              <Shield className="w-3.5 h-3.5" />
              <span>DEFENCE R&amp;D NETWORK • SCIENTIFIC PORTAL</span>
            </div>
            <h1 className="text-3xl xl:text-4xl font-black leading-tight tracking-tight uppercase" style={{ color: "var(--text-main)" }}>
              BALASYA MULAM VIJNANAM <br />
              <span className="text-[#5ba0cc] dark:text-[#64a9d4] text-xl sm:text-2xl xl:text-3xl block pt-1 font-bold tracking-tight">STRENGTH&apos;S ORIGIN IS IN SCIENCE</span>
            </h1>
            <p className="text-xs sm:text-sm leading-relaxed font-medium" style={{ color: "var(--text-muted)" }}>
              Official AI Knowledge Engine for 50+ DRDO Laboratories, Missile Programs, Radar Systems, Armaments, and Technical Research Papers.
            </p>
          </div>

          {/* Official DRDO Motto Card */}
          <div
            className="p-5 rounded-2xl decent-card space-y-3 border"
            style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
          >
            <p className="text-xs sm:text-sm italic font-medium leading-relaxed" style={{ color: "var(--text-muted)" }}>
              &ldquo;DRDO is dedicated to achieving self-reliance in critical defence technologies and equipping India&apos;s Armed Forces with world-class indigenous weapons platforms.&rdquo;
            </p>
            <div className="flex items-center gap-2.5 pt-0.5">
              <div className="w-9 h-9 rounded-xl bg-slate-950 border border-[#5ba0cc]/30 flex items-center justify-center text-xs font-black text-[#5ba0cc] dark:text-[#64a9d4] shrink-0 shadow-sm">
                DRDO
              </div>
              <div>
                <p className="text-xs sm:text-sm font-black" style={{ color: "var(--text-main)" }}>
                  Dr. Samir V. Kamat
                </p>
                <p className="text-[10px] sm:text-xs font-semibold text-[#5ba0cc] dark:text-[#64a9d4]">
                  Secretary, Dept. of Defence R&amp;D &amp; Chairman, DRDO
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer info */}
        <div className="flex items-center justify-between text-xs pt-4 border-t" style={{ borderColor: "var(--border-subtle)", color: "var(--text-subtle)" }}>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-500" />
            <span>VENU-AI Intelligence System v2.4</span>
          </div>
          <div className="flex items-center gap-1 font-semibold text-[#5ba0cc] dark:text-[#64a9d4]">
            <Shield className="w-3.5 h-3.5" />
            <span>Ministry of Defence</span>
          </div>
        </div>
      </div>

      {/* ── Right: Clean Login Form Panel ── */}
      <div className="relative flex-1 flex flex-col items-center justify-center px-5 py-10 sm:px-8 lg:px-10 xl:px-14">
        {/* Mobile header logo */}
        <div className="flex lg:hidden items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center overflow-hidden bg-slate-950 border border-[#5ba0cc]/30 shadow-sm shrink-0">
            <img src="/drdo-logo.png" alt="DRDO Logo" className="w-8 h-8 object-contain" />
          </div>
          <div>
            <span className="text-base font-black tracking-tight text-[#5ba0cc] dark:text-[#64a9d4] block uppercase">
              VENU-AI
            </span>
            <span className="block text-[9px] font-bold text-slate-400 uppercase">
              Defence R&amp;D Portal
            </span>
          </div>
        </div>

        {/* Form header */}
        <div className="w-full max-w-md mb-6 fade-in-up">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider mb-2" style={{ background: "var(--badge-bg)", color: "var(--badge-text)", border: "1px solid var(--badge-border)" }}>
            <Lock className="w-3 h-3 text-[#5ba0cc] dark:text-[#64a9d4]" />
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
        <Suspense fallback={null}><LoginForm /></Suspense>

        {/* Terms footer */}
        <p className="mt-8 text-xs text-center max-w-xs leading-relaxed font-medium" style={{ color: "var(--text-subtle)" }}>
          DRDO Scientific R&amp;D Portal • Ministry of Defence, Govt. of India. Unauthorized access is strictly prohibited.
        </p>
      </div>
    </main>
  );
}
