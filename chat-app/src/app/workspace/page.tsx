"use client";

import { RaptorBuildButton } from "@/components/features/FeatureControls";
import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import ThemeToggle from "@/components/ThemeToggle";
import {
  Building2,
  FileText,
  Database,
  Network,
  Bot,
  ArrowLeft,
  RefreshCw,
  Search,
  Trash2,
  Loader2,
  ChevronRight,
  Cpu,
  Shield,
  UserPlus,
  Users,
  Pencil,
  FileSearch,
} from "lucide-react";
import Link from "next/link";
import GraphVisualization from "@/components/GraphVisualization";

type Tab = "overview" | "documents" | "datasets" | "graph" | "agents" | "team";

interface TenantInfo {
  tenant_id: string;
  name: string;
  max_users: number;
  max_documents: number;
  max_datasets: number;
}

interface TenantUsage {
  tenant_id: string;
  name: string;
  user_count: number;
  max_users: number;
  document_count: number;
  max_documents: number;
  dataset_count: number;
  max_datasets: number;
}

interface TenantMember {
  id: string;
  email: string;
  role: string;
}

interface DocumentInfo {
  id: string;
  filename: string;
  pages: number;
  chunks: number;
  upload_time: string;
  size_kb: number;
  tags?: Record<string, unknown>;
}

interface DatasetInfo {
  id: string;
  filename: string;
  columns: string[];
  row_count: number;
  size_kb: number;
  upload_time: string;
  classification: string;
}

interface EntityOut {
  entity_id: string;
  entity_type: string;
  name: string;
  classification: string;
}

interface RelationshipOut {
  relationship_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: string;
  valid_from?: string | null;
  valid_until?: string | null;
}

interface AgentOut {
  agent_id: string;
  name: string;
  description: string;
  risk_level: string;
  max_steps: number;
  enabled: boolean;
}

interface ModelOut {
  model_id: string;
  provider: string;
  capabilities: string[];
  context_window: number;
  cost_tier: string;
  enabled: boolean;
  notes: string;
}

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: "overview", label: "Overview", icon: Building2 },
  { id: "documents", label: "Documents", icon: FileText },
  { id: "datasets", label: "Datasets", icon: Database },
  { id: "graph", label: "Knowledge Graph", icon: Network },
  { id: "agents", label: "Agents & Models", icon: Bot },
  { id: "team", label: "Team & Quotas", icon: UserPlus },
];

