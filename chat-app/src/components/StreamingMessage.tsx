"use client";

import React, { useEffect, useState } from "react";
import FormattedMarkdown from "./FormattedMarkdown";
import type { Citation } from "@/lib/answerTypes";

interface StreamingMessageProps {
  content: string;
  isStreaming?: boolean;
  speedMs?: number;
  citations?: Citation[];
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isStreaming = false,
  speedMs = 15,
  citations,
}) => {
  const [displayedText, setDisplayedText] = useState("");

  const safeContent =
    typeof content === "string"
      ? content
      : typeof content === "object"
      ? JSON.stringify(content)
      : String(content || "");

  useEffect(() => {
    // When not streaming, render safeContent directly (see below) — no need
    // to mirror it into state, which was an unnecessary extra render.
    if (!isStreaming) return;

    if (displayedText.length < safeContent.length) {
      const timeout = setTimeout(() => {
        setDisplayedText(safeContent.slice(0, displayedText.length + 3));
      }, speedMs);
      return () => clearTimeout(timeout);
    }
  }, [safeContent, displayedText, isStreaming, speedMs]);

  const textToRender = isStreaming ? displayedText : safeContent;

  return (
    <div className="relative m-stream">
      <FormattedMarkdown content={textToRender} citations={citations} />
      {isStreaming && (
        <span className="m-caret" aria-hidden="true" />
      )}
    </div>
  );
};

export default StreamingMessage;
