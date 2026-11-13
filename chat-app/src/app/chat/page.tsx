"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  MessageCircle,
  Search,
  Bell,
  Settings,
  LogOut,
  Users,
  Hash,
  Plus,
  Smile,
  Paperclip,
  Send,
  Mic,
  ChevronDown,
  Circle,
  Sparkles,
  Bot,
  Globe,
  Brain,
  Copy,
  Check,
  RotateCcw,
  ThumbsUp,
  ThumbsDown,
  Trash2,
  Cpu,
  Layers,
  Terminal,
  Zap,
  Shield,
  ArrowRight,
  FileText,
  Upload,
  BookOpen,
  Database,
  X,
  ChevronRight,
  Atom,
  Radar,
  Crosshair,
  BarChart3,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import ThemeToggle from "@/components/ThemeToggle";
import FormattedMarkdown from "@/components/FormattedMarkdown";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { VerificationPanel } from "@/components/VerificationPanel";
import { ModelRouting, type RegistryModel } from "@/components/ModelRouting";
import { ShieldAlert } from "lucide-react";
import { AnswerActions } from "@/components/AnswerActions";
import { ResearchReport, ResearchPending, type ResearchData } from "@/components/ResearchReport";
import { answerToMarkdown, cleanScore, normalizeCitations, type Citation } from "@/lib/answerTypes";
import { AgentModeToggle, AgentMode } from "@/components/AgentModeToggle";
import { StreamingMessage } from "@/components/StreamingMessage";
import DataAnalystWorkspace from "@/components/DataAnalystWorkspace";

// ─── Error Formatting Helper ──────────────────────────────────────────────────

type ApiErrorItem = { msg?: string; message?: string; loc?: string[] } | string;
type ApiErrorValue = string | ApiErrorItem[] | { msg?: string; message?: string };

function extractErrorMessage(data: unknown, fallback: string = "An error occurred."): string {
  if (!data) return fallback;
  if (typeof data === "string") return data;
  if (typeof data !== "object") return fallback;

  const obj = data as { error?: ApiErrorValue; detail?: ApiErrorValue };
  if (typeof obj.error === "string") return obj.error;
  if (typeof obj.detail === "string") return obj.detail;

  if (Array.isArray(obj.detail)) {
    return obj.detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
          const msg = item.msg || item.message || JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(item);
      })
      .join("; ");
  }

  if (Array.isArray(obj.error)) {
    return obj.error
      .map((item) => (typeof item === "string" ? item : (item?.msg || JSON.stringify(item))))
      .join("; ");
  }

  if (obj.detail && typeof obj.detail === "object") {
    return obj.detail.msg || obj.detail.message || JSON.stringify(obj.detail);
  }

  if (obj.error && typeof obj.error === "object") {
    return obj.error.msg || obj.error.message || JSON.stringify(obj.error);
  }

  return fallback;
}

// ─── AI Models List ───────────────────────────────────────────────────────────

// ─── DRDO RAG Types ───────────────────────────────────────────────────────────

interface RagDocument {
  id: string;
  filename: string;
  pages: number;
  chunks: number;
  upload_time: string;
  size_kb: number;
}

interface RagSource {
  document: string;
  page: number;
  chunk_preview: string;
}

interface RagMessage {
  id: string;
  sender: "user" | "ai";
  text: string;
  timestamp: string;
  sources?: RagSource[];
  chunks_retrieved?: number;
  grounding_score?: number;
  hallucination_score?: number;
  verification_flags?: string[];
  citations?: Citation[];
  verified?: boolean | null;
  verification_reason?: string;
  blocked?: boolean;
  block_reason?: string;
  model_used?: string;
  question?: string;
  research?: ResearchData;
  researchPending?: boolean;
  agent_mode?: AgentMode;
  isStreaming?: boolean;
  isError?: boolean;
}

// ─── DRDO Quick Prompts ────────────────────────────────────────────────────────

const DRDO_QUICK_PROMPTS = [
  { icon: BookOpen, prompt: "What laboratories belong to the Missiles cluster in DRDO?", desc: "Explore DRDL, RCI, and ASL labs" },
  { icon: Atom, prompt: "What are the technical specifications of Agni-V missile?", desc: "Range, payload & propulsion specs" },
  { icon: Radar, prompt: "Tell me about Uttam AESA Radar developed by LRDE", desc: "Indigenous fighter radar & TR modules" },
  { icon: Crosshair, prompt: "List all DRDO Young Scientist Laboratories (DYSL)", desc: "AI, Quantum, Smart Materials & Asymmetric" },
];

// ─── Initial AI Conversation Threads ──────────────────────────────────────────

const INITIAL_AI_THREADS = [
  { id: "t-1", title: "Sub-10ms WebSocket Architecture", date: "Today", count: 4 },
  { id: "t-2", title: "React 19 Server Components Audit", date: "Yesterday", count: 8 },
  { id: "t-3", title: "Tailwind CSS v4 Token Mapping", date: "Previous 7 Days", count: 3 },
];

// ─── Initial AI Messages Stream ───────────────────────────────────────────────

interface AiMessage {
  id: string;
  sender: "user" | "ai";
  text: string;
  timestamp: string;
  modelName?: string;
  reasoningTime?: string;
  codeSnippet?: { language: string; code: string };
  thumbs?: "up" | "down" | null;
}

const INITIAL_AI_MESSAGES: AiMessage[] = [
  {
    id: "m-1",
    sender: "user",
    text: "Can you explain how VenuAI achieves sub-10ms latency in realtime messaging?",
    timestamp: "10:14 AM",
  },
  {
    id: "m-2",
    sender: "ai",
    modelName: "VenuAI backend",
    reasoningTime: "0.24s",
    timestamp: "10:14 AM",
    text: "VenuAI achieves sub-10ms latency by pairing an event-driven WebSocket pooling layer with zero-allocation memory buffers and optimized regional edge routing. Here is a high-level architectural overview:",
    codeSnippet: {
      language: "typescript",
      code: `// High-Performance Regional Gateway Pool
import { WebSocketGateway } from '@venu/core';

export const streamPool = new WebSocketGateway({
  regionalEdges: ['us-east-1', 'eu-central-1', 'ap-south-1'],
  bufferAllocation: 'zero-copy',
  targetLatencyMs: 10,
});

streamPool.on('connection', (socket) => {
  socket.setNoDelay(true); // Disable Nagle's algorithm for instant packet dispatch
});`,
    },
    thumbs: "up",
  },
];

// ─── Team Chat Mock Data ──────────────────────────────────────────────────────

const CHANNELS = [
  { id: "general", name: "general", unread: 0 },
  { id: "engineering", name: "engineering", unread: 3 },
  { id: "design", name: "design", unread: 1 },
  { id: "product", name: "product", unread: 0 },
  { id: "random", name: "random", unread: 7 },
];

const DMS = [
  { id: "sarah", name: "Sarah Rose", avatar: "SR", online: true, bg: "linear-gradient(135deg, #6366f1, #8b5cf6)" },
  { id: "alex", name: "Alex Chen", avatar: "AC", online: true, bg: "linear-gradient(135deg, #ec4899, #f43f5e)" },
  { id: "jordan", name: "Jordan Miller", avatar: "JM", online: false, bg: "linear-gradient(135deg, #10b981, #059669)" },
];

const TEAM_MESSAGES = [
  {
    id: 1,
    author: "Alex Chen",
    avatar: "AC",
    avatarBg: "linear-gradient(135deg, #ec4899, #f43f5e)",
    time: "2:41 PM",
    text: "Hey team! Check out the clean theme switch and AI Agent interface 🌗",
    reactions: ["🔥 4", "✨ 6"],
  },
  {
    id: 2,
    author: "Sarah Rose",
    avatar: "SR",
    avatarBg: "linear-gradient(135deg, #6366f1, #8b5cf6)",
    time: "2:42 PM",
    text: "The AI agent UI matches the login screen tokens perfectly. Very sleek!",
    reactions: ["👏 3"],
  },
];

