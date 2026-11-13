"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import ThemeToggle from "@/components/ThemeToggle";
import {
  ShieldAlert,
  Lock,
  ArrowLeft,
  RefreshCw,
  AlertTriangle,
  Search,
  Building2,
  UserCog,
} from "lucide-react";
import Link from "next/link";

interface Violation {
  id: number;
  timestamp: string;
  user_id?: string | null;
  query?: string | null;
  violation_type?: string | null;
  action_taken?: string | null;
  message?: string | null;
}

interface TenantInfo {
  tenant_id: string;
  name: string;
}

export default function SecurityCenterPage() {
  const { user, isLoading } = useAuth();
  const [tenant, setTenant] = useState<TenantInfo | null>(null);
  const [violations, setViolations] = useState<Violation[]>([]);
  const [fetching, setFetching] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setFetching(true);
    setFetchError(null);
    try {
      const [tenantRes, violationsRes] = await Promise.all([
        fetch("/api/auth/tenant"),
        fetch("/api/security/violations"),
      ]);
      if (tenantRes.ok) setTenant(await tenantRes.json());
      if (violationsRes.ok) {
        setViolations(await violationsRes.json());
      } else if (violationsRes.status !== 403) {
        setFetchError("Could not load the security audit log.");
      }
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <RefreshCw className="w-6 h-6 animate-spin" style={{ color: "var(--primary-accent)" }} />
      </div>
    );
  }

  if (!user || user.role !== "admin") {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <div className="max-w-md w-full rounded-2xl p-6 text-center" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <div className="w-12 h-12 bg-rose-500/10 rounded-full flex items-center justify-center mx-auto mb-4 border border-rose-500/20">
            <Lock className="w-6 h-6 text-rose-400" />
          </div>
          <h2 className="text-xl font-bold mb-2">Admin Access Required</h2>
          <p className="text-sm mb-6" style={{ color: "var(--text-muted)" }}>
            The Security Center is restricted to administrator accounts within your own organization.
          </p>
          <Link href="/chat" className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
            <ArrowLeft className="w-4 h-4" /> Return to Chat
          </Link>
        </div>
      </div>
    );
  }

  const filtered = violations.filter(
    (v) =>
      !search ||
      (v.query || "").toLowerCase().includes(search.toLowerCase()) ||
      (v.violation_type || "").toLowerCase().includes(search.toLowerCase()) ||
      (v.user_id || "").toLowerCase().includes(search.toLowerCase())
  );

  const byType: Record<string, number> = {};
  for (const v of violations) {
    const t = v.violation_type || "unknown";
    byType[t] = (byType[t] || 0) + 1;
  }

  return (
    <div className="min-h-screen p-6 md:p-10" style={{ color: "var(--text-main)" }}>
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6" style={{ borderColor: "var(--border-subtle)" }}>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
              <ShieldAlert className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold">Security Center</h1>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                Guardrail audit log for {tenant ? tenant.name : "your organization"} — admin is scoped to this tenant only.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={load} disabled={fetching} className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
              <RefreshCw className={`w-3.5 h-3.5 ${fetching ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            <ThemeToggle />
            <Link href="/workspace" className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
              <Building2 className="w-3.5 h-3.5" /> Workspace
            </Link>
            <Link href="/chat" className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
              <ArrowLeft className="w-4 h-4" /> Back to Chat
            </Link>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <div className="flex justify-between items-start mb-2">
              <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Total Violations</span>
              <AlertTriangle className="w-4 h-4 text-amber-500" />
            </div>
            <div className="text-3xl font-bold">{violations.length}</div>
          </div>
          <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <div className="flex justify-between items-start mb-2">
              <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Your Role</span>
              <UserCog className="w-4 h-4" style={{ color: "var(--primary-accent)" }} />
            </div>
            <div className="text-lg font-bold capitalize">{user.role} (this tenant)</div>
          </div>
          {Object.entries(byType).slice(0, 2).map(([type, count]) => (
            <div key={type} className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <div className="flex justify-between items-start mb-2">
                <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{type.replace(/_/g, " ")}</span>
                <ShieldAlert className="w-4 h-4 text-rose-500" />
              </div>
              <div className="text-3xl font-bold">{count}</div>
            </div>
          ))}
        </div>

        <div className="rounded-2xl p-6 space-y-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <h3 className="text-base font-bold flex items-center gap-2"><ShieldAlert className="w-4 h-4" /> Guardrail Audit Log</h3>
            <div className="relative w-full sm:w-64">
              <Search className="w-3.5 h-3.5 absolute left-3 top-3" style={{ color: "var(--text-subtle)" }} />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search query, type, or user…"
                className="w-full pl-9 pr-3 py-1.5 rounded-xl text-xs outline-none"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
              />
            </div>
          </div>

          {fetchError && <p className="text-xs text-red-500">{fetchError}</p>}

          {filtered.length === 0 ? (
            <p className="text-xs italic py-6 text-center" style={{ color: "var(--text-subtle)" }}>
              No guardrail violations recorded for this tenant yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b text-[11px] font-semibold uppercase tracking-wider" style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}>
                    <th className="py-2.5 px-3">Time</th>
                    <th className="py-2.5 px-3">User</th>
                    <th className="py-2.5 px-3">Query</th>
                    <th className="py-2.5 px-3">Type</th>
                    <th className="py-2.5 px-3">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y text-xs" style={{ color: "var(--text-main)" }}>
                  {filtered.map((v) => (
                    <tr key={v.id}>
                      <td className="py-2.5 px-3 font-mono text-[11px]" style={{ color: "var(--text-subtle)" }}>{v.timestamp}</td>
                      <td className="py-2.5 px-3 font-mono text-[11px]">{v.user_id}</td>
                      <td className="py-2.5 px-3 max-w-xs truncate">{v.query}</td>
                      <td className="py-2.5 px-3">
                        <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                          {v.violation_type}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-rose-500 font-semibold">{v.action_taken}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
