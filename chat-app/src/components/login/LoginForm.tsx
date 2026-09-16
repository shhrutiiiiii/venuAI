"use client";

import { useState, useCallback } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import {
  Mail,
  Lock,
  Eye,
  EyeOff,
  ArrowRight,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";

import { z } from "zod";
import { loginSchema, mockLogin } from "@/lib/mockAuth";

type LoginFormInput = z.input<typeof loginSchema>;
type LoginFormData = z.output<typeof loginSchema>;
import { useAuth } from "@/contexts/AuthContext";
import DemoCredentialsBadge from "./DemoCredentialsBadge";

// ─── Floating Label Input ─────────────────────────────────────────────────────

interface FloatingInputProps {
  id: string;
  label: string;
  type?: string;
  icon: React.ReactNode;
  error?: string;
  disabled?: boolean;
  autoComplete?: string;
  register: ReturnType<typeof useForm<LoginFormData>>["register"];
  name: keyof LoginFormData;
  rightElement?: React.ReactNode;
}

function FloatingInput({
  id,
  label,
  type = "text",
  icon,
  error,
  disabled,
  autoComplete,
  register,
  name,
  rightElement,
}: FloatingInputProps) {
  const [isFocused, setIsFocused] = useState(false);
  const [hasValue, setHasValue] = useState(false);

  return (
    <div className="relative">
      <div
        className="relative rounded-xl transition-all duration-150 input-glow"
        style={{
          background: "var(--bg-input)",
          border: error
            ? "1px solid #ef4444"
            : isFocused
            ? "1px solid var(--border-focus)"
            : "1px solid var(--border-subtle)",
        }}
      >
        {/* Leading icon */}
        <div
          className={`absolute left-3.5 top-1/2 -translate-y-1/2 transition-colors duration-150 ${
            error
              ? "text-red-500"
              : isFocused
              ? "text-indigo-600 dark:text-indigo-400"
              : "text-slate-400"
          }`}
        >
          {icon}
        </div>

        {/* Floating label */}
        <label
          htmlFor={id}
          className={`absolute left-10 transition-all duration-150 pointer-events-none font-medium ${
            isFocused || hasValue
              ? "top-1.5 text-[10px] uppercase tracking-wider " +
                (error
                  ? "text-red-500"
                  : isFocused
                  ? "text-indigo-600 dark:text-indigo-400"
                  : "text-slate-400")
              : "top-1/2 -translate-y-1/2 text-sm text-slate-400"
          }`}
        >
          {label}
        </label>

        {/* Input */}
        <input
          id={id}
          type={type}
          autoComplete={autoComplete}
          disabled={disabled}
          className={`w-full bg-transparent pt-4.5 pb-1.5 pl-10 text-sm outline-none transition-colors duration-150 placeholder:text-transparent ${
            rightElement ? "pr-10" : "pr-3"
          } disabled:opacity-50 disabled:cursor-not-allowed`}
          style={{ borderRadius: "inherit", color: "var(--text-main)" }}
          {...register(name, {
            onChange: (e) => setHasValue(e.target.value.length > 0),
          })}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
        />

        {/* Right element */}
        {rightElement && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            {rightElement}
          </div>
        )}
      </div>

      {/* Error message */}
      {error && (
        <div className="flex items-center gap-1.5 mt-1.5 px-1 fade-in-up">
          <AlertCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
          <p className="text-xs text-red-500 font-medium">{error}</p>
        </div>
      )}
    </div>
  );
}

// ─── Main Login Form ──────────────────────────────────────────────────────────

