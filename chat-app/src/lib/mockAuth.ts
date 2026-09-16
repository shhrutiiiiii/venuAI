import { z } from "zod";

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
  name: string;
  email: string;
  avatar: string;
  role: "admin" | "user";
  token: string;
}

export interface AuthResponse {
  success: boolean;
  user?: AuthUser;
  error?: string;
}

// ─── Mock Database ───────────────────────────────────────────────────────────

const MOCK_USERS: Array<{ email: string; password: string; user: AuthUser }> = [
  {
    email: "demo@chat.com",
    password: "123456",
    user: {
      id: "usr_demo_001",
      name: "Demo User",
      email: "demo@chat.com",
      avatar: "DU",
      role: "user",
      token: "mock_jwt_token_demo_usr_001_" + Date.now(),
    },
  },
  {
    email: "admin@chat.com",
    password: "admin123",
    user: {
      id: "usr_admin_001",
      name: "Admin User",
      email: "admin@chat.com",
      avatar: "AU",
      role: "admin",
      token: "mock_jwt_token_admin_usr_001_" + Date.now(),
    },
  },
];

// ─── Session Storage Keys ────────────────────────────────────────────────────

export const SESSION_KEY = "chat_session";
export const REMEMBER_KEY = "chat_remember";

// ─── Mock Auth API ───────────────────────────────────────────────────────────

/**
 * Simulates a secure API login request with realistic network delay.
 */
export async function mockLogin(
  email: string,
  password: string,
  rememberMe = false
): Promise<AuthResponse> {
  // Simulate network latency: 800ms – 1000ms
  const delay = 800 + Math.random() * 200;
  await new Promise((resolve) => setTimeout(resolve, delay));

  const found = MOCK_USERS.find(
    (u) =>
      u.email.toLowerCase() === email.toLowerCase() &&
      u.password === password
  );

  if (!found) {
    return {
      success: false,
      error: "Invalid email or password. Please try again.",
    };
  }

  // Refresh token per-login
  const sessionUser: AuthUser = {
    ...found.user,
    token: "mock_jwt_" + Math.random().toString(36).slice(2),
  };

  // Persist session
  const storage = rememberMe ? localStorage : sessionStorage;
  storage.setItem(SESSION_KEY, JSON.stringify(sessionUser));
  if (rememberMe) {
    localStorage.setItem(REMEMBER_KEY, "true");
  }

  return { success: true, user: sessionUser };
}

/**
 * Clears the session from both storages.
 */
export function mockLogout(): void {
  sessionStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(REMEMBER_KEY);
}

/**
 * Retrieves the current session user, if any.
 */
export function getSessionUser(): AuthUser | null {
  if (typeof window === "undefined") return null;

  const raw =
    localStorage.getItem(SESSION_KEY) ||
    sessionStorage.getItem(SESSION_KEY);

  if (!raw) return null;

  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}
