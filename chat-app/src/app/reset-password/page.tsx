"use client";

import { ArrowLeft, Lock, CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "This reset link is invalid or has expired.");
        return;
      }
      setDone(true);
      setTimeout(() => router.push("/login"), 1800);
    } catch {
      setError("Could not reach the server. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="text-center py-4 space-y-2">
        <p className="text-sm font-semibold text-red-400">Missing reset token</p>
        <p className="text-xs text-slate-500">This link is incomplete. Request a new one from the sign-in page.</p>
      </div>
    );
  }

  if (done) {
    return (
      <div className="text-center py-4 space-y-2">
        <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto" />
        <p className="text-sm font-semibold text-emerald-400">Password updated</p>
        <p className="text-xs text-slate-500">Redirecting you to sign in…</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2">{error}</p>
      )}
      <div
        className="flex items-center gap-3 px-4 py-3 rounded-2xl"
        style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}
      >
        <Lock className="w-4 h-4 text-slate-500 shrink-0" />
        <input
          type="password"
          placeholder="New password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="flex-1 bg-transparent text-sm text-slate-200 placeholder:text-slate-600 outline-none"
          required
        />
      </div>
      <div
        className="flex items-center gap-3 px-4 py-3 rounded-2xl"
        style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}
      >
        <Lock className="w-4 h-4 text-slate-500 shrink-0" />
        <input
          type="password"
          placeholder="Confirm new password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className="flex-1 bg-transparent text-sm text-slate-200 placeholder:text-slate-600 outline-none"
          required
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full py-3 rounded-2xl text-sm font-semibold text-white disabled:opacity-60 disabled:cursor-not-allowed transition-all hover:scale-[1.02] hover:shadow-[0_8px_30px_rgba(139,92,246,0.3)]"
        style={{ background: "linear-gradient(135deg, #7c3aed 0%, #6366f1 100%)" }}
      >
        {loading ? "Updating…" : "Update password"}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <main className="min-h-screen flex items-center justify-center px-5" style={{ background: "#060818" }}>
      <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden>
        <div
          className="absolute orb-animate"
          style={{
            width: "500px", height: "500px", borderRadius: "50%",
            background: "radial-gradient(circle, rgba(124,58,237,0.15) 0%, transparent 70%)",
            top: "50%", left: "50%", transform: "translate(-50%, -50%)", filter: "blur(50px)",
          }}
        />
      </div>

      <div className="relative w-full max-w-sm fade-in-up">
        <Link href="/login" className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-violet-400 transition-colors mb-8">
          <ArrowLeft className="w-4 h-4" />
          Back to sign in
        </Link>

        <div
          className="rounded-3xl p-8 space-y-6"
          style={{
            background: "linear-gradient(135deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.02) 100%)",
            border: "1px solid rgba(255,255,255,0.08)", backdropFilter: "blur(24px)",
          }}
        >
          <div>
            <div className="w-12 h-12 rounded-2xl flex items-center justify-center mb-4" style={{ background: "linear-gradient(135deg, #7c3aed, #6366f1)" }}>
              <Lock className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-white">Set a new password</h1>
            <p className="text-sm text-slate-500 mt-1">Choose a new password for your account.</p>
          </div>

          <Suspense fallback={null}>
            <ResetPasswordForm />
          </Suspense>
        </div>
      </div>
    </main>
  );
}
