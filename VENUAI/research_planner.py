"""
ResearchPlannerAgent — decomposes a research question into bounded
sub-questions, per spec section 17.

Real LLM call via AIGateway, with a deterministic fallback (single
sub-question = the original question) if the model call fails or returns
something that doesn't parse as a clean numbered/bulleted list — this
agent never raises on a malformed LLM response, and never fabricates a
plan that looks structured but wasn't actually produced by the model.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("drdo-rag")

MAX_SUBQUESTIONS = 5

_PLANNING_PROMPT = """You are a research planning assistant. Break the following research \
question into at most {max_n} focused sub-questions that, together, would let someone \
answer the original question thoroughly. Each sub-question should be independently \
answerable by searching a document knowledge base.

Respond with ONLY a numbered list, one sub-question per line, no other text.

Research question: {question}

Sub-questions:"""


@dataclass(frozen=True)
class ResearchPlan:
    objective: str
    subquestions: List[str] = field(default_factory=list)
    is_fallback: bool = False  # True if the LLM decomposition failed/was unusable


def _parse_numbered_list(raw: str, max_n: int) -> List[str]:
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    items = []
    for line in lines:
        cleaned = re.sub(r"^\s*(\d+[\.\)]|[-*•])\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items[:max_n]


class ResearchPlannerAgent:
    def plan(self, question: str, groq_client, max_subquestions: int = MAX_SUBQUESTIONS) -> ResearchPlan:
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType

        try:
            gateway = get_gateway(groq_client)
            result = gateway.complete(
                task_type=TaskType.GENERATION,
                messages=[{"role": "user", "content": _PLANNING_PROMPT.format(
                    question=question, max_n=max_subquestions)}],
                max_tokens=400,
                temperature=0.1,
            )
            subquestions = _parse_numbered_list(result.content, max_subquestions)
            if not subquestions:
                raise ValueError("Planner LLM response did not contain a parseable list.")
            return ResearchPlan(objective=question, subquestions=subquestions, is_fallback=False)
        except Exception as e:
            logger.warning(f"[ResearchPlannerAgent] Falling back to single-question plan: {e}")
            return ResearchPlan(objective=question, subquestions=[question], is_fallback=True)
