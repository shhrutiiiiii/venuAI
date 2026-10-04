"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Upload, Database, Trash2, Loader2, BarChart3, FileSpreadsheet, Send } from "lucide-react";
import { DataBarChart, ChartData } from "./DataBarChart";

interface DatasetInfo {
  id: string;
  filename: string;
  columns: string[];
  dtypes: Record<string, string>;
  row_count: number;
  size_kb: number;
  upload_time: string;
  classification: string;
}

interface AnalyzeResponse {
  operation: string;
  computed_values: Record<string, unknown>;
  interpretation: string;
  referenced_columns: string[];
  chart_data: ChartData | null;
}

export default function DataAnalystWorkspace() {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const loadDatasets = useCallback(async () => {
    try {
      const res = await fetch("/api/datasets");
      if (res.ok) {
        const data = await res.json();
        setDatasets(Array.isArray(data) ? data : []);
      }
    } catch {
      // silent — list stays empty, upload/retry remains available
    }
  }, []);

  useEffect(() => {
    // Standard fetch-on-mount — see the matching comment in
    // src/app/analytics/page.tsx for why this needs the disable.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadDatasets();
  }, [loadDatasets]);

  const handleFileSelect = async (file: File) => {
    const ext = file.name.toLowerCase();
    if (!ext.endsWith(".csv") && !ext.endsWith(".xlsx")) {
      setUploadError("Only .csv and .xlsx files are supported.");
      return;
    }
    setUploading(true);
    setUploadError(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/datasets/upload", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        setUploadError(data.detail || "Upload failed.");
        return;
      }
      setDatasets((prev) => [data, ...prev]);
      setSelectedId(data.id);
      setResult(null);
    } catch {
      setUploadError("Dataset server unavailable.");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: string) => {
    await fetch(`/api/datasets/${id}`, { method: "DELETE" });
    setDatasets((prev) => prev.filter((d) => d.id !== id));
    if (selectedId === id) {
      setSelectedId(null);
      setResult(null);
    }
  };

  const handleAnalyze = async () => {
    if (!selectedId || !question.trim()) return;
    setAnalyzing(true);
    setAnalyzeError(null);
    setResult(null);
    try {
      const res = await fetch(`/api/datasets/${selectedId}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      if (!res.ok) {
        setAnalyzeError(data.detail || "Analysis failed.");
        return;
      }
      setResult(data);
    } catch {
      setAnalyzeError("Dataset server unavailable.");
    } finally {
      setAnalyzing(false);
    }
  };

  const selectedDataset = datasets.find((d) => d.id === selectedId) || null;

  return (
    // No outer flex-1/overflow-y-auto wrapper here — the call site in
    // chat/page.tsx already provides that (same as the other three
    // workspace modes' content divs), so this composes directly into it.
    <div className="h-full">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Header */}
        <div className="text-center space-y-1.5">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider"
               style={{ background: "var(--badge-bg)", color: "var(--badge-text)", border: "1px solid var(--badge-border)" }}>
            <BarChart3 className="w-3 h-3" /> Data Analyst Agent
          </div>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Upload a CSV/XLSX dataset, then ask a question. Every number comes from real pandas computation — the AI only explains it.
          </p>
        </div>

        {/* Upload */}
        <div
          className="rounded-2xl border p-6 text-center cursor-pointer transition-all hover:opacity-90"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", borderStyle: "dashed" }}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFileSelect(file);
              e.target.value = "";
            }}
          />
          {uploading ? (
            <Loader2 className="w-6 h-6 mx-auto animate-spin text-purple-400" />
          ) : (
            <>
              <Upload className="w-6 h-6 mx-auto mb-2 text-purple-400" />
              <p className="text-sm font-semibold" style={{ color: "var(--text-main)" }}>
                Click to upload a dataset (.csv or .xlsx)
              </p>
            </>
          )}
          {uploadError && <p className="text-xs text-red-400 mt-2">{uploadError}</p>}
        </div>

        {/* Dataset list */}
        {datasets.length > 0 && (
          <div className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-subtle)" }}>
              Your datasets
            </p>
            {datasets.map((d) => (
              <div
                key={d.id}
                onClick={() => {
                  setSelectedId(d.id);
                  setResult(null);
                }}
                className={`flex items-center justify-between p-3 rounded-xl border cursor-pointer transition-all ${
                  selectedId === d.id ? "ring-1 ring-purple-500/50" : ""
                }`}
                style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)" }}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <FileSpreadsheet className="w-4 h-4 text-purple-400 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-xs font-semibold truncate" style={{ color: "var(--text-main)" }}>{d.filename}</p>
                    <p className="text-[10px]" style={{ color: "var(--text-subtle)" }}>
                      {d.row_count} rows · {d.columns.length} columns
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(d.id);
                  }}
                  className="p-1.5 rounded-lg hover:text-red-400 transition-colors shrink-0"
                  style={{ color: "var(--text-muted)" }}
                  aria-label={`Delete ${d.filename}`}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Question input */}
        {selectedDataset && (
          <div className="space-y-2">
            <p className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
              Columns: {selectedDataset.columns.join(", ")}
            </p>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAnalyze();
                }}
                placeholder={`Ask about ${selectedDataset.filename}… e.g. "What is the average revenue?"`}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm outline-none"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-main)" }}
              />
              <button
                type="button"
                onClick={handleAnalyze}
                disabled={analyzing || !question.trim()}
                className="p-2.5 rounded-xl disabled:opacity-50"
                style={{ background: "var(--primary-gradient)", color: "var(--btn-text)" }}
                aria-label="Analyze"
              >
                {analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              </button>
            </div>
            {analyzeError && <p className="text-xs text-red-400">{analyzeError}</p>}
          </div>
        )}

        {/* Results */}
        {result && (
          <div className="rounded-2xl border p-5 space-y-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md"
                    style={{ background: "var(--badge-bg)", color: "var(--badge-text)" }}>
                {result.operation.replace(/_/g, " ")}
              </span>
            </div>

            <p className="text-sm leading-relaxed" style={{ color: "var(--text-main)" }}>{result.interpretation}</p>

            {result.chart_data && <DataBarChart chart={result.chart_data} />}

            <details className="text-xs">
              <summary className="cursor-pointer font-semibold" style={{ color: "var(--text-subtle)" }}>
                Raw computed values
              </summary>
              <pre className="mt-2 p-3 rounded-lg overflow-x-auto text-[11px]" style={{ background: "var(--bg-input)", color: "var(--text-muted)" }}>
                {JSON.stringify(result.computed_values, null, 2)}
              </pre>
            </details>
          </div>
        )}

        {!selectedDataset && datasets.length === 0 && !uploading && (
          <div className="text-center py-10">
            <Database className="w-8 h-8 mx-auto mb-2 opacity-40" style={{ color: "var(--text-subtle)" }} />
            <p className="text-xs" style={{ color: "var(--text-subtle)" }}>
              No datasets yet — upload a CSV or XLSX file to get started.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
