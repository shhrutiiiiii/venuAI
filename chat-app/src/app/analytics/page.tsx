"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { BarChart3, Activity, CheckCircle2, AlertTriangle, Clock, Search, Gauge } from "lucide-react";
import PageShell from "@/components/pages/PageShell";
import {
  AdminOnly, CountUp, EmptyState, ErrorState, Meter, PageSpinner, SkeletonRows, SkeletonTiles, StatTile,
} from "@/components/pages/primitives";

/** Shape returned by GET /analytics/dashboard (VENUAI/analytics.py). Every field may be absent on a fresh install. */
interface DayPoint { date: string; value: number | null; count: number }
interface Dashboard {
  total_queries?: { today: number; week: number; all_time: number };
  avg_latency_ms?: number;
  error_rate_pct?: number;
  grounding_trend?: DayPoint[];
  hallucination_trend?: DayPoint[];
  queries_by_domain?: Record<string, number>;
  top_topics?: { topic: string; count: number }[];
  recent_queries?: {
    question: string;
    latency_ms: number | null;
    grounding_score: number | null;
    hallucination_score: number | null;
    agent_used: string | null;
    timestamp: string;
    error: string | null;
  }[];
  ops?: {
    window_days?: number;
    llm_calls?: number;
    tokens?: { prompt: number; completion: number };
    latency_ms?: { p50: number | null; p95: number | null };
    cache_hit_rate?: number | null;
    escalation_rate?: number | null;
    cost_usd?: number | null;
  };
  online_eval?: { enabled?: boolean; n?: number; mean_score?: number | null; online_eval_regression?: boolean };
}

const pct = (v: number | null | undefined) => (v == null ? null : v * 100);