export default function WorkspacePage() {
  const { user, isLoading } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [tenant, setTenant] = useState<TenantInfo | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [raptorOn, setRaptorOn] = useState(false);
  useEffect(() => {
    fetch("/api/features", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setRaptorOn(!!d?.features?.find((f: { key: string; enabled: boolean }) => f.key === "raptor")?.enabled))
      .catch(() => undefined);
  }, []);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [agents, setAgents] = useState<AgentOut[]>([]);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [loading, setLoading] = useState(true);

  const [graphQuery, setGraphQuery] = useState("");
  const [graphResults, setGraphResults] = useState<EntityOut[]>([]);
  const [graphSearching, setGraphSearching] = useState(false);
  const [selectedEntity, setSelectedEntity] = useState<{ entity: EntityOut; relationships: RelationshipOut[]; related_entities: EntityOut[] } | null>(null);
  const [extractText, setExtractText] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);

  const [usage, setUsage] = useState<TenantUsage | null>(null);
  const [members, setMembers] = useState<TenantMember[]>([]);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [newMemberEmail, setNewMemberEmail] = useState("");
  const [newMemberPassword, setNewMemberPassword] = useState("");
  const [addingMember, setAddingMember] = useState(false);
  const [memberError, setMemberError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [tenantRes, docsRes, datasetsRes, agentsRes, modelsRes, usageRes] = await Promise.all([
        fetch("/api/auth/tenant"),
        fetch("/api/rag/documents"),
        fetch("/api/datasets"),
        fetch("/api/agents"),
        fetch("/api/models"),
        fetch("/api/tenants/usage"),
      ]);
      if (tenantRes.ok) {
        const t = await tenantRes.json();
        setTenant(t);
        setRenameValue(t.name);
      }
      if (docsRes.ok) setDocuments(await docsRes.json());
      if (datasetsRes.ok) setDatasets(await datasetsRes.json());
      if (agentsRes.ok) setAgents(await agentsRes.json());
      if (modelsRes.ok) setModels(await modelsRes.json());
      if (usageRes.ok) setUsage(await usageRes.json());
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMembers = useCallback(async () => {
    const res = await fetch("/api/tenants/members");
    if (res.ok) setMembers(await res.json());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (user?.role === "admin") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      loadMembers();
    }
  }, [user, loadMembers]);

  const saveTenantName = async () => {
    if (!renameValue.trim()) return;
    setRenameSaving(true);
    try {
      const res = await fetch("/api/tenants", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: renameValue.trim() }),
      });
      if (res.ok) {
        const updated = await res.json();
        setTenant((prev) => (prev ? { ...prev, name: updated.name } : prev));
        setUsage((prev) => (prev ? { ...prev, name: updated.name } : prev));
      }
    } finally {
      setRenameSaving(false);
    }
  };

  const addMember = async () => {
    if (!newMemberEmail.trim() || !newMemberPassword.trim()) return;
    setAddingMember(true);
    setMemberError(null);
    try {
      const res = await fetch("/api/tenants/members", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: newMemberEmail.trim(), password: newMemberPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMemberError(data.detail || "Could not add member.");
        return;
      }
      setMembers((prev) => [...prev, data]);
      setNewMemberEmail("");
      setNewMemberPassword("");
      loadAll();
    } catch {
      setMemberError("Tenant service unavailable.");
    } finally {
      setAddingMember(false);
    }
  };

  const deleteDocument = async (id: string) => {
    const res = await fetch(`/api/rag/documents?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    if (res.ok) setDocuments((prev) => prev.filter((d) => d.id !== id));
  };

  const deleteDataset = async (id: string) => {
    const res = await fetch(`/api/datasets/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (res.ok) setDatasets((prev) => prev.filter((d) => d.id !== id));
  };

  const runGraphSearch = async () => {
    setGraphSearching(true);
    setSelectedEntity(null);
    try {
      const res = await fetch(`/api/graph/search?q=${encodeURIComponent(graphQuery)}`);
      setGraphResults(res.ok ? await res.json() : []);
    } finally {
      setGraphSearching(false);
    }
  };

  const openEntity = async (entityId: string) => {
    const res = await fetch(`/api/graph/entities/${encodeURIComponent(entityId)}`);
    if (res.ok) setSelectedEntity(await res.json());
  };

  const runExtract = async () => {
    if (!extractText.trim()) return;
    setExtracting(true);
    setExtractError(null);
    try {
      const res = await fetch("/api/graph/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: extractText, document_name: "Workspace manual entry", use_llm: true }),
      });
      const data = await res.json();
      if (!res.ok) {
        setExtractError(data.detail || "Extraction failed.");
        return;
      }
      setExtractText("");
      setGraphQuery("");
      setGraphResults(data.entities || []);
    } catch {
      setExtractError("Knowledge graph service unavailable.");
    } finally {
      setExtracting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <RefreshCw className="w-6 h-6 animate-spin" style={{ color: "var(--primary-accent)" }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen p-6 md:p-10" style={{ color: "var(--text-main)" }}>
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6" style={{ borderColor: "var(--border-subtle)" }}>
          <div>
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
                <Building2 className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-2xl font-bold">Research Workspace</h1>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {tenant ? `${tenant.name} · ${tenant.tenant_id}` : "Loading organization…"}
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={loadAll}
              disabled={loading}
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-medium transition-all"
              style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            <ThemeToggle />
            <Link href="/evidence" className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
              <FileSearch className="w-3.5 h-3.5" /> Evidence Explorer
            </Link>
            {user?.role === "admin" && (
              <Link href="/security" className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
                <Shield className="w-3.5 h-3.5" /> Security Center
              </Link>
            )}
            <Link href="/chat" className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
              <ArrowLeft className="w-4 h-4" /> Back to Chat
            </Link>
          </div>
        </div>

        <div className="flex items-center gap-1.5 p-1.5 rounded-2xl" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl text-xs font-bold transition-all ${active ? "shadow-sm" : "opacity-70 hover:opacity-100"}`}
                style={{
                  background: active ? "var(--bg-card)" : "transparent",
                  color: active ? "var(--text-main)" : "var(--text-muted)",
                  border: active ? "1px solid var(--border-subtle)" : "1px solid transparent",
                }}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{t.label}</span>
              </button>
            );
          })}
        </div>

        {tab === "overview" && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <StatCard icon={FileText} label="Documents" value={documents.length} />
            <StatCard icon={Database} label="Datasets" value={datasets.length} />
            <StatCard icon={Bot} label="Registered Agents" value={agents.length} />
          </div>
        )}

        {tab === "documents" && (
          <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <h3 className="text-sm font-bold mb-3">Documents in your tenant</h3>
            {documents.length === 0 ? (
              <EmptyState text="No documents yet. Upload one from the RAG workspace in Chat." />
            ) : (
              <div className="space-y-2">
                {documents.map((d) => (
                  <div key={d.id} className="flex items-center justify-between p-3 rounded-xl" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                    <div>
                      <div className="text-sm font-semibold">{d.filename}</div>
                      <div className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
                        {d.pages} pages · {d.chunks} chunks · {d.size_kb} KB
                      </div>
                    </div>
                    <RaptorBuildButton docId={d.id} serverOn={raptorOn} />
                    <button onClick={() => deleteDocument(d.id)} className="p-2 rounded-lg hover:bg-red-500/10 text-red-500">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "datasets" && (
          <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <h3 className="text-sm font-bold mb-3">Datasets in your tenant</h3>
            {datasets.length === 0 ? (
              <EmptyState text="No datasets yet. Upload one from the Data Analyst tab in Chat." />
            ) : (
              <div className="space-y-2">
                {datasets.map((d) => (
                  <div key={d.id} className="flex items-center justify-between p-3 rounded-xl" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                    <div>
                      <div className="text-sm font-semibold">{d.filename}</div>
                      <div className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
                        {d.row_count} rows · {d.columns.length} columns · {d.size_kb} KB
                      </div>
                    </div>
                    <button onClick={() => deleteDataset(d.id)} className="p-2 rounded-lg hover:bg-red-500/10 text-red-500">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "graph" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div className="rounded-2xl p-5 space-y-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <h3 className="text-sm font-bold">Search the knowledge graph</h3>
              <div className="flex gap-2">
                <input
                  value={graphQuery}
                  onChange={(e) => setGraphQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && runGraphSearch()}
                  placeholder="Entity name, e.g. LRDE"
                  className="flex-1 px-3 py-2 rounded-xl text-sm outline-none"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                />
                <button onClick={runGraphSearch} disabled={graphSearching} className="px-3.5 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
                  {graphSearching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                </button>
              </div>
              <div className="space-y-1.5 max-h-72 overflow-y-auto">
                {graphResults.map((e) => (
                  <button
                    key={e.entity_id}
                    onClick={() => openEntity(e.entity_id)}
                    className="w-full flex items-center justify-between p-2.5 rounded-lg text-left text-xs hover:opacity-90"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}
                  >
                    <span className="font-semibold">{e.name}</span>
                    <span className="flex items-center gap-1" style={{ color: "var(--text-subtle)" }}>
                      {e.entity_type} <ChevronRight className="w-3 h-3" />
                    </span>
                  </button>
                ))}
                {graphResults.length === 0 && <EmptyState text="Search for an entity, or extract one below." />}
              </div>

              <div className="pt-3 border-t space-y-2" style={{ borderColor: "var(--border-subtle)" }}>
                <h4 className="text-xs font-bold">Extract entities from text</h4>
                <textarea
                  value={extractText}
                  onChange={(e) => setExtractText(e.target.value)}
                  rows={3}
                  placeholder="Paste a paragraph mentioning a lab, system, or technology…"
                  className="w-full px-3 py-2 rounded-xl text-xs outline-none resize-none"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                />
                {extractError && <p className="text-xs text-red-500">{extractError}</p>}
                <button onClick={runExtract} disabled={extracting || !extractText.trim()} className="px-3.5 py-2 rounded-xl text-xs font-semibold disabled:opacity-50" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
                  {extracting ? "Extracting…" : "Extract"}
                </button>
              </div>
            </div>

            <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <h3 className="text-sm font-bold mb-3">Entity detail</h3>
              {!selectedEntity ? (
                <EmptyState text="Select an entity to see its relationships." />
              ) : (
                <div className="space-y-3">
                  <div>
                    <div className="text-base font-bold">{selectedEntity.entity.name}</div>
                    <div className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
                      {selectedEntity.entity.entity_type} · {selectedEntity.entity.classification}
                    </div>
                  </div>

                  <GraphVisualization
                    center={selectedEntity.entity}
                    relationships={selectedEntity.relationships}
                    relatedEntities={selectedEntity.related_entities}
                    onNodeClick={openEntity}
                  />

                  <div className="space-y-1.5">
                    {selectedEntity.relationships.length === 0 ? (
                      <EmptyState text="No relationships recorded yet." />
                    ) : (
                      selectedEntity.relationships.map((r) => (
                        <div key={r.relationship_id} className="p-2.5 rounded-lg text-xs" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                          <span className="font-semibold">{r.relationship_type}</span>
                          {r.valid_from && <span style={{ color: "var(--text-subtle)" }}> · since {r.valid_from}</span>}
                          {r.valid_until && <span style={{ color: "var(--text-subtle)" }}> until {r.valid_until}</span>}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "agents" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <h3 className="text-sm font-bold mb-3 flex items-center gap-2"><Bot className="w-4 h-4" /> Agent Registry</h3>
              <div className="space-y-2">
                {agents.map((a) => (
                  <div key={a.agent_id} className="p-3 rounded-xl" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold">{a.name}</span>
                      <RiskBadge level={a.risk_level} />
                    </div>
                    <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{a.description}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <h3 className="text-sm font-bold mb-3 flex items-center gap-2"><Cpu className="w-4 h-4" /> Model Registry</h3>
              <div className="space-y-2">
                {models.map((m) => (
                  <div key={m.model_id} className="p-3 rounded-xl" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold font-mono">{m.model_id}</span>
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${m.enabled ? "text-emerald-500" : "text-slate-400"}`}>
                        {m.enabled ? "ENABLED" : "DISABLED"}
                      </span>
                    </div>
                    <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
                      {m.capabilities.join(", ")} · {m.context_window.toLocaleString()} ctx · cost: {m.cost_tier}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === "team" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div className="rounded-2xl p-5 space-y-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <h3 className="text-sm font-bold flex items-center gap-2"><Users className="w-4 h-4" /> Usage & Quotas</h3>
              {usage && (
                <div className="space-y-3">
                  <QuotaBar label="Members" used={usage.user_count} max={usage.max_users} />
                  <QuotaBar label="Documents" used={usage.document_count} max={usage.max_documents} />
                  <QuotaBar label="Datasets" used={usage.dataset_count} max={usage.max_datasets} />
                </div>
              )}

              {user?.role === "admin" && (
                <div className="pt-3 border-t space-y-2" style={{ borderColor: "var(--border-subtle)" }}>
                  <label className="block text-xs font-bold" style={{ color: "var(--text-muted)" }}>
                    <Pencil className="w-3 h-3 inline mr-1 -mt-0.5" /> Organization name
                  </label>
                  <div className="flex gap-2">
                    <input
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      className="flex-1 px-3 py-2 rounded-xl text-sm outline-none"
                      style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                    />
                    <button onClick={saveTenantName} disabled={renameSaving} className="px-3.5 py-2 rounded-xl text-xs font-semibold" style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}>
                      {renameSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : "Save"}
                    </button>
                  </div>
                  <p className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
                    Quota limits can be raised directly via <code>PATCH /tenants</code> — no dedicated slider UI yet; see Documents/Backend/multi-tenancy.md.
                  </p>
                </div>
              )}
            </div>

            <div className="rounded-2xl p-5 space-y-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
              <h3 className="text-sm font-bold flex items-center gap-2"><Users className="w-4 h-4" /> Members</h3>
              {user?.role !== "admin" ? (
                <EmptyState text="Only an admin of this organization can view and manage members." />
              ) : (
                <>
                  <div className="space-y-1.5 max-h-56 overflow-y-auto">
                    {members.map((m) => (
                      <div key={m.id} className="flex items-center justify-between p-2.5 rounded-lg text-xs" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
                        <span className="font-mono">{m.email}</span>
                        <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-subtle)" }}>{m.role}</span>
                      </div>
                    ))}
                  </div>

                  <div className="pt-3 border-t space-y-2" style={{ borderColor: "var(--border-subtle)" }}>
                    <h4 className="text-xs font-bold flex items-center gap-1.5"><UserPlus className="w-3.5 h-3.5" /> Add a member</h4>
                    <p className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
                      No email/invite system exists — you provision the account here and share the password with them directly.
                    </p>
                    <input
                      value={newMemberEmail}
                      onChange={(e) => setNewMemberEmail(e.target.value)}
                      placeholder="teammate@yourorg.com"
                      className="w-full px-3 py-2 rounded-xl text-xs outline-none"
                      style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                    />
                    <input
                      value={newMemberPassword}
                      onChange={(e) => setNewMemberPassword(e.target.value)}
                      type="password"
                      placeholder="Temporary password (min 8 chars)"
                      className="w-full px-3 py-2 rounded-xl text-xs outline-none"
                      style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
                    />
                    {memberError && <p className="text-xs text-red-500">{memberError}</p>}
                    <button onClick={addMember} disabled={addingMember} className="px-3.5 py-2 rounded-xl text-xs font-semibold disabled:opacity-50" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}>
                      {addingMember ? "Adding…" : "Add member"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: number }) {
  return (
    <div className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
      <div className="flex justify-between items-start mb-2">
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</span>
        <Icon className="w-4 h-4" style={{ color: "var(--primary-accent)" }} />
      </div>
      <div className="text-3xl font-bold">{value}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="text-xs italic py-4 text-center" style={{ color: "var(--text-subtle)" }}>{text}</p>;
}

function RiskBadge({ level }: { level: string }) {
  const colors: Record<string, string> = { low: "text-emerald-500", medium: "text-amber-500", high: "text-red-500" };
  return (
    <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full ${colors[level] || "text-slate-400"}`}>
      {level}
    </span>
  );
}

function QuotaBar({ label, used, max }: { label: string; used: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const nearLimit = pct >= 90;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs font-medium">
        <span style={{ color: "var(--text-main)" }}>{label}</span>
        <span className="font-mono" style={{ color: nearLimit ? "#ef4444" : "var(--text-subtle)" }}>{used} / {max}</span>
      </div>
      <div className="h-2 w-full rounded-full overflow-hidden" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}>
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: nearLimit ? "#ef4444" : "var(--primary-accent)" }} />
      </div>
    </div>
  );
}
