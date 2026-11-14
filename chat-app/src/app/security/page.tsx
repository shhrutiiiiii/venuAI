"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { ShieldAlert, AlertTriangle, Search, UserCog } from "lucide-react";
import PageShell from "@/components/pages/PageShell";
import { AdminOnly, EmptyState, ErrorState, PageSpinner, SkeletonRows, SkeletonTiles, StatTile, CountUp } from "@/components/pages/primitives";

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
        setFetchError("The security audit log did not respond. Check that the backend is running.");
      }
    } catch {
      setFetchError("The security audit log is unreachable. Check that the backend is running.");
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  if (isLoading) return <PageSpinner />;
  if (!user || user.role !== "admin") return <AdminOnly what="The Security Center" role={user?.role} />;

  const q = search.toLowerCase();
  const filtered = violations.filter(
    (v) =>
      !search ||
      (v.query || "").toLowerCase().includes(q) ||
      (v.violation_type || "").toLowerCase().includes(q) ||
      (v.user_id || "").toLowerCase().includes(q)
  );

  const byType: Record<string, number> = {};
  for (const v of violations) {
    const t = v.violation_type || "unknown";
    byType[t] = (byType[t] || 0) + 1;
  }
  const topTypes = Object.entries(byType).sort((a, b) => b[1] - a[1]).slice(0, 2);
  const initial = fetching && violations.length === 0 && !fetchError;

  return (
    <PageShell
      current="security"
      title="Security center"
      icon={ShieldAlert}
      isAdmin
      onRefresh={load}
      refreshing={fetching}
      subtitle={`Guardrail audit log for ${tenant ? tenant.name : "your organization"}. Admin access is scoped to this organization only.`}
    >
      {fetchError && <ErrorState message={fetchError} onRetry={load} />}

      {initial ? (
        <SkeletonTiles count={3} />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatTile index={0} icon={AlertTriangle} label="Total violations"><CountUp value={violations.length} /></StatTile>
          <StatTile index={1} icon={UserCog} label="Your role"><span className="capitalize">{user.role}</span></StatTile>
          {topTypes.map(([type, count], i) => (
            <StatTile key={type} index={i + 2} icon={ShieldAlert} label={type.replace(/_/g, " ")}><CountUp value={count} /></StatTile>
          ))}
        </div>
      )}

      <section className="p-surface p-4 sm:p-6 space-y-4 min-w-0" aria-label="Guardrail audit log">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <h2 className="text-base font-bold">Guardrail audit log</h2>
          <div className="relative w-full sm:w-72">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-subtle)" }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search query, type, or user"
              aria-label="Search the audit log"
              className="p-field w-full pl-9 pr-3 py-2 text-xs"
            />
          </div>
        </div>

        {initial ? (
          <SkeletonRows count={4} />
        ) : filtered.length === 0 ? (
          <EmptyState
            title={search ? "No matches" : "No violations recorded"}
            text={search ? "Nothing in the log matches that search. Clear it to see every entry." : "No guardrail violations have been recorded for this organization yet."}
          />
        ) : (
          <div className="overflow-x-auto -mx-1">
            <table className="w-full border-collapse min-w-[640px]">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--border-subtle)" }}>
                  <th className="p-th">Time</th>
                  <th className="p-th">User</th>
                  <th className="p-th">Query</th>
                  <th className="p-th">Type</th>
                  <th className="p-th">Action</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((v, i) => (
                  <tr key={v.id} className="p-row border-b p-rise" style={{ borderColor: "var(--border-subtle)", animationDelay: `${Math.min(i, 12) * 30}ms` }}>
                    <td className="p-td font-mono text-[11px] whitespace-nowrap" style={{ color: "var(--text-subtle)" }}>{v.timestamp}</td>
                    <td className="p-td font-mono text-[11px]">{v.user_id}</td>
                    <td className="p-td max-w-xs truncate" title={v.query || undefined}>{v.query}</td>
                    <td className="p-td">
                      <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                        {v.violation_type}
                      </span>
                    </td>
                    <td className="p-td font-semibold" style={{ color: "var(--p-bad)" }}>{v.action_taken}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </PageShell>
  );
}
