"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { Mail, Lock, Building2, ArrowRight, AlertCircle, CheckCircle2 } from "lucide-react";
import { z } from "zod";
import { signupSchema, register as apiRegister } from "@/lib/authClient";
import { useAuth } from "@/contexts/AuthContext";
import ThemeToggle from "@/components/ThemeToggle";

type SignupFormInput = z.input<typeof signupSchema>;

export default function SignupPage() {
  const router = useRouter();
  const { setUser } = useAuth();
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSuccess, setIsSuccess] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignupFormInput>({
    resolver: zodResolver(signupSchema) as never,
    defaultValues: { email: "", password: "", organizationName: "" },
  });

  const onSubmit = async (data: SignupFormInput) => {
    setAuthError(null);
    const result = await apiRegister(data.email, data.password, data.organizationName || undefined);
    if (!result.success || !result.user) {
      setAuthError(result.error ?? "Could not create account. Please try again.");
      return;
    }
    setUser(result.user);
    setIsSuccess(true);
    setTimeout(() => router.push("/chat"), 600);
  };

  return (
    <main
      className="relative min-h-screen w-full flex items-center justify-center px-5 py-10 transition-colors duration-250"
      style={{ color: "var(--text-main)" }}
    >
      <div className="absolute top-5 right-5 z-50">
        <ThemeToggle showLabel />
      </div>

      <div className="w-full max-w-md space-y-5">
        <div className="text-center space-y-1.5">
          <h2 className="text-2xl sm:text-3xl font-black tracking-tight" style={{ color: "var(--text-main)" }}>
            Create your workspace
          </h2>
          <p className="text-xs sm:text-sm font-medium" style={{ color: "var(--text-muted)" }}>
            Give it an organization name to start a brand-new tenant, or leave it blank to join the shared default workspace.
          </p>
        </div>

        <div className="rounded-2xl decent-card glow-card p-6 sm:p-7 space-y-5 fade-in-up">
          {authError && (
            <div className="flex items-start gap-2.5 p-3.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 scale-in">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <p className="text-xs sm:text-sm font-medium">{authError}</p>
            </div>
          )}
          {isSuccess && (
            <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 scale-in">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <p className="text-xs sm:text-sm font-medium">Account created. Opening workspace…</p>
            </div>
          )}

          <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4 m-stagger">
            <div>
              <label htmlFor="email" className="block text-xs font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>
                <Mail className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" /> Email address
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                disabled={isSubmitting || isSuccess}
                className="w-full px-3.5 py-2.5 rounded-xl text-sm outline-none disabled:opacity-50"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                {...register("email")}
              />
              {errors.email && <p className="text-xs text-red-500 mt-1 font-medium">{errors.email.message}</p>}
            </div>

            <div>
              <label htmlFor="password" className="block text-xs font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>
                <Lock className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" /> Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                disabled={isSubmitting || isSuccess}
                className="w-full px-3.5 py-2.5 rounded-xl text-sm outline-none disabled:opacity-50"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                {...register("password")}
              />
              {errors.password && <p className="text-xs text-red-500 mt-1 font-medium">{errors.password.message}</p>}
            </div>

            <div>
              <label htmlFor="organizationName" className="block text-xs font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>
                <Building2 className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" /> Organization name (optional)
              </label>
              <input
                id="organizationName"
                type="text"
                placeholder="Leave blank to join the default workspace"
                disabled={isSubmitting || isSuccess}
                className="w-full px-3.5 py-2.5 rounded-xl text-sm outline-none disabled:opacity-50 placeholder:text-slate-400"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                {...register("organizationName")}
              />
              <p className="text-[11px] mt-1" style={{ color: "var(--text-subtle)" }}>
                Creating an organization makes you its first member — your documents, datasets, and research stay isolated from other organizations.
              </p>
            </div>

            <button
              type="submit"
              disabled={isSubmitting || isSuccess}
              className={`m-press w-full py-3 rounded-xl font-semibold text-sm shadow-sm transition-all duration-150 ${
                isSubmitting || isSuccess ? "opacity-75 cursor-not-allowed" : "hover:opacity-95 active:scale-[0.99]"
              }`}
              style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}
            >
              <span className="flex items-center justify-center gap-2">
                {isSubmitting ? "Creating account…" : isSuccess ? "Success" : (<>Create account <ArrowRight className="w-4 h-4" /></>)}
              </span>
            </button>
          </form>

          <div className="flex items-center gap-3 pt-1">
            <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
            <span className="text-[11px] font-medium" style={{ color: "var(--text-subtle)" }}>Already have an account?</span>
            <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
          </div>

          <a
            href="/login"
            className="flex items-center justify-center w-full py-2.5 rounded-xl text-sm font-medium transition-all duration-150 hover:bg-slate-500/5"
            style={{ border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
          >
            Sign in instead
          </a>
        </div>
      </div>
    </main>
  );
}
