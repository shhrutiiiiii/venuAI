"use client";

import { useEffect, useState } from "react";
import { NO_CAPABILITIES, type Capabilities } from "@/lib/apiContracts";

let cached: Promise<Capabilities> | null = null;

function loadCapabilities(): Promise<Capabilities> {
  if (!cached) {
    cached = fetch("/api/capabilities", { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<Partial<Capabilities>>) : {}))
      .then((c) => ({ ...NO_CAPABILITIES, ...c }))
      .catch(() => NO_CAPABILITIES);
    // Do not cache a negative result forever: retry after 30s (backend may start later).
    cached.then((c) => {
      if (!Object.values(c).some(Boolean)) setTimeout(() => (cached = null), 30_000);
    });
  }
  return cached;
}

/** Which optional backend features are deployed. All false until probed (features stay hidden). */
export function useCapabilities(): Capabilities {
  const [caps, setCaps] = useState<Capabilities>(NO_CAPABILITIES);
  useEffect(() => {
    let alive = true;
    loadCapabilities().then((c) => {
      if (alive) setCaps(c);
    });
    return () => {
      alive = false;
    };
  }, []);
  return caps;
}

/** True when an endpoint says "not deployed" or "disabled": the feature should be hidden, not shown broken. */
export function isUnavailableStatus(status: number): boolean {
  return status === 404 || status === 405 || status === 501 || status === 503;
}
