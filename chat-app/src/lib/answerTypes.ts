/**
 * Shared answer/citation/verification types and helpers for the chat UI.
 * Every new backend field is optional: older backends only send `sources`.
 */

export interface RagSource {
  document: string;
  page: number;
  chunk_preview?: string;
}

/** Quote-verified citation (future backend shape). */
export interface Citation {
  index: number;
  chunk_id?: string;
  doc_id?: string;
  filename: string;
  page?: number | null;
  quote?: string;
  verified?: boolean;
  char_start?: number | null;
  char_end?: number | null;
  reason?: string | null;
}

export interface VerificationInfo {
  /** null/undefined => not measured. Never defaulted to a number. */
  groundingScore: number | null;
  verified: boolean | null;
  reason?: string;
}

/** Normalise backend citations; fall back to the legacy `sources` list (unverified, no quotes). */
export function normalizeCitations(citations?: Citation[] | null, sources?: RagSource[] | null): Citation[] {
  if (Array.isArray(citations) && citations.length > 0) {
    return citations.map((c, i) => ({ ...c, index: typeof c.index === "number" ? c.index : i + 1 }));
  }
  return (sources ?? []).map((s, i) => ({
    index: i + 1,
    filename: s.document,
    page: s.page,
    quote: s.chunk_preview,
    verified: undefined,
  }));
}

/** Only trust a numeric score; anything else is "not measured". */
export function cleanScore(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function citationsToMarkdown(cites: Citation[]): string {
  if (!cites.length) return "";
  const lines = cites.map((c) => {
    const page = c.page != null ? `, p. ${c.page}` : "";
    const q = c.quote ? ` — "${c.quote.replace(/\s+/g, " ").trim()}"` : "";
    const v = c.verified === true ? " (verified)" : c.verified === false ? " (unverified)" : "";
    return `${c.index}. ${c.filename}${page}${q}${v}`;
  });
  return `\n\n## Sources\n\n${lines.join("\n")}\n`;
}

export function answerToMarkdown(opts: {
  question?: string;
  answer: string;
  citations?: Citation[];
  footer?: string[];
}): string {
  const head = opts.question ? `# ${opts.question.trim()}\n\n` : "";
  const foot = opts.footer?.length ? `\n\n---\n${opts.footer.join("\n")}\n` : "";
  return `${head}${opts.answer}${citationsToMarkdown(opts.citations ?? [])}${foot}`;
}

export function downloadMarkdown(filename: string, md: string) {
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".md") ? filename : `${filename}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "answer";
}
