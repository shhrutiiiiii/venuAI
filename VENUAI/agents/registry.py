"""
Agent Registry — every agent in VenuAI declares itself here: what it does,
what tools/permissions it needs, and its execution limits.

This registers the agents that genuinely exist and run today. As new agents
are implemented in this phase (ResearchPlannerAgent, DeepResearchAgent,
DataAnalystAgent, CitationAgent, ContradictionAgent, CriticAgent), they are
added here in the same change that implements them — never pre-registered
as a placeholder for something not yet built. See Documents/AI/agents.md.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    name: str
    description: str
    version: str
    allowed_tools: List[str] = field(default_factory=list)
    required_permissions: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    max_steps: int = 1
    max_runtime_seconds: int = 60
    enabled: bool = True


_REGISTRY: dict[str, AgentDescriptor] = {}


def register(descriptor: AgentDescriptor) -> None:
    _REGISTRY[descriptor.agent_id] = descriptor


def get(agent_id: str) -> AgentDescriptor | None:
    return _REGISTRY.get(agent_id)


def all_agents() -> List[AgentDescriptor]:
    return list(_REGISTRY.values())


# ── Real, currently-running agents ───────────────────────────────────────────
# Specialist agents (VENUAI/agents/*_agent.py via BaseSpecialistAgent) do
# direct vector-store retrieval, not the ToolRegistry-mediated tool-calling
# path — so allowed_tools is empty for them; that's accurate, not an omission.
register(AgentDescriptor(
    agent_id="general_agent",
    name="GeneralAgent",
    description="Fallback/cross-domain DRDO specialist; no metadata filter.",
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=30,
))

register(AgentDescriptor(
    agent_id="technical_agent",
    name="TechnicalAgent",
    description="Weapons specs, engineering params, materials, propulsion, electronics.",
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=30,
))

register(AgentDescriptor(
    agent_id="labs_agent",
    name="LabsAgent",
    description="DRDO labs, clusters, locations, org structure.",
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=30,
))

register(AgentDescriptor(
    agent_id="research_specialist_agent",
    name="ResearchAgent (RAG specialist)",
    description=(
        "DRDO programme/publication RAG specialist — NOT the multi-step "
        "research planner/agent described in the full roadmap. Kept under a "
        "distinct registry id (research_specialist_agent, not research_agent) "
        "specifically to avoid conflating the two in documentation or code."
    ),
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=30,
))

register(AgentDescriptor(
    agent_id="orchestrator_agent",
    name="OrchestratorAgent",
    description="Classifies query intent and routes to/merges specialist agents.",
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=3,          # classify + up to N specialist calls + merge
    max_runtime_seconds=60,
))

register(AgentDescriptor(
    agent_id="verifier_agent",
    name="VerifierAgent",
    description=(
        "Heuristic grounding/policy/consistency checker run after generation. "
        "Makes no model calls itself (verified: no self.groq_client usage in "
        "VENUAI/verifier_agent.py) — purely rule-based, hence LOW risk."
    ),
    version="1.0.0",
    allowed_tools=[],
    required_permissions=[],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=10,
))

register(AgentDescriptor(
    agent_id="research_planner_agent",
    name="ResearchPlannerAgent",
    description="Decomposes a research question into bounded sub-questions. Falls back to a single-question plan if the LLM call fails or is unparseable.",
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=30,
))

register(AgentDescriptor(
    agent_id="deep_research_agent",
    name="DeepResearchAgent",
    description=(
        "Multi-step research: plans sub-questions, runs AgenticRAG per "
        "sub-question, synthesizes, then verifies the combined answer. "
        "NOT the same as research_specialist_agent (single-shot RAG)."
    ),
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.MEDIUM,   # multiple LLM calls + bounded retrieval loop per sub-question
    max_steps=5,                   # see research_planner.MAX_SUBQUESTIONS
    max_runtime_seconds=120,       # matches deep_research_agent.MAX_RUNTIME_SECONDS
))

register(AgentDescriptor(
    agent_id="vision_agent",
    name="VisionAgent",
    description=(
        "Answers questions about extracted table/image regions using their "
        "real extracted text (table text or OCR) via text reasoning — NOT "
        "actual visual/semantic interpretation, since no vision-capable "
        "model is configured (see Documents/AI/vision.md). Returns "
        "insufficient_visual_evidence=True rather than guessing when a "
        "question genuinely requires seeing the image."
    ),
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=20,
))

register(AgentDescriptor(
    agent_id="data_analyst_agent",
    name="DataAnalystAgent",
    description=(
        "Deterministic pandas-based tabular analysis (describe, correlation, "
        "missing values, duplicates, mean/sum, growth rate, groupby). LLM "
        "only interprets already-computed numbers, never computes them. "
        "No upload endpoint/UI wired to this yet — see Documents/future-additions.md."
    ),
    version="1.0.0",
    allowed_tools=[],
    required_permissions=["READ_DOCUMENTS"],
    risk_level=RiskLevel.LOW,
    max_steps=1,
    max_runtime_seconds=30,
))

register(AgentDescriptor(
    agent_id="react_agent",
    name="ReAct Agent (agent_mode=true)",
    description="LangGraph prebuilt ReAct agent using the identity-bound tools from build_agent_tools().",
    version="1.0.0",
    allowed_tools=[
        "drdo_kb_search", "drdo_calculator", "memory_recall",
        "document_summary", "web_search_drdo", "list_knowledge_base",
        "code_execution",
    ],
    required_permissions=["READ_DOCUMENTS", "WEB_SEARCH", "EXECUTE_CODE"],
    risk_level=RiskLevel.MEDIUM,
    max_steps=8,  # matches the existing recursion_limit=8 in rag_server.py
    max_runtime_seconds=120,
))
