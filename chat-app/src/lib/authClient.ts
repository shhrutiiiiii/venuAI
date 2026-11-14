import { z } from "zod";
import { CSRF_HEADER, CSRF_VALUE } from "@/lib/csrf";

/** Explicit CSRF header on auth calls (also added globally by installCsrfFetch). */
const JSON_HEADERS = { "Content-Type": "application/json", [CSRF_HEADER]: CSRF_VALUE };

// ─── Zod Schemas ────────────────────────────────────────────────────────────

export const loginSchema = z.object({
  email: z
    .string()
    .min(1, "Email is required")
    .email("Please enter a valid email address"),
  password: z
    .string()
    .min(1, "Password is required")
    .min(6, "Password must be at least 6 characters"),
  rememberMe: z.boolean(),
});

export type LoginFormData = z.infer<typeof loginSchema>;

// ─── Types ───────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string;
  email: string;
  role: "admin" | "user";
  tenant_id?: string;
}

export interface AuthResponse {
  success: boolean;
  user?: AuthUser;
  error?: string;
}

/**
 * Real authentication client. Calls the Next.js proxy routes under
 * /api/auth/*, which forward to the FastAPI backend's real auth endpoints
 * (VENUAI/auth.py) and store tokens in httpOnly cookies set server-side —
 * never in localStorage/sessionStorage, which is readable by any script on
 * the page. See Documents/Backend/authentication.md and
 * Documents/Frontend/state-management.md.
 */
export async function login(
  email: string,
  password: string
): Promise<AuthResponse> {
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      return { success: false, error: typeof data.detail === "string" ? data.detail : "Invalid email or password. Please try again." };
    }
    return { success: true, user: data.user as AuthUser };
  } catch {
    return { success: false, error: "Could not reach the server. Please try again." };
  }
}

export const signupSchema = z.object({
  email: z.string().min(1, "Email is required").email("Please enter a valid email address"),
  password: z.string().min(1, "Password is required").min(12, "Password must be at least 12 characters"),
  organizationName: z.string().optional(),
});

export type SignupFormData = z.infer<typeof signupSchema>;

/**
 * Real registration — POST /auth/register (via /api/auth/register), then an
 * immediate login to establish the session, since the register endpoint
 * itself only returns the created UserOut, not tokens. Passing
 * organization_name creates a brand-new tenant and makes this user its
 * first member (Phase 4 multi-tenancy — see
 * Documents/Backend/multi-tenancy.md); leaving it blank gets a personal
 * workspace; joining an existing tenant needs an invite (backend-enforced).
 */
export async function register(
  email: string,
  password: string,
  organizationName?: string
): Promise<AuthResponse> {
  try {
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        email,
        password,
        ...(organizationName ? { organization_name: organizationName } : {}),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      return { success: false, error: typeof data.detail === "string" ? data.detail : "Could not create account. Please try again." };
    }
    return login(email, password);
  } catch {
    return { success: false, error: "Could not reach the server. Please try again." };
  }
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", headers: { [CSRF_HEADER]: CSRF_VALUE } }).catch(() => {});
}

export async function getSessionUser(): Promise<AuthUser | null> {
  if (typeof window === "undefined") return null;
  try {
    const res = await fetch("/api/auth/me", { method: "GET" });
    if (!res.ok) return null;
    return (await res.json()) as AuthUser;
  } catch {
    return null;
  }
}