export default function LoginForm() {
  const router = useRouter();
  const { setUser } = useAuth();
  const [showPassword, setShowPassword] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSuccess, setIsSuccess] = useState(false);

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormInput>({
    resolver: zodResolver(loginSchema) as never,
    defaultValues: { email: "", password: "", rememberMe: false },
  });

  const handleDemoCredentials = useCallback(
    (email: string, password: string) => {
      setValue("email", email, { shouldValidate: true, shouldDirty: true });
      setValue("password", password, { shouldValidate: true, shouldDirty: true });
      setTimeout(() => {
        handleSubmit(onSubmit)();
      }, 300);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [setValue, handleSubmit]
  );

  const onSubmit = async (data: LoginFormInput) => {
    setAuthError(null);
    try {
      const result = await mockLogin(data.email, data.password, data.rememberMe);

      if (!result.success || !result.user) {
        setAuthError(result.error ?? "Authentication failed. Please check credentials.");
        return;
      }

      setUser(result.user);
      setIsSuccess(true);

      setTimeout(() => {
        router.push("/chat");
      }, 600);
    } catch {
      setAuthError("An unexpected error occurred. Please try again.");
    }
  };

  return (
    <div className="w-full max-w-md mx-auto space-y-4">
      {/* Demo credentials badge */}
      <DemoCredentialsBadge onUseCredentials={handleDemoCredentials} />

      {/* Form card */}
      <div className="rounded-2xl decent-card glow-card p-6 sm:p-7 space-y-5 fade-in-up">
        {/* Global error */}
        {authError && (
          <div className="flex items-start gap-2.5 p-3.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 scale-in">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <p className="text-xs sm:text-sm font-medium">{authError}</p>
          </div>
        )}

        {/* Success state */}
        {isSuccess && (
          <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 scale-in">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <p className="text-xs sm:text-sm font-medium">
              Signed in successfully. Opening workspace…
            </p>
          </div>
        )}

        <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
          {/* Email field */}
          <FloatingInput
            id="email"
            name="email"
            label="Email address"
            type="email"
            autoComplete="email"
            icon={<Mail className="w-4 h-4" />}
            error={errors.email?.message}
            disabled={isSubmitting || isSuccess}
            register={register}
          />

          {/* Password field */}
          <FloatingInput
            id="password"
            name="password"
            label="Password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            icon={<Lock className="w-4 h-4" />}
            error={errors.password?.message}
            disabled={isSubmitting || isSuccess}
            register={register}
            rightElement={
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="p-1 rounded text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors"
                tabIndex={-1}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? (
                  <EyeOff className="w-4 h-4" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
              </button>
            }
          />

          {/* Remember me + Forgot password */}
          <div className="flex items-center justify-between pt-0.5">
            <label
              htmlFor="rememberMe"
              className="flex items-center gap-2 cursor-pointer group"
            >
              <input
                id="rememberMe"
                type="checkbox"
                className="w-4 h-4 rounded border-slate-300 accent-indigo-600 cursor-pointer"
                {...register("rememberMe")}
                disabled={isSubmitting || isSuccess}
              />
              <span className="text-xs sm:text-sm font-medium select-none" style={{ color: "var(--text-muted)" }}>
                Remember me
              </span>
            </label>

            <a
              href="/forgot-password"
              className="text-xs sm:text-sm font-medium hover:underline transition-colors"
              style={{ color: "var(--primary-accent)" }}
              tabIndex={isSubmitting ? -1 : 0}
            >
              Forgot password?
            </a>
          </div>

          {/* Submit button */}
          <button
            id="login-submit-btn"
            type="submit"
            disabled={isSubmitting || isSuccess}
            className={`w-full py-3 rounded-xl font-semibold text-sm shadow-sm transition-all duration-150 ${
              isSubmitting || isSuccess
                ? "opacity-75 cursor-not-allowed"
                : "hover:opacity-95 active:scale-[0.99]"
            }`}
            style={{
              background: "var(--primary-gradient)",
              color: "var(--btn-text)",
            }}
          >
            <span className="flex items-center justify-center gap-2">
              {isSubmitting ? (
                <>
                  <svg
                    className="w-4 h-4 spin-smooth"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeOpacity="0.3"
                    />
                    <path
                      d="M12 2a10 10 0 0 1 10 10"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                    />
                  </svg>
                  Signing in…
                </>
              ) : isSuccess ? (
                <>
                  <CheckCircle2 className="w-4 h-4" />
                  Success
                </>
              ) : (
                <>
                  Sign In
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </span>
          </button>
          {/* Social Sign-In Buttons */}
          <div className="space-y-2 pt-1">
            <div className="flex items-center gap-3 py-1">
              <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
              <span className="text-[11px] font-medium" style={{ color: "var(--text-subtle)" }}>
                OR CONTINUE WITH
              </span>
              <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => handleDemoCredentials("demo@chat.com", "123456")}
                className="flex items-center justify-center gap-2 py-2.5 px-3 rounded-xl text-xs font-semibold border transition-all duration-150 hover:bg-slate-500/5 active:scale-[0.98]"
                style={{
                  background: "var(--bg-input)",
                  borderColor: "var(--border-subtle)",
                  color: "var(--text-main)",
                }}
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24">
                  <path
                    fill="#4285F4"
                    d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                  />
                  <path
                    fill="#34A853"
                    d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  />
                  <path
                    fill="#FBBC05"
                    d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                  />
                  <path
                    fill="#EA4335"
                    d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                  />
                </svg>
                <span>Google</span>
              </button>

              <button
                type="button"
                onClick={() => handleDemoCredentials("admin@chat.com", "admin123")}
                className="flex items-center justify-center gap-2 py-2.5 px-3 rounded-xl text-xs font-semibold border transition-all duration-150 hover:bg-slate-500/5 active:scale-[0.98]"
                style={{
                  background: "var(--bg-input)",
                  borderColor: "var(--border-subtle)",
                  color: "var(--text-main)",
                }}
              >
                <svg className="w-4 h-4 fill-current" viewBox="0 0 24 24">
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                </svg>
                <span>GitHub</span>
              </button>
            </div>
          </div>
        </form>

        {/* Divider */}
        <div className="flex items-center gap-3 pt-2">
          <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
          <span className="text-[11px] font-medium" style={{ color: "var(--text-subtle)" }}>
            Don&rsquo;t have an account?
          </span>
          <div className="flex-1 h-px" style={{ background: "var(--border-subtle)" }} />
        </div>

        {/* Sign up link */}
        <a
          href="/signup"
          id="signup-link"
          className="flex items-center justify-center gap-1.5 w-full py-2.5 rounded-xl text-sm font-medium transition-all duration-150 hover:bg-slate-500/5"
          style={{
            border: "1px solid var(--border-subtle)",
            color: "var(--text-main)",
          }}
        >
          Create account
        </a>
      </div>
    </div>
  );
}