export default function AnalyticsPage() {
  const { user, isLoading } = useAuth();
  const [data, setData] = useState<Dashboard | null>(null);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");

  const load = useCallback(async () => {
    setFetching(true);
    setError(null);
    try {
      const res = await fetch("/api/rag/analytics");
      if (!res.ok) {
        setError(res.status === 403 ? "Your account is not allowed to view analytics." : `The analytics service returned an error (${res.status}).`);
        return;
      }
      setData(await res.json());
    } catch {
      setError("The analytics service is unreachable. Check that the backend is running.");
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  if (isLoading) return <PageSpinner />;
  if (!user || user.role !== "admin") return <AdminOnly what="Analytics" role={user?.role} />;

  const initial = fetching && !data && !error;
  const total = data?.total_queries;
  const hasQueries = (total?.all_time ?? 0) > 0;
  const ops = data?.ops;
  const term = searchTerm.toLowerCase();
  const recent = (data?.recent_queries ?? []).filter(
    (q) => q.question.toLowerCase().includes(term) || (q.agent_used || "").toLowerCase().includes(term)
  );
  const domains = Object.entries(data?.queries_by_domain ?? {}).sort((a, b) => b[1] - a[1]);
  const maxDomain = domains.length ? domains[0][1] : 0;
  const topics = data?.top_topics ?? [];
  const maxTopic = topics.length ? Math.max(...topics.map((t) => t.count)) : 0;

  return (
    <PageShell
      current="analytics"
      title="Analytics"
      icon={BarChart3}
      isAdmin
      wide
      onRefresh={load}
      refreshing={fetching}
      subtitle="Query volume, answer quality and model usage recorded by this server."
    >
      {error && <ErrorState message={error} onRetry={load} />}

      {initial ? (
        <SkeletonTiles count={4} />
      ) : data && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatTile index={0} icon={Activity} label="Queries today" hint={`${total?.week ?? 0} this week, ${total?.all_time ?? 0} all time`}>
              <CountUp value={total?.today ?? 0} />
            </StatTile>
            <StatTile index={1} icon={Clock} label="Average latency" hint={ops?.latency_ms?.p95 != null ? `p50 ${ops.latency_ms.p50} ms, p95 ${ops.latency_ms.p95} ms (last ${ops.window_days ?? 7} days)` : "Successful queries only"}>
              {hasQueries ? <CountUp value={data.avg_latency_ms ?? 0} suffix=" ms" /> : "No data"}
            </StatTile>
            <StatTile index={2} icon={AlertTriangle} label="Error rate" hint="Share of queries that failed">
              {hasQueries ? <CountUp value={data.error_rate_pct ?? 0} decimals={1} suffix="%" /> : "No data"}
            </StatTile>
            <StatTile
              index={3}
              icon={CheckCircle2}
              label="Online eval score"
              hint={data.online_eval?.enabled === false ? "Online evaluation is switched off" : `${data.online_eval?.n ?? 0} sampled answers`}
            >
              {data.online_eval?.mean_score != null ? <CountUp value={data.online_eval.mean_score * 100} decimals={0} suffix="%" /> : "No data"}
            </StatTile>
          </div>

          {ops && (ops.llm_calls ?? 0) > 0 && (
            <div className="p-surface p-rise p-4 sm:p-5 grid grid-cols-2 md:grid-cols-5 gap-4" aria-label={`Model usage, last ${ops.window_days ?? 7} days`}>
              <Fact label="Model calls" value={(ops.llm_calls ?? 0).toLocaleString()} />
              <Fact label="Tokens in / out" value={`${(ops.tokens?.prompt ?? 0).toLocaleString()} / ${(ops.tokens?.completion ?? 0).toLocaleString()}`} />
              <Fact label="Cache hit rate" value={ops.cache_hit_rate != null ? `${Math.round(ops.cache_hit_rate * 100)}%` : "n/a"} />
              <Fact label="Escalation rate" value={ops.escalation_rate != null ? `${Math.round(ops.escalation_rate * 100)}%` : "n/a"} />
              <Fact label="Estimated cost" value={ops.cost_usd != null ? `$${ops.cost_usd.toFixed(4)}` : "Not priced"} />
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <TrendCard title="Grounding score by day" series={data.grounding_trend ?? []} good="high" />
            <TrendCard title="Hallucination score by day" series={data.hallucination_trend ?? []} good="low" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <section className="p-surface p-4 sm:p-6 min-w-0">
              <h2 className="text-base font-bold mb-4">Queries by agent</h2>
              {domains.length === 0 ? (
                <EmptyState text="No queries have been recorded yet." />
              ) : (
                <ul className="space-y-3">
                  {domains.map(([name, count], i) => (
                    <li key={name} className="space-y-1">
                      <div className="flex justify-between gap-3 text-xs font-medium">
                        <span className="truncate">{name}</span>
                        <span className="font-mono tabular-nums" style={{ color: "var(--text-muted)" }}>{count}</span>
                      </div>
                      <Meter label={`${name}: ${count} queries`} pct={maxDomain ? (count / maxDomain) * 100 : 0} index={i} />
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="p-surface p-4 sm:p-6 min-w-0">
              <h2 className="text-base font-bold mb-4">Common topics</h2>
              {topics.length === 0 ? (
                <EmptyState text="Topics appear once people start asking questions." />
              ) : (
                <ul className="space-y-3">
                  {topics.slice(0, 8).map((t, i) => (
                    <li key={t.topic} className="space-y-1">
                      <div className="flex justify-between gap-3 text-xs font-medium">
                        <span className="truncate">{t.topic}</span>
                        <span className="font-mono tabular-nums" style={{ color: "var(--text-muted)" }}>{t.count}</span>
                      </div>
                      <Meter label={`${t.topic}: ${t.count}`} pct={maxTopic ? (t.count / maxTopic) * 100 : 0} index={i} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <section className="p-surface p-4 sm:p-6 space-y-4 min-w-0" aria-label="Recent queries">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <h2 className="text-base font-bold">Recent queries</h2>
              <div className="relative w-full sm:w-72">
                <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-subtle)" }} />
                <input
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search by question or agent"
                  aria-label="Search recent queries"
                  className="p-field w-full pl-9 pr-3 py-2 text-xs"
                />
              </div>
            </div>

            {recent.length === 0 ? (
              <EmptyState
                title={searchTerm ? "No matches" : "No queries yet"}
                text={searchTerm ? "Nothing matches that search. Clear it to see all recent queries." : "Ask something in chat and it will show up here."}
              />
            ) : (
              <div className="overflow-x-auto -mx-1">
                <table className="w-full border-collapse min-w-[640px]">
                  <thead>
                    <tr className="border-b" style={{ borderColor: "var(--border-subtle)" }}>
                      <th className="p-th">Question</th>
                      <th className="p-th">Agent</th>
                      <th className="p-th">Grounding</th>
                      <th className="p-th">Latency</th>
                      <th className="p-th">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((row, i) => (
                      <tr key={`${row.timestamp}-${i}`} className="p-row border-b p-rise" style={{ borderColor: "var(--border-subtle)", animationDelay: `${Math.min(i, 12) * 30}ms` }}>
                        <td className="p-td max-w-xs truncate" title={row.question}>{row.question}</td>
                        <td className="p-td font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>{row.agent_used ?? "n/a"}</td>
                        <td className="p-td font-mono tabular-nums">{row.grounding_score != null ? `${Math.round(row.grounding_score * 100)}%` : "n/a"}</td>
                        <td className="p-td font-mono tabular-nums whitespace-nowrap" style={{ color: "var(--text-muted)" }}>{row.latency_ms != null ? `${Math.round(row.latency_ms)} ms` : "n/a"}</td>
                        <td className="p-td">
                          {row.error ? (
                            <span className="inline-flex items-center gap-1 font-medium" style={{ color: "var(--p-bad)" }} title={row.error}>
                              <AlertTriangle className="w-3 h-3" /> Failed
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 font-medium" style={{ color: "var(--p-ok)" }}>
                              <CheckCircle2 className="w-3 h-3" /> OK
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {initial && <SkeletonRows count={3} />}
    </PageShell>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="text-sm font-semibold font-mono tabular-nums truncate mt-0.5" title={value}>{value}</div>
    </div>
  );
}

/** Daily bars, one per recorded day. Heights are the real averaged score (0..1). */
function TrendCard({ title, series, good }: { title: string; series: DayPoint[]; good: "high" | "low" }) {
  const points = series.filter((p) => p.value != null).slice(-14);
  return (
    <section className="p-surface p-4 sm:p-6 min-w-0">
      <div className="flex items-center gap-2 mb-4">
        <Gauge className="w-4 h-4" style={{ color: "var(--text-subtle)" }} />
        <h2 className="text-base font-bold">{title}</h2>
      </div>
      {points.length === 0 ? (
        <EmptyState text="Scores will chart here once queries have been scored." />
      ) : (
        <div role="img" aria-label={`${title}: ${points.map((p) => `${p.date} ${Math.round((p.value ?? 0) * 100)}%`).join(", ")}`}>
          <div className="h-44 flex items-end gap-1.5 sm:gap-2 border-b" style={{ borderColor: "var(--border-subtle)" }}>
            {points.map((p, i) => {
              const v = Math.max(0, Math.min(1, p.value ?? 0));
              const bad = good === "high" ? v < 0.5 : v > 0.5;
              return (
                <div key={p.date} className="flex-1 min-w-0 h-full flex flex-col justify-end items-center gap-1" title={`${p.date}: ${Math.round(v * 100)}% over ${p.count} queries`}>
                  <span className="text-[10px] font-mono tabular-nums" style={{ color: "var(--text-muted)" }}>{Math.round(v * 100)}</span>
                  <div className="w-full rounded-t-md p-bar-y" style={{ height: `${Math.max(v * 100 * 0.8, 1)}%`, background: bad ? "var(--p-bad)" : "var(--primary-accent)", opacity: 0.85, ["--i" as string]: i }} />
                </div>
              );
            })}
          </div>
          <div className="flex gap-1.5 sm:gap-2 mt-1.5">
            {points.map((p) => (
              <span key={p.date} className="flex-1 min-w-0 text-center text-[9px] font-mono truncate" style={{ color: "var(--text-subtle)" }}>{p.date.slice(5)}</span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
