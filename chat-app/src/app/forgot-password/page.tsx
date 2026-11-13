"use client";

import { ArrowLeft, Mail } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Could not send the reset email. Please try again.");
        return;
      }
      // The backend always returns 202 regardless of whether the email
      // belongs to a real account — deliberate, to avoid confirming
      // account existence. The UI shows the same "check your inbox"
      // state either way for the same reason.
      setSent(true);
    } catch {
      setError("Could not reach the server. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main
      className="min-h-screen flex items-center justify-center px-5"
      style={{ background: "rgba(6, 8, 24, 0.8)" }}
    >
      {/* Background orb */}
      <div
        className="pointer-events-none fixed inset-0 overflow-hidden"
        aria-hidden
      >
        <div
          className="absolute orb-animate"
          style={{
            width: "500px",
            height: "500px",
            borderRadius: "50%",
            background:
              "radial-gradient(circle, rgba(124,58,237,0.15) 0%, transparent 70%)",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            filter: "blur(50px)",
          }}
        />
      </div>

      <div className="relative w-full max-w-sm fade-in-up">
        <Link
          href="/login"
          className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-violet-400 transition-colors mb-8"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to sign in
        </Link>

        <div
          className="rounded-3xl p-8 space-y-6"
          style={{
            background:
              "linear-gradient(135deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.02) 100%)",
            border: "1px solid rgba(255,255,255,0.08)",
            backdropFilter: "blur(24px)",
          }}
        >
          <div>
            <div
              className="w-12 h-12 rounded-2xl flex items-center justify-center mb-4"
              style={{ background: "linear-gradient(135deg, #7c3aed, #6366f1)" }}
            >
              <Mail className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-white">Reset password</h1>
            <p className="text-sm text-slate-500 mt-1">
              Enter your email and we&apos;ll send you a reset link.
            </p>
          </div>

          {sent ? (
            <div className="text-center py-4 space-y-2">
              <p className="text-3xl">📬</p>
              <p className="text-sm font-semibold text-emerald-400">Check your inbox!</p>
              <p className="text-xs text-slate-500">
                We sent a reset link to <strong className="text-slate-300">{email}</strong>
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              {error && (
                <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2">
                  {error}
                </p>
              )}
              <div
                className="flex items-center gap-3 px-4 py-3 rounded-2xl"
                style={{
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                }}
              >
                <Mail className="w-4 h-4 text-slate-500 shrink-0" />
                <input
                  type="email"
                  placeholder="your@email.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="flex-1 bg-transparent text-sm text-slate-200 placeholder:text-slate-600 outline-none"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={loading || !email}
                className="w-full py-3 rounded-2xl text-sm font-semibold text-white disabled:opacity-60 disabled:cursor-not-allowed transition-all hover:scale-[1.02] hover:shadow-[0_8px_30px_rgba(139,92,246,0.3)]"
                style={{
                  background:
                    "linear-gradient(135deg, #7c3aed 0%, #6366f1 100%)",
                }}
              >
                {loading ? "Sending…" : "Send reset link"}
              </button>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