// ─── Main AI Agent Workspace ──────────────────────────────────────────────────

export default function ChatPage() {
  const { user, isLoading, isAuthenticated, logout } = useAuth();
  const router = useRouter();

  // Mode: "ai_agent" | "team_chat" | "drdo_rag"
  const [workspaceMode, setWorkspaceMode] = useState<"ai_agent" | "team_chat" | "drdo_rag" | "data_analyst">("drdo_rag");

  // AI Agent States
  const [registryModels, setRegistryModels] = useState<RegistryModel[] | null>(null);
  const [deepResearch, setDeepResearch] = useState(false);
  const [aiThreads, setAiThreads] = useState(INITIAL_AI_THREADS);
  const [activeThreadId, setActiveThreadId] = useState<string | null>("t-1");
  const [messages, setMessages] = useState<AiMessage[]>(INITIAL_AI_MESSAGES);
  const [inputPrompt, setInputPrompt] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Toggles
  const [webSearchActive, setWebSearchActive] = useState(false);
  const [deepThinkActive, setDeepThinkActive] = useState(true);

  // Team Chat States
  const [activeChannel, setActiveChannel] = useState("engineering");
  const [teamMessageInput, setTeamMessageInput] = useState("");

  // ── DRDO RAG States ──────────────────────────────────────────────────────
  const [ragDocuments, setRagDocuments] = useState<RagDocument[]>([]);
  const [ragMessages, setRagMessages] = useState<RagMessage[]>([]);
  const [ragInput, setRagInput] = useState("");
  const [ragLoading, setRagLoading] = useState(false);
  const [ragUploading, setRagUploading] = useState(false);
  const [ragUploadProgress, setRagUploadProgress] = useState<string | null>(null);
  const [ragServerStatus, setRagServerStatus] = useState<"checking" | "online" | "offline">("checking");
  const [isDragOver, setIsDragOver] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set());
  const [agentMode, setAgentMode] = useState<AgentMode>("rag");
  const [useStreaming, setUseStreaming] = useState(true);
  const ragFileInputRef = useRef<HTMLInputElement>(null);
  const ragMessagesEndRef = useRef<HTMLDivElement>(null);

  const [mounted, setMounted] = useState(false);

  // Declared above the effects that reference them — `const` bindings are
  // not hoisted, so even though effects only run after the full render
  // (making the original call-before-declare order runtime-safe), this
  // ordering is the honest, lint-clean version of the same behavior.
  const checkRagServerHealth = async () => {
    try {
      const res = await fetch("/api/rag/health");
      if (res.ok) {
        setRagServerStatus("online");
      } else {
        setRagServerStatus("offline");
      }
    } catch {
      setRagServerStatus("offline");
    }
  };

  const fetchRagDocuments = async () => {
    try {
      const res = await fetch("/api/rag/documents");
      if (res.ok) {
        const docs = await res.json();
        setRagDocuments(Array.isArray(docs) ? docs : []);
        setRagServerStatus("online");
      } else {
        setRagServerStatus("offline");
      }
    } catch {
      setRagServerStatus("offline");
    }
  };

  useEffect(() => {
    let cancelled = false;
    fetch("/api/models")
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => {
        if (!cancelled) setRegistryModels(Array.isArray(d) ? d : []);
      })
      .catch(() => {
        if (!cancelled) setRegistryModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // Standard "mark as mounted" flag for hydration-safe rendering — this
    // can only run client-side, after mount, which is the documented
    // exception to the setState-in-effect rule.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
    checkRagServerHealth();
    const interval = setInterval(checkRagServerHealth, 15000);
    return () => clearInterval(interval);
  }, []);

  // Route protection
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  // Load RAG documents when switching to RAG mode
  useEffect(() => {
    if (workspaceMode === "drdo_rag") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      fetchRagDocuments();
    }
  }, [workspaceMode]);

  // Auto-scroll RAG messages
  useEffect(() => {
    ragMessagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [ragMessages, ragLoading]);

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  // ── DRDO RAG Handlers ────────────────────────────────────────────────────
  // (checkRagServerHealth, fetchRagDocuments now declared above, before the
  // effects that use them)

  const handleRagUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setRagUploadProgress("❌ Only PDF files are supported.");
      setTimeout(() => setRagUploadProgress(null), 3000);
      return;
    }
    setRagUploading(true);
    setRagUploadProgress(`⏳ Uploading "${file.name}"…`);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/rag/upload", { method: "POST", body: formData });
      const data = await res.json();
      if (res.ok) {
        setRagUploadProgress(`✅ "${data.filename}" ingested — ${data.pages} pages, ${data.chunks} chunks`);
        setRagDocuments((prev) => [data, ...prev]);
      } else {
        setRagUploadProgress(`❌ Upload failed: ${extractErrorMessage(data, "Unknown error")}`);
      }
    } catch {
      setRagUploadProgress("❌ RAG server unavailable. Start rag_server.py first.");
    } finally {
      setRagUploading(false);
      setTimeout(() => setRagUploadProgress(null), 5000);
    }
  };

  const handleRagFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleRagUpload(file);
    e.target.value = "";
  };

  const handleRagDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleRagUpload(file);
  };

  const handleRagDelete = async (docId: string) => {
    try {
      const res = await fetch(`/api/rag/documents?id=${docId}`, { method: "DELETE" });
      if (res.ok) {
        setRagDocuments((prev) => prev.filter((d) => d.id !== docId));
      }
    } catch {
      // silent
    }
  };

  const handleRagQuery = async (promptText?: string) => {
    const question = (promptText || ragInput).trim();
    if (!question || ragLoading) return;

    // Date.now() here generates a unique message id inside an event handler
    // (triggered by the user sending a message) — not during render. The
    // react-hooks/purity rule's static analysis doesn't distinguish render
    // bodies from event-handler bodies in this preview version, so it flags
    // this despite it being an ordinary, safe id-generation pattern.
    const userMsg: RagMessage = {
      // eslint-disable-next-line react-hooks/purity
      id: `rag-user-${Date.now()}`,
      sender: "user",
      text: question,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };
    setRagMessages((prev) => [...prev, userMsg]);
    setRagInput("");
    setRagLoading(true);

    // eslint-disable-next-line react-hooks/purity
    const aiMsgId = `rag-ai-${Date.now()}`;

    // Add placeholder AI message
    const placeholderMsg: RagMessage = {
      id: aiMsgId,
      sender: "ai",
      text: deepResearch || useStreaming ? "" : "Analyzing DRDO research documents...",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      agent_mode: agentMode,
      isStreaming: useStreaming && !deepResearch,
      question,
      researchPending: deepResearch,
    };
    setRagMessages((prev) => [...prev, placeholderMsg]);

    if (deepResearch) {
      await runDeepResearch(question, aiMsgId);
      return;
    }

    const requestBody = {
      question,
      top_k: 4,
      history: ragMessages,
      agent_mode: agentMode === "react",
      multi_agent_mode: agentMode === "multi_agent",
    };

    if (useStreaming) {
      try {
        const response = await fetch("/api/rag/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody),
        });

        if (!response.ok || !response.body) {
          throw new Error("Streaming connection failed.");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let accumulatedText = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split("\n");

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const dataStr = line.replace("data: ", "").trim();
              if (dataStr === "[DONE]") break;
              try {
                const parsed = JSON.parse(dataStr);
                if (parsed.token) {
                  // Accumulating streamed SSE tokens into a local `let`
                  // inside this async reader loop — plain imperative code
                  // in an event-driven async function, not a render body.
                  // eslint-disable-next-line react-hooks/immutability
                  accumulatedText += parsed.token;
                } else if (parsed.answer) {
                  accumulatedText = parsed.answer;
                }
                setRagMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === aiMsgId
                      ? {
                          ...msg,
                          text: accumulatedText,
                          // The stream "done" score is not a verified measurement
                          // unless the backend marks verified === true.
                          grounding_score: parsed.verified === true ? cleanScore(parsed.grounding_score) ?? msg.grounding_score : msg.grounding_score,
                          verified: typeof parsed.verified === "boolean" ? parsed.verified : msg.verified,
                          verification_reason: typeof parsed.verification_reason === "string" ? parsed.verification_reason : msg.verification_reason,
                          sources: parsed.sources || msg.sources,
                          citations: parsed.citations || parsed.sources ? normalizeCitations(parsed.citations, parsed.sources) : msg.citations,
                          model_used: typeof parsed.model_used === "string" ? parsed.model_used : msg.model_used,
                          verification_flags: parsed.verification_flags || msg.verification_flags,
                        }
                      : msg
                  )
                );
              } catch {
                // Raw token string fallback
                if (dataStr && !dataStr.startsWith("{")) {
                  accumulatedText += dataStr;
                  setRagMessages((prev) =>
                    prev.map((msg) => (msg.id === aiMsgId ? { ...msg, text: accumulatedText } : msg))
                  );
                }
              }
            }
          }
        }

        setRagMessages((prev) =>
          prev.map((msg) =>
            msg.id === aiMsgId ? { ...msg, isStreaming: false } : msg
          )
        );
      } catch {
        // Stream fallback to standard endpoint
        fetchStandardRagQuery(requestBody, aiMsgId);
      } finally {
        setRagLoading(false);
      }
    } else {
      fetchStandardRagQuery(requestBody, aiMsgId);
    }
  };

  // Maps a /query response (current or future shape) onto a message patch.
  // Scores are only kept when the backend actually reports them.
  const mapQueryResponse = (data: Record<string, unknown>): Partial<RagMessage> => {
    const flags = Array.isArray(data.verification_flags) ? (data.verification_flags as string[]) : [];
    const guardFlag = flags.find((f) => f.startsWith("GUARDRAIL_TRIGGERED"));
    const blocked = data.blocked === true || !!guardFlag;
    const verified = typeof data.verified === "boolean" ? data.verified : null;
    return {
      text: String(data.answer ?? ""),
      sources: data.sources as RagSource[] | undefined,
      citations: normalizeCitations(data.citations as Citation[] | undefined, data.sources as RagSource[] | undefined),
      chunks_retrieved: data.chunks_retrieved as number | undefined,
      grounding_score: blocked ? undefined : cleanScore(data.grounding_score) ?? undefined,
      hallucination_score: blocked ? undefined : cleanScore(data.hallucination_score) ?? undefined,
      verified,
      verification_reason:
        typeof data.verification_reason === "string" ? data.verification_reason : typeof data.reason === "string" ? data.reason : undefined,
      verification_flags: flags,
      blocked,
      block_reason:
        (typeof data.block_reason === "string" && data.block_reason) ||
        guardFlag?.replace(/^GUARDRAIL_TRIGGERED:?\s*/, "") ||
        undefined,
      model_used: typeof data.model_used === "string" ? data.model_used : undefined,
    };
  };

  const runDeepResearch = async (question: string, aiMsgId: string) => {
    try {
      const res = await fetch("/api/rag/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json().catch(() => ({}));
      setRagMessages((prev) =>
        prev.map((msg) =>
          msg.id !== aiMsgId
            ? msg
            : res.ok
            ? { ...msg, researchPending: false, isStreaming: false, research: data as ResearchData, text: (data as ResearchData).synthesized_answer }
            : {
                ...msg,
                researchPending: false,
                isStreaming: false,
                isError: true,
                text:
                  res.status === 429
                    ? "Deep research is rate limited (5 per minute). Wait a moment and try again."
                    : res.status === 504
                    ? "Deep research timed out. Try a narrower question."
                    : extractErrorMessage(data, "Deep research failed."),
              }
        )
      );
    } catch {
      setRagMessages((prev) =>
        prev.map((msg) =>
          msg.id === aiMsgId ? { ...msg, researchPending: false, isStreaming: false, isError: true, text: "Cannot reach the research service." } : msg
        )
      );
    } finally {
      setRagLoading(false);
    }
  };

  const fetchStandardRagQuery = async (requestBody: Record<string, unknown>, aiMsgId: string) => {
    try {
      const res = await fetch("/api/rag/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      const data = await res.json();
      setRagMessages((prev) =>
        prev.map((msg) =>
          msg.id === aiMsgId
            ? {
                ...msg,
                ...(res.ok ? mapQueryResponse(data) : { text: extractErrorMessage(data, "An error occurred.") }),
                isStreaming: false,
                isError: !res.ok,
              }
            : msg
        )
      );
    } catch {
      setRagMessages((prev) =>
        prev.map((msg) =>
          msg.id === aiMsgId
            ? {
                ...msg,
                text: "❌ Cannot reach the RAG server. Make sure rag_server.py is running on port 8000.",
                isStreaming: false,
                isError: true,
              }
            : msg
        )
      );
    } finally {
      setRagLoading(false);
    }
  };

  const toggleSourceExpand = (msgId: string) => {
    setExpandedSources((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  // Start new AI conversation
  const handleNewChat = () => {
    setActiveThreadId(null);
    setMessages([]);
    setInputPrompt("");
  };

  // Submit AI Prompt (ChatGPT / Gemini style)
  const handleSendPrompt = async (promptText?: string) => {
    const textToSend = (promptText || inputPrompt).trim();
    if (!textToSend || isGenerating) return;

    // See the matching comment in handleRagQuery above — Date.now() calls
    // in this block all generate ids / measure elapsed time inside this
    // event handler, not during render.
    const userMsg: AiMessage = {
      // eslint-disable-next-line react-hooks/purity
      id: `user-${Date.now()}`,
      sender: "user",
      text: textToSend,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setMessages((prev) => [...prev, userMsg]);
    if (!promptText) setInputPrompt("");
    setIsGenerating(true);

    // Dynamic thread title if new
    if (!activeThreadId) {
      // eslint-disable-next-line react-hooks/purity
      const newThreadId = `t-${Date.now()}`;
      const newThreadTitle = textToSend.slice(0, 32) + (textToSend.length > 32 ? "…" : "");
      setAiThreads((prev) => [{ id: newThreadId, title: newThreadTitle, date: "Today", count: 1 }, ...prev]);
      setActiveThreadId(newThreadId);
    }

    // eslint-disable-next-line react-hooks/purity
    const startTime = Date.now();
    try {
      const res = await fetch("/api/rag/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: textToSend, top_k: 4, history: messages }),
      });
      const data = await res.json();
      // eslint-disable-next-line react-hooks/purity
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);

      const aiMsg: AiMessage = {
        // eslint-disable-next-line react-hooks/purity
        id: `ai-${Date.now()}`,
        sender: "ai",
        modelName: "VenuAI backend",
        reasoningTime: `${elapsed}s`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        text: res.ok ? data.answer : extractErrorMessage(data, "Error connecting to AI backend."),
        thumbs: null,
      };

      setMessages((prev) => [...prev, aiMsg]);
    } catch {
      setMessages((prev) => [...prev, {
        id: `ai-err-${Date.now()}`,
        sender: "ai",
        modelName: "VenuAI backend",
        reasoningTime: "0.01s",
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        text: "❌ Unable to connect to backend server. Ensure backend server is running.",
        thumbs: null,
      }]);
    } finally {
      setIsGenerating(false);
    }
  };

  // Copy code / text helper
  const handleCopyText = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Thumbs reaction toggle
  const handleToggleThumbs = (id: string, dir: "up" | "down") => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, thumbs: m.thumbs === dir ? null : dir } : m))
    );
  };

  if (!mounted || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center transition-colors duration-300">
        <div className="flex flex-col items-center gap-4">
          <div
            className="w-12 h-12 rounded-2xl flex items-center justify-center shadow-lg"
            style={{ background: "var(--primary-gradient)" }}
          >
            <Bot className="w-6 h-6 text-white spin-smooth" />
          </div>
          <p className="text-sm font-medium" style={{ color: "var(--text-muted)" }}>Connecting to VenuAI Agent…</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) return null;


  return (
    <div
      className="flex h-screen overflow-hidden transition-colors duration-300"
      style={{ color: "var(--text-main)" }}
    >
      {/* ── Left Sidebar (ChatGPT / Gemini / Team / DRDO RAG) ── */}
      <aside
        className="w-64 flex flex-col border-r shrink-0 transition-colors duration-300"
        style={{ borderColor: "var(--border-subtle)", background: "var(--bg-panel)" }}
      >
        {/* Workspace & Mode Switcher Header */}
        <div className="p-3.5 border-b space-y-3" style={{ borderColor: "var(--border-subtle)" }}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center overflow-hidden bg-slate-950 border border-cyan-500/40 shadow-sm shrink-0">
                <img src="/drdo-logo.png" alt="DRDO Official Logo" className="w-8 h-8 object-contain" />
              </div>
              <div>
                <span className="text-sm font-black tracking-tight text-cyan-400 block uppercase">DRDO VenuAI</span>
                <span className="block text-[9px] font-bold tracking-wider text-slate-400 uppercase">Defence R&D Engine</span>
              </div>
            </div>
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" title="DRDO Network Active" />
          </div>

          {/* Mode Switcher Buttons — 4 tabs */}
          <div
            className="flex items-center gap-1 p-1 rounded-xl"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)" }}
          >
            <button
              type="button"
              onClick={() => setWorkspaceMode("ai_agent")}
              className={`flex-1 flex items-center justify-center gap-1 py-1.5 px-1.5 rounded-lg text-[10px] font-bold transition-all ${
                workspaceMode === "ai_agent" ? "shadow-sm" : "opacity-70 hover:opacity-100"
              }`}
              style={{
                background: workspaceMode === "ai_agent" ? "var(--bg-card)" : "transparent",
                color: workspaceMode === "ai_agent" ? "var(--text-main)" : "var(--text-muted)",
                border: workspaceMode === "ai_agent" ? "1px solid var(--border-subtle)" : "1px solid transparent",
              }}
            >
              <Bot className="w-3 h-3" />
              <span>Agent</span>
            </button>

            <button
              type="button"
              onClick={() => setWorkspaceMode("team_chat")}
              className={`flex-1 flex items-center justify-center gap-1 py-1.5 px-1.5 rounded-lg text-[10px] font-bold transition-all ${
                workspaceMode === "team_chat" ? "shadow-sm" : "opacity-70 hover:opacity-100"
              }`}
              style={{
                background: workspaceMode === "team_chat" ? "var(--bg-card)" : "transparent",
                color: workspaceMode === "team_chat" ? "var(--text-main)" : "var(--text-muted)",
                border: workspaceMode === "team_chat" ? "1px solid var(--border-subtle)" : "1px solid transparent",
              }}
            >
              <Users className="w-3 h-3" />
              <span>Team</span>
            </button>

            <button
              type="button"
              onClick={() => setWorkspaceMode("drdo_rag")}
              className={`flex-1 flex items-center justify-center gap-1 py-1.5 px-1.5 rounded-lg text-[10px] font-bold transition-all ${
                workspaceMode === "drdo_rag" ? "shadow-sm" : "opacity-70 hover:opacity-100"
              }`}
              style={{
                background: workspaceMode === "drdo_rag" ? "var(--bg-card)" : "transparent",
                color: workspaceMode === "drdo_rag" ? "var(--primary-accent)" : "var(--text-muted)",
                border: workspaceMode === "drdo_rag" ? "1px solid var(--badge-border)" : "1px solid transparent",
              }}
            >
              <Shield className="w-3 h-3" />
              <span>DRDO</span>
            </button>

            <button
              type="button"
              onClick={() => setWorkspaceMode("data_analyst")}
              className={`flex-1 flex items-center justify-center gap-1 py-1.5 px-1.5 rounded-lg text-[10px] font-bold transition-all ${
                workspaceMode === "data_analyst" ? "shadow-sm" : "opacity-70 hover:opacity-100"
              }`}
              style={{
                background: workspaceMode === "data_analyst" ? "var(--bg-card)" : "transparent",
                color: workspaceMode === "data_analyst" ? "var(--text-main)" : "var(--text-muted)",
                border: workspaceMode === "data_analyst" ? "1px solid var(--border-subtle)" : "1px solid transparent",
              }}
            >
              <BarChart3 className="w-3 h-3" />
              <span>Data</span>
            </button>
          </div>
        </div>

        {/* AI Agent Sidebar Content */}
        {workspaceMode === "ai_agent" ? (
          <div className="flex-1 flex flex-col min-h-0">
            {/* New Chat Button (ChatGPT / Gemini style) */}
            <div className="p-3">
              <button
                type="button"
                onClick={handleNewChat}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-3 rounded-xl text-xs font-bold transition-all duration-150 border group hover:scale-[1.01]"
                style={{
                  background: "var(--bg-card)",
                  borderColor: "var(--border-subtle)",
                  color: "var(--text-main)",
                }}
              >
                <Plus className="w-4 h-4" />
                <span>New AI Conversation</span>
              </button>
            </div>

            {/* Conversation Threads List */}
            <div className="flex-1 overflow-y-auto px-3 space-y-4 pb-4">
              <div>
                <span className="text-[10px] font-bold uppercase tracking-wider block px-2 mb-2" style={{ color: "var(--text-subtle)" }}>
                  Recent AI Threads
                </span>
                <div className="space-y-1">
                  {aiThreads.map((thread) => {
                    const isActive = activeThreadId === thread.id;
                    return (
                      <button
                        key={thread.id}
                        onClick={() => setActiveThreadId(thread.id)}
                        className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium transition-all ${
                          isActive ? "shadow-sm" : "hover:bg-slate-500/5"
                        }`}
                        style={{
                          background: isActive ? "var(--badge-bg)" : "transparent",
                          color: isActive ? "var(--primary-accent)" : "var(--text-muted)",
                          border: isActive ? "1px solid var(--badge-border)" : "1px solid transparent",
                        }}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <MessageCircle className="w-3.5 h-3.5 shrink-0" />
                          <span className="truncate text-left font-medium">{thread.title}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* AI Capabilities Cards */}
              <div className="p-3 rounded-2xl decent-card space-y-2">
                <div className="flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5" style={{ color: "var(--text-main)" }} />
                  <span className="text-[11px] font-bold" style={{ color: "var(--text-main)" }}>Agent Capabilities</span>
                </div>
                <div className="space-y-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
                  <p>• Sub-10ms Realtime Inference</p>
                  <p>• TypeScript &amp; Next.js 16 Expert</p>
                  <p>• Theme Token Integration</p>
                </div>
              </div>
            </div>
          </div>
        ) : workspaceMode === "team_chat" ? (
          /* Team Chat Channels Sidebar */
          <div className="flex-1 overflow-y-auto px-2 space-y-1 pb-4 pt-2">
            <div className="px-2 pt-2 pb-1">
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-subtle)" }}>Channels</span>
            </div>
            {CHANNELS.map((ch) => (
              <button
                key={ch.id}
                onClick={() => setActiveChannel(ch.id)}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-xl text-xs font-medium transition-all"
                style={{
                  background: activeChannel === ch.id ? "var(--badge-bg)" : "transparent",
                  color: activeChannel === ch.id ? "var(--primary-accent)" : "var(--text-muted)",
                  border: activeChannel === ch.id ? "1px solid var(--badge-border)" : "1px solid transparent",
                }}
              >
                <Hash className="w-3.5 h-3.5 shrink-0" />
                <span className="flex-1 text-left truncate">{ch.name}</span>
                {ch.unread > 0 && (
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "var(--badge-border)", color: "var(--badge-text)" }}>
                    {ch.unread}
                  </span>
                )}
              </button>
            ))}

            <div className="px-2 pt-4 pb-1">
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-subtle)" }}>Direct Messages</span>
            </div>
            {DMS.map((dm) => (
              <button
                key={dm.id}
                className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-xs hover:bg-slate-500/10 transition-all"
                style={{ color: "var(--text-muted)" }}
              >
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white shrink-0" style={{ background: dm.bg }}>
                  {dm.avatar}
                </div>
                <span className="flex-1 text-left truncate font-medium">{dm.name}</span>
              </button>
            ))}
          </div>
        ) : (
          /* ── DRDO Knowledge Base RAG Sidebar ── */
          <div className="flex-1 flex flex-col min-h-0">
            {/* Upload Zone */}
            <div className="p-3 border-b" style={{ borderColor: "var(--border-subtle)" }}>
              <input
                ref={ragFileInputRef}
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={handleRagFileSelect}
              />
              <div
                id="rag-upload-zone"
                onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleRagDrop}
                onClick={() => !ragUploading && ragFileInputRef.current?.click()}
                className="relative flex flex-col items-center justify-center gap-2 p-4 rounded-xl border-2 border-dashed cursor-pointer transition-all duration-200"
                style={{
                  borderColor: isDragOver ? "var(--primary-accent)" : "var(--border-subtle)",
                  background: isDragOver ? "var(--badge-bg)" : "var(--bg-input)",
                }}
              >
                {ragUploading ? (
                  <>
                    <div className="w-6 h-6 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: "var(--primary-accent)", borderTopColor: "transparent" }} />
                    <span className="text-[10px] font-bold text-center" style={{ color: "var(--primary-accent)" }}>Processing PDF…</span>
                  </>
                ) : (
                  <>
                    <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: "var(--badge-bg)" }}>
                      <Upload className="w-4 h-4" style={{ color: "var(--primary-accent)" }} />
                    </div>
                    <div className="text-center">
                      <p className="text-[10px] font-bold" style={{ color: "var(--text-main)" }}>Drop PDF or click to upload</p>
                      <p className="text-[9px]" style={{ color: "var(--text-subtle)" }}>DRDO reports, research papers</p>
                    </div>
                  </>
                )}
              </div>
              {/* Upload Status */}
              {ragUploadProgress && (
                <p className="text-[10px] mt-2 px-1 font-medium leading-relaxed" style={{ color: "var(--text-muted)" }}>
                  {ragUploadProgress}
                </p>
              )}
            </div>

            {/* Document Library */}
            <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-subtle)" }}>Document Library</span>
                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "var(--badge-bg)", color: "var(--badge-text)" }}>
                  {ragDocuments.length} docs
                </span>
              </div>

              {ragDocuments.length === 0 ? (
                <div className="text-center py-6">
                  <Database className="w-6 h-6 mx-auto mb-2" style={{ color: "var(--text-subtle)" }} />
                  <p className="text-[10px]" style={{ color: "var(--text-subtle)" }}>No documents yet.<br />Upload a DRDO PDF above.</p>
                </div>
              ) : (
                ragDocuments.map((doc) => (
                  <div
                    key={doc.id}
                    className="flex items-start gap-2 p-2.5 rounded-xl border group transition-all"
                    style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
                  >
                    <FileText className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--primary-accent)" }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-[10px] font-bold truncate" style={{ color: "var(--text-main)" }}>{doc.filename}</p>
                      <p className="text-[9px]" style={{ color: "var(--text-subtle)" }}>{doc.pages}pp · {doc.chunks} chunks · {doc.size_kb}KB</p>
                    </div>
                    <button
                      onClick={() => handleRagDelete(doc.id)}
                      className="opacity-0 group-hover:opacity-100 p-1 rounded-lg hover:text-red-500 transition-all"
                      style={{ color: "var(--text-subtle)" }}
                      title="Remove document"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* User Account Profile at Sidebar Bottom */}
        <div
          className="flex items-center gap-2.5 px-3.5 py-3 border-t"
          style={{ borderColor: "var(--border-subtle)", background: "var(--bg-input)" }}
        >
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0 shadow-sm"
            style={{ background: "var(--primary-gradient)" }}
          >
            {user?.email ? user.email.slice(0, 2).toUpperCase() : "DU"}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-bold truncate" style={{ color: "var(--text-main)" }}>{user?.email?.split("@")[0]}</p>
            <span className="text-[10px] font-medium block truncate" style={{ color: "var(--text-subtle)" }}>Pro Member</span>
          </div>
          <button
            onClick={handleLogout}
            className="p-1.5 rounded-lg hover:text-red-500 transition-colors"
            style={{ color: "var(--text-muted)" }}
            title="Sign out"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </aside>

      {/* ── Main Workspace Area ── */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Header Bar */}
        <div
          className="flex items-center justify-between px-6 py-3.5 border-b shrink-0 transition-colors duration-300"
          style={{ borderColor: "var(--border-subtle)", background: "var(--bg-panel)" }}
        >
          {workspaceMode === "ai_agent" ? (
            <ModelRouting models={registryModels} />
          ) : workspaceMode === "team_chat" ? (
            /* Team Channel Title */
            <div className="flex items-center gap-2">
              <Hash className="w-4 h-4" style={{ color: "var(--text-main)" }} />
              <span className="font-bold text-sm tracking-tight" style={{ color: "var(--text-main)" }}>#{activeChannel}</span>
            </div>
          ) : (
            /* DRDO RAG Header */
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center overflow-hidden border shadow-sm shrink-0" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
                <img src="/drdo-logo.png" alt="DRDO Emblem" className="w-8 h-8 object-contain" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-black text-sm tracking-tight uppercase" style={{ color: "var(--text-main)" }}>DEFENCE RESEARCH &amp; DEVELOPMENT ORGANISATION</span>
                  <span className="text-[9px] px-2 py-0.5 rounded font-extrabold uppercase border" style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--badge-text)" }}>Govt. of India</span>
                </div>
                <div className="flex items-center gap-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
                  <span className="font-bold" style={{ color: "var(--primary-accent)" }}>बलस्य मूलं विज्ञानम्</span>
                  <span>•</span>
                  <span>Ministry of Defence</span>
                  <span>•</span>
                  <span>{ragDocuments.length} custom doc{ragDocuments.length !== 1 ? "s" : ""} + Master KB</span>
                </div>
              </div>
            </div>
          )}

          {/* Right Header Actions */}
          <div className="flex items-center gap-3">
            {workspaceMode === "drdo_rag" && (
              <div className="flex items-center gap-2">
                <ModelRouting models={registryModels} />
                <button
                  type="button"
                  onClick={() => setDeepResearch((v) => !v)}
                  aria-pressed={deepResearch}
                  className="px-2.5 py-1.5 rounded-xl text-xs font-semibold border transition-all"
                  style={{
                    background: deepResearch ? "var(--badge-bg)" : "var(--bg-input)",
                    color: deepResearch ? "var(--text-main)" : "var(--text-muted)",
                    borderColor: deepResearch ? "var(--border-hover)" : "var(--border-subtle)",
                  }}
                  title="Deep Research: plan sub-questions, retrieve evidence for each, synthesize and verify. Can take up to ~2 minutes."
                >
                  Deep Research
                </button>
                <AgentModeToggle mode={agentMode} onChange={setAgentMode} disabled={deepResearch} />
                <button
                  type="button"
                  onClick={() => setUseStreaming((v) => !v)}
                  className="px-2.5 py-1.5 rounded-xl text-xs font-semibold border transition-all"
                  style={{
                    background: useStreaming ? "var(--badge-bg)" : "var(--bg-input)",
                    color: useStreaming ? "var(--text-main)" : "var(--text-muted)",
                    borderColor: useStreaming ? "var(--border-hover)" : "var(--border-subtle)"
                  }}
                  title="Toggle SSE Token Streaming mode"
                >
                  ⚡ {useStreaming ? "Stream SSE" : "Full Res"}
                </button>
              </div>
            )}

            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold" style={{ background: "var(--badge-bg)", border: "1px solid var(--badge-border)", color: "var(--badge-text)" }}>
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              <span>Sub-10ms Engine Active</span>
            </div>

            {/* Theme Toggle Button */}
            <ThemeToggle showLabel />
          </div>
        </div>

        {/* ── Main Chat Stream Content ── */}
        <div className={`flex-1 overflow-y-auto px-4 sm:px-8 py-6 space-y-6 ${workspaceMode === "drdo_rag" ? "drdo-radar-bg" : ""}`}>
          {workspaceMode === "data_analyst" ? (
            <DataAnalystWorkspace />
          ) : workspaceMode === "drdo_rag" ? (
            /* ── DRDO RAG Chat Interface ── */
            ragMessages.length === 0 ? (
              /* DRDO RAG High-Impact Tactical Hero Screen */
              <div className="h-full flex flex-col items-center justify-center max-w-2xl mx-auto text-center space-y-7 py-10 fade-in-up">
                
                {/* Glowing Official DRDO Emblem */}
                <div className="relative flex items-center justify-center">
                  <div className="absolute inset-0 rounded-full radar-pulse opacity-40 bg-amber-500/20 blur-xl" />
                  <div className="relative w-28 h-28 rounded-2xl flex items-center justify-center overflow-hidden border-2 shadow-2xl p-2" style={{ background: "var(--bg-card)", borderColor: "var(--border-hover)" }}>
                    <img src="/drdo-logo.png" alt="DRDO Official Emblem" className="w-full h-full object-contain drop-shadow-xl" />
                  </div>
                </div>

                {/* Hero Title & Security Badge */}
                <div className="space-y-2">
                  <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border shadow-sm" style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)", color: "var(--badge-text)" }}>
                    <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_#10b981]" />
                    <span>DEFENCE R&amp;D NETWORK • OFFICIAL KNOWLEDGE ENGINE</span>
                  </div>

                  <h1 className="text-2xl sm:text-4xl font-black tracking-tight" style={{ color: "var(--text-main)" }}>
                    DRDO Master R&amp;D Intelligence Engine
                  </h1>
                  <p className="text-xs font-bold tracking-wider" style={{ color: "var(--primary-accent)" }}>
                    बलस्य मूलं विज्ञानम् — Strength&apos;s Origin is in Science
                  </p>
                  
                  <p className="text-xs sm:text-sm max-w-lg mx-auto font-medium leading-relaxed pt-1" style={{ color: "var(--text-muted)" }}>
                    {ragDocuments.length === 0
                      ? "Preloaded with 50+ official DRDO Laboratories (DRDL, RCI, ASL, LRDE, ADE, CVRDE), Missile Programs, Radars & Defense Platforms. Ask questions directly or drop PDFs to expand the vault."
                      : `${ragDocuments.length} custom document${ragDocuments.length > 1 ? "s" : ""} indexed (+ Preloaded DRDO Master Knowledge Base).`}
                  </p>
                </div>

                {/* Quick Prompts — Tactical HUD Cards with Light Beam Sweeps */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5 w-full text-left pt-2">
                  {DRDO_QUICK_PROMPTS.map((card, i) => {
                    const Icon = card.icon;
                    const tagNames = ["[LABS DIRECTORY]", "[SPEC SHEET]", "[RADAR TECH]", "[YOUNG SCIENTISTS]"];
                    return (
                      <button
                        key={i}
                        type="button"
                        onClick={() => handleRagQuery(card.prompt)}
                        className="p-4 rounded-2xl decent-card-interactive text-left transition-all duration-200 border group"
                        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
                      >
                        <div className="flex items-center justify-between mb-2">
                          <div className="w-7 h-7 rounded-xl flex items-center justify-center border shadow-xs" style={{ background: "var(--badge-bg)", borderColor: "var(--badge-border)" }}>
                            <Icon className="w-4 h-4" style={{ color: "var(--primary-accent)" }} />
                          </div>
                          <span className="text-[9px] font-extrabold px-2 py-0.5 rounded-md uppercase tracking-wider border shimmer-badge" style={{ borderColor: "var(--badge-border)", color: "var(--badge-text)" }}>
                            {tagNames[i]}
                          </span>
                        </div>
                        <p className="text-xs font-bold group-hover:underline mb-1" style={{ color: "var(--text-main)" }}>
                          {card.prompt}
                        </p>
                        <p className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
                          {card.desc}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              /* DRDO RAG Active Messages */
              <div className="max-w-3xl mx-auto space-y-6">
                {ragMessages.map((msg) => (
                  <div key={msg.id} className="space-y-3 fade-in-up">
                    {msg.sender === "user" ? (
                      /* User bubble */
                      <div className="flex justify-end">
                        <div
                          className="max-w-xl px-4 py-3 rounded-2xl text-sm font-medium shadow-sm"
                          style={{
                            background: "var(--badge-bg)",
                            border: "1px solid var(--badge-border)",
                            color: "var(--text-main)",
                          }}
                        >
                          {msg.text}
                        </div>
                      </div>
                    ) : (
                      /* AI RAG Response */
                      <div className="flex items-start gap-3.5">
                        <div className="w-9 h-9 rounded-xl flex items-center justify-center overflow-hidden border border-cyan-500/40 bg-slate-950 shrink-0 mt-0.5 shadow-md">
                          <img src="/drdo-logo.png" alt="DRDO Agent Logo" className="w-8 h-8 object-contain" />
                        </div>

                        <div className="flex-1 min-w-0 space-y-2.5">
                          {/* RAG Meta Header */}
                          <div className="flex items-center gap-2 flex-wrap justify-between">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-xs font-bold" style={{ color: "var(--text-main)" }}>DRDO RAG Agent</span>
                              {!msg.isError && !msg.blocked && !msg.researchPending && !msg.research && (
                                <ConfidenceBadge
                                  score={msg.grounding_score}
                                  verified={msg.verified}
                                  reason={msg.verification_reason}
                                  hallucinationScore={msg.hallucination_score}
                                />
                              )}
                              {msg.model_used && (
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}>
                                  {msg.model_used}
                                </span>
                              )}
                              <span className="text-[10px]" style={{ color: "var(--text-subtle)" }}>{msg.timestamp}</span>
                            </div>
                          </div>

                          {msg.researchPending ? (
                            <ResearchPending question={msg.question || ""} />
                          ) : msg.research ? (
                            <ResearchReport data={msg.research} />
                          ) : msg.isError ? (
                            <p className="text-sm font-medium text-red-500 break-words">{msg.text}</p>
                          ) : msg.blocked ? (
                            <div className="p-3 rounded-xl border text-sm space-y-1" style={{ borderColor: "rgba(244,63,94,0.4)", background: "rgba(244,63,94,0.08)" }}>
                              <div className="flex items-center gap-1.5 font-bold text-rose-500">
                                <ShieldAlert className="w-4 h-4" /> Blocked by guardrails
                              </div>
                              <p className="break-words" style={{ color: "var(--text-main)" }}>{msg.text}</p>
                              {msg.block_reason && <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Reason: {msg.block_reason}</p>}
                            </div>
                          ) : (
                            <div className="print-scope space-y-2.5">
                              <StreamingMessage content={msg.text} isStreaming={msg.isStreaming} citations={msg.citations} />
                              {!msg.isStreaming && msg.text && (
                                <>
                                  <VerificationPanel
                                    groundingScore={msg.grounding_score}
                                    hallucinationScore={msg.hallucination_score}
                                    verified={msg.verified}
                                    reason={msg.verification_reason}
                                    verificationFlags={msg.verification_flags}
                                    citations={msg.citations}
                                    modelUsed={msg.model_used}
                                    model={msg.agent_mode === "multi_agent" ? "Multi-Agent Swarm" : msg.agent_mode === "react" ? "ReAct Tool Agent" : "Direct RAG Engine"}
                                  />
                                  <AnswerActions
                                    markdown={answerToMarkdown({
                                      question: ragMessages[ragMessages.findIndex((m) => m.id === msg.id) - 1]?.text,
                                      answer: msg.text,
                                      citations: msg.citations,
                                      footer: [
                                        typeof msg.grounding_score === "number" && msg.verified !== false
                                          ? `Grounding score: ${Math.round(msg.grounding_score * 100)}%`
                                          : "Grounding: unverified",
                                      ],
                                    })}
                                    title={ragMessages[ragMessages.findIndex((m) => m.id === msg.id) - 1]?.text || "answer"}
                                  />
                                </>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}

                {/* RAG Generating Spinner */}
                {ragLoading && (
                  <div className="flex items-center gap-3 fade-in-up">
                    <div className="w-8 h-8 rounded-xl flex items-center justify-center text-white shrink-0 shadow-sm" style={{ background: "linear-gradient(135deg, #1e3a5f, #0f6cbd)" }}>
                      <Shield className="w-4 h-4 spin-smooth" />
                    </div>
                    <div className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-muted)" }}>
                      <div className="w-2 h-2 rounded-full bg-blue-500 animate-ping" />
                      <span>Searching knowledge base & generating answer…</span>
                    </div>
                  </div>
                )}
                <div ref={ragMessagesEndRef} />
              </div>
            )
          ) : workspaceMode === "ai_agent" ? (
            messages.length === 0 ? (
              /* ChatGPT / Gemini Style Empty Hero Screen */
              <div className="h-full flex flex-col items-center justify-center max-w-2xl mx-auto text-center space-y-6 py-12 fade-in-up">
                <div
                  className="w-16 h-16 rounded-3xl flex items-center justify-center text-white shadow-xl glow-card"
                  style={{ background: "var(--primary-gradient)" }}
                >
                  <Bot className="w-8 h-8" />
                </div>

                <div className="space-y-2">
                  <h1 className="text-2xl sm:text-3xl font-black tracking-tight" style={{ color: "var(--text-main)" }}>
                    What can I help you build today?
                  </h1>
                  <p className="text-xs sm:text-sm max-w-md mx-auto" style={{ color: "var(--text-muted)" }}>
                    VenuAI is your intelligent copilot for code generation, architecture design, and real-time team workflow.
                  </p>
                </div>

                {/* Quick Suggestion Prompt Cards */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full text-left pt-4">
                  {[
                    {
                      icon: Zap,
                      prompt: "Explain sub-10ms WebSocket architecture",
                      desc: "Realtime zero-copy buffer pooling",
                    },
                    {
                      icon: Sparkles,
                      prompt: "Design a dark mode monochrome UI component",
                      desc: "Tailwind CSS variables & tokens",
                    },
                    {
                      icon: Terminal,
                      prompt: "Audit React 19 Server Component state",
                      desc: "Check hydration & streaming performance",
                    },
                    {
                      icon: Shield,
                      prompt: "Verify E2EE security protocols",
                      desc: "Session key exchange & SOC2 compliance",
                    },
                  ].map((card, i) => {
                    const Icon = card.icon;
                    return (
                      <button
                        key={i}
                        type="button"
                        onClick={() => handleSendPrompt(card.prompt)}
                        className="p-4 rounded-2xl decent-card text-left transition-all duration-200 border group hover:scale-[1.01]"
                        style={{
                          background: "var(--bg-card)",
                          borderColor: "var(--border-subtle)",
                        }}
                      >
                        <div className="flex items-center gap-2 mb-1.5">
                          <Icon className="w-4 h-4" style={{ color: "var(--text-main)" }} />
                          <span className="text-xs font-bold group-hover:underline" style={{ color: "var(--text-main)" }}>
                            {card.prompt}
                          </span>
                        </div>
                        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          {card.desc}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              /* Active AI Stream Messages (ChatGPT / Gemini style) */
              <div className="max-w-3xl mx-auto space-y-6">
                {messages.map((msg) => (
                  <div key={msg.id} className="space-y-3 fade-in-up">
                    {msg.sender === "user" ? (
                      /* User Prompt Bubble */
                      <div className="flex justify-end">
                        <div
                          className="max-w-xl px-4 py-3 rounded-2xl text-sm font-medium shadow-sm"
                          style={{
                            background: "var(--badge-bg)",
                            border: "1px solid var(--badge-border)",
                            color: "var(--text-main)",
                          }}
                        >
                          {msg.text}
                        </div>
                      </div>
                    ) : (
                      /* AI Agent Response Bubble */
                      <div className="flex items-start gap-3.5">
                        <div
                          className="w-8 h-8 rounded-xl flex items-center justify-center text-white shrink-0 mt-0.5 shadow-sm"
                          style={{ background: "var(--primary-gradient)" }}
                        >
                          <Bot className="w-4.5 h-4.5" />
                        </div>

                        <div className="flex-1 min-w-0 space-y-2.5">
                          {/* AI Meta Header */}
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold" style={{ color: "var(--text-main)" }}>
                              {msg.modelName}
                            </span>
                            {msg.reasoningTime && (
                              <span className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-subtle)" }}>
                                ⚡ Reasoning: {msg.reasoningTime}
                              </span>
                            )}
                          </div>

                          {/* Message Body */}
                          <p className="text-sm leading-relaxed font-normal" style={{ color: "var(--text-main)" }}>
                            {msg.text}
                          </p>

                          {/* Code Snippet Formatting */}
                          {msg.codeSnippet && (
                            <div
                              className="rounded-xl p-4 overflow-x-auto text-xs font-mono border space-y-2"
                              style={{
                                background: "var(--bg-input)",
                                borderColor: "var(--border-subtle)",
                                color: "var(--text-main)",
                              }}
                            >
                              <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--border-subtle)" }}>
                                <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-subtle)" }}>
                                  {msg.codeSnippet.language}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => handleCopyText(msg.id, msg.codeSnippet!.code)}
                                  className="flex items-center gap-1 text-[10px] font-bold hover:opacity-80 transition-opacity"
                                  style={{ color: "var(--text-muted)" }}
                                >
                                  {copiedId === msg.id ? (
                                    <>
                                      <Check className="w-3 h-3 text-emerald-500" />
                                      <span>Copied</span>
                                    </>
                                  ) : (
                                    <>
                                      <Copy className="w-3 h-3" />
                                      <span>Copy Code</span>
                                    </>
                                  )}
                                </button>
                              </div>
                              <pre className="leading-relaxed">
                                <code>{msg.codeSnippet.code}</code>
                              </pre>
                            </div>
                          )}

                          {/* Interactive Action Bar (ChatGPT / Gemini style) */}
                          <div className="flex items-center gap-2 pt-1">
                            <button
                              type="button"
                              onClick={() => handleToggleThumbs(msg.id, "up")}
                              className={`p-1.5 rounded-lg border transition-all ${
                                msg.thumbs === "up" ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/30" : "hover:opacity-80"
                              }`}
                              style={{ borderColor: msg.thumbs === "up" ? undefined : "var(--border-subtle)", color: msg.thumbs === "up" ? undefined : "var(--text-subtle)" }}
                              title="Helpful"
                            >
                              <ThumbsUp className="w-3.5 h-3.5" />
                            </button>

                            <button
                              type="button"
                              onClick={() => handleToggleThumbs(msg.id, "down")}
                              className={`p-1.5 rounded-lg border transition-all ${
                                msg.thumbs === "down" ? "bg-red-500/10 text-red-500 border-red-500/30" : "hover:opacity-80"
                              }`}
                              style={{ borderColor: msg.thumbs === "down" ? undefined : "var(--border-subtle)", color: msg.thumbs === "down" ? undefined : "var(--text-subtle)" }}
                              title="Not helpful"
                            >
                              <ThumbsDown className="w-3.5 h-3.5" />
                            </button>

                            <button
                              type="button"
                              onClick={() => handleCopyText(msg.id, msg.text)}
                              className="p-1.5 rounded-lg border hover:opacity-80 transition-opacity"
                              style={{ borderColor: "var(--border-subtle)", color: "var(--text-subtle)" }}
                              title="Copy response"
                            >
                              {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}

                {/* AI Generating Spinner */}
                {isGenerating && (
                  <div className="flex items-center gap-3 fade-in-up">
                    <div className="w-8 h-8 rounded-xl flex items-center justify-center text-white shrink-0 shadow-sm" style={{ background: "var(--primary-gradient)" }}>
                      <Bot className="w-4 h-4 spin-smooth" />
                    </div>
                    <div className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium" style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-muted)" }}>
                      <div className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                      <span>VenuAI is processing prompt…</span>
                    </div>
                  </div>
                )}
              </div>
            )
          ) : (
            /* Team Messages Feed */
            <div className="max-w-3xl mx-auto space-y-4">
              {TEAM_MESSAGES.map((msg) => (
                <div key={msg.id} className="flex items-start gap-3 p-3.5 rounded-2xl decent-card">
                  <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0" style={{ background: msg.avatarBg }}>
                    {msg.avatar}
                  </div>
                  <div>
                    <div className="flex items-baseline gap-2">
                      <span className="text-xs font-bold" style={{ color: "var(--text-main)" }}>{msg.author}</span>
                      <span className="text-[10px]" style={{ color: "var(--text-subtle)" }}>{msg.time}</span>
                    </div>
                    <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{msg.text}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Prompt Input Bar — adapts to mode (DataAnalystWorkspace has its own input, so it's hidden for that mode) ── */}
        {workspaceMode !== "data_analyst" && (
        <div className="p-4 sm:p-6 shrink-0 max-w-4xl w-full mx-auto">
          <div className="rounded-3xl decent-card glow-card p-3 space-y-2">
            {workspaceMode === "drdo_rag" ? (
              /* DRDO RAG Input */
              <div className="flex items-center gap-3 px-2">
                <Shield className="w-4 h-4 shrink-0" style={{ color: "#4a9eff" }} />
                <input
                  id="rag-prompt-input"
                  type="text"
                  value={ragInput}
                  onChange={(e) => setRagInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleRagQuery(); }}
                  placeholder="Ask anything about DRDO labs, missiles, radars, platforms…"
                  disabled={ragLoading}
                  className="flex-1 bg-transparent text-sm outline-none font-medium"
                  style={{ color: "var(--text-main)" }}
                />
                <button
                  id="rag-send-btn"
                  type="button"
                  disabled={!ragInput.trim() || ragLoading}
                  onClick={() => handleRagQuery()}
                  className={`p-2.5 rounded-2xl transition-all duration-200 ${
                    ragInput.trim() && !ragLoading
                      ? "hover:scale-105 shadow-md"
                      : "opacity-40 cursor-not-allowed"
                  }`}
                  style={
                    ragInput.trim() && !ragLoading
                      ? { background: "linear-gradient(135deg, #1e3a5f, #0f6cbd)", color: "#fff" }
                      : { background: "var(--bg-input)", color: "var(--text-subtle)" }
                  }
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            ) : (
              /* AI Agent / Team Chat Input */
              <>
                {/* Quick Action Toggles */}
                <div className="flex items-center gap-2 px-2">
                  <button
                    type="button"
                    onClick={() => setWebSearchActive((v) => !v)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold transition-all border ${
                      webSearchActive ? "border-emerald-500/40" : ""
                    }`}
                    style={{
                      background: webSearchActive ? "var(--badge-bg)" : "transparent",
                      color: webSearchActive ? "var(--primary-accent)" : "var(--text-subtle)",
                      borderColor: webSearchActive ? "var(--badge-border)" : "var(--border-subtle)",
                    }}
                  >
                    <Globe className="w-3 h-3" />
                    <span>Web Search</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setDeepThinkActive((v) => !v)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold transition-all border ${
                      deepThinkActive ? "border-purple-500/40" : ""
                    }`}
                    style={{
                      background: deepThinkActive ? "var(--badge-bg)" : "transparent",
                      color: deepThinkActive ? "var(--primary-accent)" : "var(--text-subtle)",
                      borderColor: deepThinkActive ? "var(--badge-border)" : "var(--border-subtle)",
                    }}
                  >
                    <Brain className="w-3 h-3" />
                    <span>Deep Reason</span>
                  </button>
                </div>

                {/* Input Box */}
                <div className="flex items-center gap-3 px-2">
                  <button
                    type="button"
                    className="p-2 rounded-full hover:opacity-80 transition-opacity"
                    style={{ color: "var(--text-muted)" }}
                    title="Attach files"
                  >
                    <Paperclip className="w-4 h-4" />
                  </button>

                  <input
                    id="ai-prompt-input"
                    type="text"
                    value={inputPrompt}
                    onChange={(e) => setInputPrompt(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") handleSendPrompt(); }}
                    placeholder={workspaceMode === "ai_agent" ? "Ask VenuAI anything…" : `Message #${activeChannel}…`}
                    className="flex-1 bg-transparent text-sm outline-none font-medium"
                    style={{ color: "var(--text-main)" }}
                  />

                  <button
                    type="button"
                    className="p-2 rounded-full hover:opacity-80 transition-opacity"
                    style={{ color: "var(--text-muted)" }}
                    title="Voice prompt"
                  >
                    <Mic className="w-4 h-4" />
                  </button>

                  <button
                    id="ai-send-btn"
                    type="button"
                    disabled={!inputPrompt.trim() || isGenerating}
                    onClick={() => handleSendPrompt()}
                    className={`p-2.5 rounded-2xl transition-all duration-200 ${
                      inputPrompt.trim() && !isGenerating
                        ? "hover:scale-105 shadow-md"
                        : "opacity-40 cursor-not-allowed"
                    }`}
                    style={
                      inputPrompt.trim() && !isGenerating
                        ? { background: "var(--primary-gradient)", color: "var(--btn-text)" }
                        : { background: "var(--bg-input)", color: "var(--text-subtle)" }
                    }
                  >
                    <Send className="w-4 h-4" />
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
