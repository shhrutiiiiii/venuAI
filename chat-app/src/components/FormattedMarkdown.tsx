"use client";

import React from "react";
import { Check, Copy } from "lucide-react";

interface FormattedMarkdownProps {
  content: string;
  className?: string;
}

export default function FormattedMarkdown({ content, className = "" }: FormattedMarkdownProps) {
  const [copiedIndex, setCopiedIndex] = React.useState<number | null>(null);

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  // Clean any weird character artifacts from model output
  const cleanedContent = content.replace(/\?\?/g, "—");

  // Split content into blocks (code blocks, tables, lists, paragraphs, headers)
  const blocks = parseMarkdownBlocks(cleanedContent);

  return (
    <div className={`space-y-3 font-sans text-sm leading-relaxed ${className}`}>
      {blocks.map((block, idx) => {
        if (block.type === "header") {
          const level = block.level;
          if (level === 1 || level === 2) {
            return (
              <h2
                key={idx}
                className="text-base font-extrabold tracking-tight pt-2 pb-1 border-b"
                style={{ color: "var(--text-main)", borderColor: "var(--border-subtle)" }}
              >
                {renderInlineFormatting(block.text)}
              </h2>
            );
          }
          return (
            <h3 key={idx} className="text-sm font-bold pt-1.5 flex items-center gap-1.5" style={{ color: "var(--primary-accent)" }}>
              {renderInlineFormatting(block.text)}
            </h3>
          );
        }

        if (block.type === "table") {
          return (
            <div key={idx} className="my-3 overflow-x-auto rounded-2xl border shadow-sm" style={{ borderColor: "var(--border-subtle)", background: "var(--bg-input)" }}>
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr style={{ background: "var(--badge-bg)", borderBottom: "1px solid var(--border-subtle)" }}>
                    {block.headers.map((h, i) => (
                      <th key={i} className="px-4 py-3 font-bold uppercase text-[11px] tracking-wider" style={{ color: "var(--primary-accent)" }}>
                        {renderInlineFormatting(h.trim())}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y" style={{ borderColor: "var(--border-subtle)" }}>
                  {block.rows.map((row, rIdx) => (
                    <tr key={rIdx} className="hover:bg-slate-500/5 transition-colors">
                      {row.map((cell, cIdx) => (
                        <td key={cIdx} className="px-4 py-2.5 leading-relaxed font-medium" style={{ color: "var(--text-main)" }}>
                          {renderInlineFormatting(cell.trim())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        if (block.type === "code") {
          return (
            <div key={idx} className="my-3 rounded-2xl border overflow-hidden font-mono text-xs shadow-sm" style={{ background: "var(--bg-input)", borderColor: "var(--border-subtle)" }}>
              <div className="flex items-center justify-between px-3.5 py-2 border-b text-[10px] font-bold" style={{ borderColor: "var(--border-subtle)", color: "var(--text-subtle)", background: "var(--bg-card)" }}>
                <span>{block.lang || "code"}</span>
                <button
                  type="button"
                  onClick={() => handleCopy(block.text, idx)}
                  className="flex items-center gap-1 hover:opacity-80 transition-opacity"
                  style={{ color: "var(--text-muted)" }}
                >
                  {copiedIndex === idx ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-500" />
                      <span>Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </div>
              <pre className="p-3.5 overflow-x-auto" style={{ color: "var(--text-main)" }}>
                <code>{block.text}</code>
              </pre>
            </div>
          );
        }

        if (block.type === "unordered-list") {
          return (
            <ul key={idx} className="space-y-2 pl-1 my-2">
              {block.items.map((item, i) => (
                <li key={i} className="flex items-start gap-2.5 text-xs sm:text-sm">
                  <span className="inline-block w-1.5 h-1.5 rounded-full mt-2 shrink-0 shadow-sm" style={{ background: "var(--primary-accent)" }} />
                  <span style={{ color: "var(--text-main)" }}>{renderInlineFormatting(item)}</span>
                </li>
              ))}
            </ul>
          );
        }

        if (block.type === "ordered-list") {
          return (
            <ol key={idx} className="space-y-2 pl-1 my-2">
              {block.items.map((item, i) => (
                <li key={i} className="flex items-start gap-2.5 text-xs sm:text-sm">
                  <span className="inline-flex items-center justify-center w-4.5 h-4.5 rounded-full text-[10px] font-extrabold shrink-0 mt-0.5 border" style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--primary-accent)" }}>
                    {i + 1}
                  </span>
                  <span style={{ color: "var(--text-main)" }}>{renderInlineFormatting(item)}</span>
                </li>
              ))}
            </ol>
          );
        }

        // Paragraph
        return (
          <p key={idx} className="text-xs sm:text-sm leading-relaxed font-normal" style={{ color: "var(--text-main)" }}>
            {renderInlineFormatting(block.text)}
          </p>
        );
      })}
    </div>
  );
}

// ── Helpers ──

interface BlockHeader { type: "header"; level: number; text: string }
interface BlockTable { type: "table"; headers: string[]; rows: string[][] }
interface BlockCode { type: "code"; lang: string; text: string }
interface BlockUList { type: "unordered-list"; items: string[] }
interface BlockOList { type: "ordered-list"; items: string[] }
interface BlockParagraph { type: "paragraph"; text: string }

type Block = BlockHeader | BlockTable | BlockCode | BlockUList | BlockOList | BlockParagraph;

function parseMarkdownBlocks(text: string): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced Code Block
    if (line.trim().startsWith("```")) {
      const lang = line.trim().replace(/^```/, "").trim();
      i++;
      const codeLines: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing ```
      blocks.push({ type: "code", lang, text: codeLines.join("\n") });
      continue;
    }

    // Markdown Table
    if (line.trim().startsWith("|") && line.trim().endsWith("|")) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|") && lines[i].trim().endsWith("|")) {
        tableLines.push(lines[i].trim());
        i++;
      }
      if (tableLines.length >= 2) {
        const headers = tableLines[0].split("|").slice(1, -1);
        const rawRows = tableLines.slice(2);
        const rows = rawRows.map((r) => r.split("|").slice(1, -1));
        blocks.push({ type: "table", headers, rows });
        continue;
      }
    }

    // Headers
    if (line.trim().startsWith("#")) {
      const match = line.trim().match(/^(#{1,6})\s+(.*)$/);
      if (match) {
        blocks.push({ type: "header", level: match[1].length, text: match[2] });
        i++;
        continue;
      }
    }

    // Unordered List
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*•]\s+/, ""));
        i++;
      }
      blocks.push({ type: "unordered-list", items });
      continue;
    }

    // Ordered List
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ type: "ordered-list", items });
      continue;
    }

    // Empty lines
    if (!line.trim()) {
      i++;
      continue;
    }

    // Paragraph
    blocks.push({ type: "paragraph", text: line.trim() });
    i++;
  }

  return blocks;
}

function renderInlineFormatting(text: string): React.ReactNode {
  // Replace <br> or <br/> with space/newline
  const clean = text.replace(/<br\s*\/?>/gi, " ");

  // Simple parser for **bold** and `code`
  const parts = clean.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);

  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={idx} className="font-bold tracking-tight" style={{ color: "var(--primary-accent)" }}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={idx}
          className="px-1.5 py-0.5 rounded-md text-[11px] font-mono border"
          style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--primary-accent)" }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}
