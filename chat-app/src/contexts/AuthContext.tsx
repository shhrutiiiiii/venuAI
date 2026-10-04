"use client";

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  ReactNode,
} from "react";
import { AuthUser, getSessionUser, logout as apiLogout } from "@/lib/authClient";

// ─── Types ───────────────────────────────────────────────────────────────────

interface AuthContextValue {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  setUser: (user: AuthUser | null) => void;
  logout: () => void;
}

// ─── Context ─────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

// ─── Provider ────────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Hydrate session on mount by asking the backend (via the httpOnly-cookie
  // session) who the current user is — the token itself is never readable
  // from client-side JS.
  useEffect(() => {
    let mounted = true;
    getSessionUser().then((sessionUser) => {
      if (mounted) {
        setUserState(sessionUser);
        setIsLoading(false);
      }
    });
    return () => {
      mounted = false;
    };
  }, []);

  const setUser = useCallback((newUser: AuthUser | null) => {
    setUserState(newUser);
  }, []);

  const logout = useCallback(() => {
    apiLogout();
    setUserState(null);
  }, []);

  const value: AuthContextValue = {
    user,
    isLoading,
    isAuthenticated: !!user,
    setUser,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ─── Hook ────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider />");
  }
  return ctx;
}
