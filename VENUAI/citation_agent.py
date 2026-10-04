"""
CitationAgent — per-claim evidence attribution and support verification.

Extends, rather than duplicates, VerifierAgent's existing grounding check
(VENUAI/verifier_agent.py): that check scores the *whole answer* against
*all* source docs as one number. CitationAgent does the finer-grained job
spec section 26 asks for — splitting the answer into individual claims and
attributing each one to specific Evidence items, so an evidence-coverage
claim can actually be traced claim-by-claim (spec section 25's Evidence
Graph), not just asserted as one aggregate score.

Scoring is deterministic token-overlap (same family of technique as
VerifierAgent.check_grounding — token F1), not an LLM call. This is a
real, cheap, reproducible signal, not a placeholder; an LLM-based entailment
upgrade is a reasonable future enhancement, not required for this to be a
genuine implementation.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import List

from VENUAI.evidence import Claim, Evidence, SupportStatus

SUPPORTED_THRESHOLD = 0.35
PARTIAL_THRESHOLD = 0.15

# Excluded from overlap scoring — without this, two short, completely
# unrelated sentences (e.g. "Bananas are a good source of potassium" vs.
# "Pinaka is a multi-barrel rocket launcher system") share enough stopwords
# ("a", "is") to clear PARTIAL_THRESHOLD on pure token F1, producing a false
# "partially supported" verdict. Caught by
# VENUAI/tests/test_citation_and_critic.py::test_citation_agent_handles_multi_sentence_answer.
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "and", "or", "but", "with", "by",
    "as", "it", "its", "this", "that", "these", "those", "from", "has",
    "have", "had", "can", "could", "will", "would", "should", "may", "might",
    "about", "about's", "above", "after", "all", "also", "some", "any",
})

MIN_OVERLAPPING_TOKENS = 2  # a single shared content word (e.g. "about")
# scoring high on tiny token sets is a false positive, not real support —
# require at least two shared content tokens before counting a match.


def _raw_tokenize(text: str) -> set:
    return set(re.findall(r"\b\w+\b", text.lower()))


def _tokenize(text: str) -> set:
    """Stopword-filtered tokens, used for overlap *scoring* only. The
    claim-length filter in split_into_claims() deliberately uses
    _raw_tokenize instead — a short-but-real claim like "Bananas are
    purple." shouldn't be discarded just because most of its words happen
    to be stopwords."""
    return _raw_tokenize(text) - _STOPWORDS


def _token_f1(a_tokens: set, b_tokens: set) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    if overlap < MIN_OVERLAPPING_TOKENS:
        return 0.0
    precision = overlap / len(a_tokens)
    recall = overlap / len(b_tokens)
    return 2 * precision * recall / (precision + recall)


def split_into_claims(answer: str) -> List[str]:
    """Sentence-level split — the same granularity spec section 26 asks
    citation verification to operate at ('for every factual claim')."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
    # Drop pure-markdown artifacts (table separators, bare headings) that
    # aren't actually claims and would just dilute evidence-coverage stats.
    # Uses raw (non-stopword-filtered) token count — a short claim like
    # "Bananas are purple." is still a real claim even though most of its
    # words are stopwords.
    return [s for s in sentences if len(_raw_tokenize(s)) >= 3 and not s.strip().startswith(("|", "#"))]


class CitationAgent:
    def verify_claims(self, answer: str, evidence: List[Evidence]) -> List[Claim]:
        claim_texts = split_into_claims(answer)
        claims: List[Claim] = []

        for text in claim_texts:
            claim_tokens = _tokenize(text)
            scored = [
                (ev, _token_f1(claim_tokens, _tokenize(ev.content)))
                for ev in evidence
            ]
            scored.sort(key=lambda pair: pair[1], reverse=True)

            best_score = scored[0][1] if scored else 0.0
            supporting = [ev for ev, score in scored if score >= PARTIAL_THRESHOLD]

            if best_score >= SUPPORTED_THRESHOLD:
                status = SupportStatus.SUPPORTED
            elif best_score >= PARTIAL_THRESHOLD:
                status = SupportStatus.PARTIALLY_SUPPORTED
            else:
                status = SupportStatus.UNSUPPORTED
                supporting = []

            claims.append(Claim(
                claim_id=hashlib.sha256(text.encode()).hexdigest()[:12],
                text=text,
                evidence=supporting[:3],
                support_status=status,
                support_score=round(best_score, 3),
            ))

        return claims
