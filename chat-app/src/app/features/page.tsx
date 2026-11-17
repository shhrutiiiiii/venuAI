"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { FeatureCard } from "@/components/features/FeatureCard";
import { MemoryOptIn, RaptorBuildButton } from "@/components/features/FeatureControls";
import type { FeatureInfo } from "@/components/features/FeatureBadges";

const PROMINENT = ["long_term_memory", "voice", "raptor"];

interface Doc {
  id: string;
  filename: string;
}

export default function FeaturesPage() {
  const [features, setFeatures] = useState<FeatureInfo[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [docs, setDocs] = useState<Doc[]>([]);

  useEffect(() => {
    let alive = true;
    fetch("/api/features", { cache: "no-store" })
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) return setUnavailable(true);
        const d = (await r.json()) as { features?: FeatureInfo[] };
        setFeatures(d.features ?? []);
      })
      .catch(() => alive && setUnavailable(true));
    fetch("/api/rag/documents", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => alive && Array.isArray(d) && setDocs(d))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const by = (k: string) => features?.find((f) => f.key === k);
  const others = (features ?? []).filter((f) => !PROMINENT.includes(f.key));
  const memory = by("long_term_memory");
  const voice = by("voice");
  const raptor = by("raptor");

  return (
    <main className="max-w-3xl mx-auto px-4 py-8" style={{ color: "var(--text-main)" }}>
      <Link href="/chat" className="text-xs" style={{ color: "var(--text-muted)" }}>&larr; Back to chat</Link>
      <h1 className="text-xl font-bold mt-2">Features</h1>
      <p className="text-xs mb-6" style={{ color: "var(--text-muted)" }}>
        Optional features and what they cost in LLM tokens. Nothing here runs automatically; see Documents/AI/token-costs.md.
      </p>
      {unavailable && <p role="status" className="text-sm">Feature information is not available from this server.</p>}
      {!features && !unavailable && <p className="text-sm">Loading...</p>}
      {features && (
        <div className="space-y-3">
          {memory && (
            <FeatureCard feature={memory}>
              <MemoryOptIn serverOn={memory.enabled} />
            </FeatureCard>
          )}
          {voice && (
            <FeatureCard feature={voice}>
              <p className="text-[11px]" style={{ color: "var(--text-subtle)" }}>
                {voice.enabled ? "Mic and Listen buttons are active in chat." : "Mic and Listen buttons are visible in chat but disabled. Set VOICE_ENABLED=on"}
              </p>
            </FeatureCard>
          )}
          {raptor && (
            <FeatureCard feature={raptor}>
              {docs.length === 0 ? (
                <p className="text-[11px]" style={{ color: "var(--text-subtle)" }}>No documents yet.</p>
              ) : (
                <ul className="space-y-2">
                  {docs.map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate">{d.filename}</span>
                      <RaptorBuildButton docId={d.id} serverOn={raptor.enabled} />
                    </li>
                  ))}
                </ul>
              )}
            </FeatureCard>
          )}
          <h2 className="text-sm font-bold pt-4">Other optional mechanisms</h2>
          {others.map((f) => (
            <FeatureCard key={f.key} feature={f} />
          ))}
        </div>
      )}
    </main>
  );
}
