"use client";

import React, { useEffect, useState } from "react";
import FormattedMarkdown from "./FormattedMarkdown";

interface StreamingMessageProps {
  content: string;
  isStreaming?: boolean;
  speedMs?: number;
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isStreaming = false,
  speedMs = 15,
}) => {
  const [displayedText, setDisplayedText] = useState("");

  const safeContent =
    typeof content === "string"
      ? content
      : typeof content === "object"
      ? JSON.stringify(content)
      : String(content || "");

  useEffect(() => {
    if (!isStreaming) {
      setDisplayedText(safeContent);
      return;
    }

    if (displayedText.length < safeContent.length) {
      const timeout = setTimeout(() => {
        setDisplayedText(safeContent.slice(0, displayedText.length + 3));
      }, speedMs);
      return () => clearTimeout(timeout);
    }
  }, [safeContent, displayedText, isStreaming, speedMs]);

  return (
    <div className="relative">
      <FormattedMarkdown content={displayedText} />
      {isStreaming && (
        <span className="inline-block w-2 h-4 ml-1 bg-purple-400 animate-pulse rounded-xs vertical-middle" />
      )}
    </div>
  );
};

export default StreamingMessage;
