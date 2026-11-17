"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Square, Loader2, Volume2 } from "lucide-react";
import type { TranscribeResponse, VoiceStatus } from "@/lib/apiContracts";

const MAX_RECORD_MS = 55_000; // backend limit is 60 s

/** null until known; both false when voice is not deployed / disabled (404/503). */
export function useVoiceStatus(): VoiceStatus | null {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/voice/status", { cache: "no-store" })
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) return setStatus({ stt: false, tts: false });
        const d = (await r.json().catch(() => ({}))) as Partial<VoiceStatus>;
        setStatus({ stt: d.stt === true, tts: d.tts === true });
      })
      .catch(() => alive && setStatus({ stt: false, tts: false }));
    return () => {
      alive = false;
    };
  }, []);
  return status;
}

function pickMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
}

/** Mic button: record, transcribe via /api/voice/transcribe, hand the text to `onTranscript`. Hidden unless STT is available. */
export function VoiceButton({ onTranscript, disabled }: { onTranscript: (text: string) => void; disabled?: boolean }) {
  const status = useVoiceStatus();
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cleanup = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const stop = useCallback(() => {
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") rec.stop();
  }, []);

  const start = async () => {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Recording is not supported in this browser.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = pickMime();
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
      rec.onstop = async () => {
        cleanup();
        const blob = new Blob(chunks, { type: rec.mimeType || mime || "audio/webm" });
        if (blob.size === 0) return setState("idle");
        setState("transcribing");
        try {
          const ext = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mp4") ? "m4a" : "webm";
          const form = new FormData();
          form.append("audio", blob, `recording.${ext}`);
          const r = await fetch("/api/voice/transcribe", { method: "POST", body: form });
          if (!r.ok) {
            setError(r.status === 429 ? "Too many voice requests, wait a moment." : r.status === 503 ? "Voice is disabled on this server." : "Transcription failed.");
          } else {
            const d = (await r.json()) as TranscribeResponse;
            if (d.text?.trim()) onTranscript(d.text.trim());
            else setError("No speech detected.");
          }
        } catch {
          setError("Transcription failed.");
        } finally {
          setState("idle");
        }
      };
      recorderRef.current = rec;
      rec.start();
      setState("recording");
      timerRef.current = setTimeout(stop, MAX_RECORD_MS);
    } catch {
      cleanup();
      setError("Microphone access was denied.");
      setState("idle");
    }
  };

  if (!status?.stt) {
    return (
      <button type="button" disabled aria-label="Voice question (disabled)" title="Voice is off on this server. Set VOICE_ENABLED=on (see Features)"
        className="p-2 rounded-full opacity-40 cursor-not-allowed" style={{ color: "var(--text-muted)" }}>
        <Mic className="w-4 h-4" />
      </button>
    );
  }
  const busy = state === "transcribing";
  return (
    <span className="relative inline-flex items-center no-print">
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => (state === "recording" ? stop() : start())}
        aria-label={state === "recording" ? "Stop recording" : "Record voice question"}
        aria-pressed={state === "recording"}
        className={`p-2 rounded-full hover:opacity-80 transition-opacity disabled:opacity-40 ${state === "recording" ? "text-rose-500" : ""}`}
        style={state === "recording" ? undefined : { color: "var(--text-muted)" }}
        title={state === "recording" ? "Stop and transcribe" : "Voice question"}
      >
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : state === "recording" ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
      </button>
      {error && (
        <span role="status" className="absolute bottom-full right-0 mb-1 px-2 py-1 rounded-lg border text-[10px] whitespace-nowrap" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-main)" }}>
          {error}
        </span>
      )}
    </span>
  );
}

/** Strip markdown / citation markers so TTS reads cleanly; the backend caps text at 1500 chars. */
export function textForSpeech(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\[(\d+)\]/g, "")
    .replace(/[#*_`>|]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 1500);
}

/** Play an answer via /api/voice/speak. Hidden unless TTS is available. */
export function SpeakButton({ text }: { text: string }) {
  const status = useVoiceStatus();
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [error, setError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      audioRef.current?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    []
  );

  if (!status?.tts) {
    return (
      <button type="button" disabled title="Voice is off on this server. Set VOICE_ENABLED=on (see Features)"
        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold no-print opacity-40 cursor-not-allowed"
        style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}>
        <Volume2 className="w-3 h-3" /> Listen
      </button>
    );
  }

  const toggle = async () => {
    if (state === "playing") {
      audioRef.current?.pause();
      return setState("idle");
    }
    setError(null);
    setState("loading");
    try {
      const r = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: textForSpeech(text) }) });
      if (!r.ok) throw new Error(String(r.status));
      const blob = await r.blob();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = URL.createObjectURL(blob);
      const audio = new Audio(urlRef.current);
      audioRef.current = audio;
      audio.onended = () => setState("idle");
      audio.onerror = () => {
        setError("Playback failed.");
        setState("idle");
      };
      await audio.play();
      setState("playing");
    } catch {
      setError("Could not generate speech.");
      setState("idle");
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={state === "loading"}
      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold hover:bg-slate-500/10 transition-colors no-print disabled:opacity-50"
      style={{ borderColor: "var(--border-subtle)", color: error ? "#f43f5e" : "var(--text-muted)" }}
    >
      {state === "loading" ? <Loader2 className="w-3 h-3 animate-spin" /> : state === "playing" ? <Square className="w-3 h-3" /> : <Volume2 className="w-3 h-3" />}
      {error ?? (state === "playing" ? "Stop" : "Listen")}
    </button>
  );
}

export default VoiceButton;
