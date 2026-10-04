"""
VENUAI AI Gateway — Phase 1 of the multi-model/multi-agent architecture.

This package consolidates what were previously three separate, duplicated
ad hoc Groq-calling code paths (VENUAI/rag_server.py's create_groq_completion,
VENUAI/agents/orchestrator.py's two direct calls, VENUAI/agents/base_agent.py's
candidate-model loop) into one gateway with a real model registry, router,
retry/fallback policy, and telemetry log.

What this Phase 1 covers (real, tested, integrated):
  - ModelRegistry   : what models actually exist and what each is good for
  - ModelRouter     : picks a model for a task type from the registry
  - AIGateway       : the single call surface agents use; owns retry/backoff/
                       fallback/circuit-breaking and telemetry
  - GroqProvider    : wraps the existing `groq` SDK client (already used
                       elsewhere in this codebase — not a new dependency)

What this Phase 1 explicitly does NOT cover yet (see
Documents/AI/model-routing.md "Not Yet Implemented"):
  - The LangChain/LangGraph-based call paths (generate_node, the ReAct agent,
    /query/stream) already go through LangChain's own ChatGroq abstraction —
    that is a real, working provider abstraction in its own right, and this
    gateway does not replace it in Phase 1. Routing those through this same
    gateway is a follow-up phase, not done here, to avoid rewriting
    currently-working generation/streaming code in the same change that
    introduces new infrastructure.
  - OpenAIProvider / LocalProvider are defined as interfaces only
    (ModelProvider ABC) — no working implementation exists because no
    OpenAI/local model credentials or runtime are configured in this
    environment. They are not fabricated; see the provider module for the
    explicit "not implemented" markers.
"""
