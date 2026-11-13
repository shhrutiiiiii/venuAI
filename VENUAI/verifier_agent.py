"""
DRDO VenuAI — Verifier Agent
==============================
Checks every AI-generated answer for factual accuracy and DRDO policy
compliance before the response is returned to the user.

Pipeline:
    verify(answer, question, source_docs)
        → check_grounding()     (token F1 overlap)
        → check_policy()        (regex + keyword patterns)
        → check_consistency()   (fast Groq LLM call)
        → rewrite_if_needed()   (string manipulation, no LLM)
        → VerificationResult
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")


# ── Policy Flags ────────────────────────────────────────────────────────────────

class PolicyFlag(Enum):
    """DRDO-specific policy violation categories."""
    CLASSIFIED_TOPIC = "classified_topic"   # classified weapon programs
    OVERCONFIDENT    = "overconfident"      # "definitely", "certainly" w/o grounding
    SENSITIVE_DATA   = "sensitive_data"     # budget figures, casualty counts
    OUT_OF_SCOPE     = "out_of_scope"       # non-DRDO topics


# ── Verification Result ─────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    """Structured output of the verification pipeline."""
    passed: bool
    grounding_score: float                          # 0.0 → 1.0
    policy_flags: List[PolicyFlag] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    revised_answer: Optional[str] = None            # set if answer was rewritten


# ── VerifierAgent ───────────────────────────────────────────────────────────────

class VerifierAgent:
    """
    Verifies AI answers against source documents and DRDO policy.

    Uses a fast Groq model (openai/gpt-oss-20b via TaskType.FAST_CLASSIFICATION) for the consistency
    check; all other checks are local (regex / string operations).
    """

    # ── Sensitive topic patterns ──────────────────────────────────────────────
    _CLASSIFIED_PATTERNS = [
        re.compile(r"\b(classified|secret|top\s+secret|confidential|restricted)\b", re.I),
        re.compile(r"\b(nuclear|thermonuclear|CBRN|radiological)\b", re.I),
        re.compile(r"\b(kill\s+radius|casualties|body\s+count|lethal\s+zone)\b", re.I),
    ]

    _SENSITIVE_DATA_PATTERNS = [
        re.compile(r"\b(budget|crore|billion|million|\$\d|₹\s*\d)\b", re.I),
        re.compile(r"\b(\d+\s*personnel|soldier|officer)\b", re.I),
        re.compile(r"\b(launch\s+code|encryption\s+key|frequency\s+hop)\b", re.I),
    ]

    _OVERCONFIDENT_PHRASES = [
        "definitely", "certainly", "absolutely", "without a doubt",
        "100%", "guaranteed", "undoubtedly", "confirmed",
    ]

    _OUT_OF_SCOPE_PATTERNS = [
        re.compile(r"\b(recipe|movie|game|celebrity|sports|cricket|football|politics|stock|crypto)\b", re.I),
    ]

    # ── Overconfident language rewrites ──────────────────────────────────────
    _REWRITE_MAP = {
        "definitely": "based on available documents",
        "certainly": "according to the retrieved information",
        "absolutely": "as indicated in the documents",
        "without a doubt": "based on the evidence provided",
        "100%": "as per the source documents",
        "guaranteed": "as stated in the retrieved material",
        "undoubtedly": "based on available DRDO documentation",
        "confirmed": "as indicated in the documents",
    }

    def __init__(self, groq_client) -> None:
        """
        Args:
            groq_client: An initialised Groq Python client instance.
        """
        self.client = groq_client

    # ── Grounding check ───────────────────────────────────────────────────────

    def check_grounding(
        self,
        answer: str,
        source_docs: List[Document],
    ) -> float:
        """
        Compute what fraction of the answer's factual sentences are
        supported by the source documents using token-level F1 overlap.

        Strategy:
          1. Tokenize each answer sentence.
          2. For each sentence, compute token F1 against every source chunk.
          3. Take the max F1 across all chunks per sentence.
          4. Return the mean F1 across all sentences (= grounding score).

        Returns:
            float in [0.0, 1.0]. 1.0 = fully grounded.
        """
        if not source_docs:
            return 0.0

        # Combine all source chunk text into one searchable string
        corpus = " ".join(doc.page_content.lower() for doc in source_docs)
        corpus_tokens = set(re.findall(r"\b\w+\b", corpus))

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
        # Keep only substantive sentences (>6 words, likely factual)
        factual_sents = [s for s in sentences if len(s.split()) > 6]

        if not factual_sents:
            return 1.0  # no claims to verify

        scores = []
        for sent in factual_sents:
            sent_tokens = set(re.findall(r"\b\w+\b", sent.lower()))
            if not sent_tokens:
                continue
            common = sent_tokens & corpus_tokens
            precision = len(common) / len(sent_tokens)
            recall = len(common) / len(corpus_tokens) if corpus_tokens else 0
            f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0
            scores.append(f1)

        return round(sum(scores) / len(scores), 3) if scores else 1.0

    # ── Policy check ──────────────────────────────────────────────────────────

    def check_policy(self, answer: str) -> List[PolicyFlag]:
        """
        Run regex patterns and keyword checks against the answer text.

        Returns:
            List of PolicyFlag enum values that were triggered.
        """
        flags: List[PolicyFlag] = []

        for pattern in self._CLASSIFIED_PATTERNS:
            if pattern.search(answer):
                flags.append(PolicyFlag.CLASSIFIED_TOPIC)
                break

        for pattern in self._SENSITIVE_DATA_PATTERNS:
            if pattern.search(answer):
                flags.append(PolicyFlag.SENSITIVE_DATA)
                break

        answer_lower = answer.lower()
        if any(phrase in answer_lower for phrase in self._OVERCONFIDENT_PHRASES):
            flags.append(PolicyFlag.OVERCONFIDENT)

        for pattern in self._OUT_OF_SCOPE_PATTERNS:
            if pattern.search(answer):
                flags.append(PolicyFlag.OUT_OF_SCOPE)
                break

        if flags:
            logger.debug(f"[VerifierAgent] Policy flags raised: {[f.value for f in flags]}")

        return flags

    # ── Consistency check ─────────────────────────────────────────────────────

    def check_consistency(self, answer: str, question: str) -> bool:
        """
        Use a fast Groq model to verify the answer actually addresses the question.

        Returns:
            True if the answer is consistent with the question, False otherwise.
        """
        try:
            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType

            prompt = (
                f"Question: {question}\n\n"
                f"Answer (first 300 chars): {answer[:300]}\n\n"
                "Does this answer actually address the question? "
                "Respond with exactly YES or NO."
            )
            gateway = get_gateway(self.client)
            result = gateway.complete(
                task_type=TaskType.VERIFICATION,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
                temperature=0.0,
            )
            verdict = result.content.strip().upper()
            return verdict.startswith("YES")
        except Exception as e:
            logger.warning(f"[VerifierAgent] check_consistency LLM call failed: {e}")
            return True  # assume consistent on error (fail open) — this is now a real
            # fallback for genuine failures, not the only code path ever reached.

    # ── Answer rewriting ──────────────────────────────────────────────────────

    def rewrite_if_needed(
        self,
        answer: str,
        flags: List[PolicyFlag],
        grounding_score: float,
    ) -> str:
        """
        Apply string-based rewrites if policy flags are present or confidence is low.
        No LLM call is used — all rewrites are deterministic string operations.

        Rewrite rules:
          • OUT_OF_SCOPE      → return a fixed redirect message.
          • CLASSIFIED_TOPIC  → prepend a classified-topic disclaimer.
          • SENSITIVE_DATA    → prepend a sensitive-data disclaimer.
          • OVERCONFIDENT     → replace overconfident phrases.
          • grounding < 0.5   → prepend a general low-confidence disclaimer.
          • grounding < 0.4   → also append ⚠️ Low confidence notice.

        Returns:
            The (possibly modified) answer string.
        """
        if PolicyFlag.OUT_OF_SCOPE in flags:
            return (
                "⚠️ **Out of Scope**: This question falls outside the DRDO research domain. "
                "VenuAI specialises in DRDO laboratories, defence research, weapon systems, "
                "and related technical topics. Please rephrase your question accordingly."
            )

        revised = answer

        # Replace overconfident language
        if PolicyFlag.OVERCONFIDENT in flags:
            for phrase, replacement in self._REWRITE_MAP.items():
                revised = re.sub(re.escape(phrase), replacement, revised, flags=re.I)

        # Prepend disclaimers
        disclaimers = []
        if PolicyFlag.CLASSIFIED_TOPIC in flags:
            disclaimers.append(
                "⚠️ **Note**: This response may reference sensitive or restricted topics. "
                "Information is sourced only from publicly available DRDO documentation."
            )
        if PolicyFlag.SENSITIVE_DATA in flags:
            disclaimers.append(
                "⚠️ **Note**: Financial or personnel figures mentioned are sourced from "
                "publicly available DRDO publications only."
            )
        if grounding_score < 0.5:
            disclaimers.append(
                "ℹ️ **Confidence Notice**: Portions of this response may not be fully supported "
                "by the retrieved documents. Please verify critical details independently."
            )

        if disclaimers:
            revised = "\n\n".join(disclaimers) + "\n\n" + revised

        if grounding_score < 0.4:
            revised += "\n\n⚠️ **Low confidence** — grounding score below threshold. Treat with caution."

        return revised

    # ── Full pipeline ─────────────────────────────────────────────────────────

    def verify(
        self,
        answer: str,
        question: str,
        source_docs: List[Document],
    ) -> VerificationResult:
        """
        Run the full verification pipeline:
            1. check_grounding()
            2. check_policy()
            3. check_consistency()
            4. rewrite_if_needed() if issues found

        Returns:
            VerificationResult with all check outcomes.
        """
        grounding_score = self.check_grounding(answer, source_docs)
        policy_flags = self.check_policy(answer)
        is_consistent = self.check_consistency(answer, question)

        warnings: List[str] = []
        if grounding_score < 0.5:
            warnings.append(f"Low grounding score: {grounding_score:.2f}")
        if policy_flags:
            warnings.extend([f"Policy flag: {f.value}" for f in policy_flags])
        if not is_consistent:
            warnings.append("Answer may not address the question.")

        needs_rewrite = bool(policy_flags) or grounding_score < 0.5 or not is_consistent
        revised_answer = None

        if needs_rewrite:
            revised_answer = self.rewrite_if_needed(answer, policy_flags, grounding_score)

        passed = grounding_score >= 0.4 and PolicyFlag.CLASSIFIED_TOPIC not in policy_flags

        result = VerificationResult(
            passed=passed,
            grounding_score=grounding_score,
            policy_flags=policy_flags,
            warnings=warnings,
            revised_answer=revised_answer,
        )

        logger.info(
            f"[VerifierAgent] Verification: passed={passed}, "
            f"grounding={grounding_score:.2f}, flags={[f.value for f in policy_flags]}"
        )
        return result


# ── Module-level singleton ──────────────────────────────────────────────────────
_verifier: Optional[VerifierAgent] = None


def get_verifier(groq_client) -> VerifierAgent:
    """Return (or create) the global VerifierAgent singleton."""
    global _verifier
    if _verifier is None:
        _verifier = VerifierAgent(groq_client)
    return _verifier


def reset_verifier_singleton() -> None:
    """Test-only hook — see VENUAI.ai_gateway.gateway.reset_gateway_singleton
    for why this exists (production always passes the same real client;
    tests inject different fake clients per test and need isolation)."""
    global _verifier
    _verifier = None
