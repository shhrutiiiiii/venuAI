"""
DRDO VenuAI — Hallucination Detector
=======================================
Detects and annotates factual claims in AI responses that are NOT
supported by the retrieved evidence using NLI (Natural Language Inference).

Pipeline:
    analyze(answer, source_docs)
        → extract_claims()       (sentence + heuristic filtering)
        → verify_claim()         (NLI model or token-F1 fallback)
        → compute_hallucination_score()
        → annotate_answer()
        → (annotated_answer, hallucination_score, List[Claim])

Primary NLI model : cross-encoder/nli-deberta-v3-small  (fast, CPU-friendly)
Fallback          : token-overlap F1 (no model required)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

# ── Claim dataclass ─────────────────────────────────────────────────────────────

@dataclass
class Claim:
    """A single factual claim extracted from an AI answer."""
    text: str              # the claim sentence
    verdict: str           # "SUPPORTED" | "UNSUPPORTED" | "UNCERTAIN"
    evidence: str          # excerpt of the supporting chunk (empty if unsupported)
    confidence: float      # 0.0 → 1.0 (NLI entailment probability)


# ── HallucinationDetector ───────────────────────────────────────────────────────

class HallucinationDetector:
    """
    Detects hallucinations by verifying each factual claim in an AI response
    against the source documents retrieved during the RAG pipeline.

    Primary model  : cross-encoder/nli-deberta-v3-small (lazy-loaded)
    Fallback       : token-overlap F1 (works without any ML model)
    """

    # NLI score thresholds
    _SUPPORTED_THRESHOLD  = 0.70
    _UNCERTAIN_THRESHOLD  = 0.40

    # Min words for a sentence to be treated as a factual claim
    _MIN_CLAIM_WORDS = 8

    def __init__(self, use_nli_model: bool = True) -> None:
        """
        Args:
            use_nli_model: If True, attempt to lazy-load the DeBERTa NLI model.
                           If False (or if the model fails to load), fall back
                           to token-overlap F1 scoring.
        """
        self._use_nli_model = use_nli_model
        self._nli_model = None   # lazy-loaded on first verify_claim() call
        self._nli_loaded = False

    # ── Lazy model loader ─────────────────────────────────────────────────────

    def _load_nli_model(self) -> bool:
        """
        Attempt to load the cross-encoder NLI model.
        Returns True on success, False if the model is unavailable.
        """
        if self._nli_loaded:
            return self._nli_model is not None
        self._nli_loaded = True
        if not self._use_nli_model:
            logger.info("[HallucinationDetector] NLI model disabled; using F1 fallback.")
            return False
        try:
            from sentence_transformers import CrossEncoder
            logger.info("[HallucinationDetector] Loading cross-encoder/nli-deberta-v3-small …")
            self._nli_model = CrossEncoder(
                "cross-encoder/nli-deberta-v3-small",
                max_length=512,
            )
            logger.info("[HallucinationDetector] NLI model loaded ✓")
            return True
        except Exception as e:
            logger.warning(
                f"[HallucinationDetector] NLI model load failed ({e}). "
                "Falling back to token-overlap F1."
            )
            return False

    # ── Claim extraction ──────────────────────────────────────────────────────

    def extract_claims(self, answer: str) -> List[str]:
        """
        Split the answer into sentences and filter to retain only likely
        factual claims using these heuristics:
          • ≥ MIN_CLAIM_WORDS words
          • Contains a number, measurement, proper noun, OR a DRDO entity code
          • Is declarative (does not end with '?' and does not start with an imperative)
          • Does not begin with disclaimer phrases ("Note:", "⚠️", etc.)

        Args:
            answer: The full AI-generated response string.

        Returns:
            List of claim sentence strings.
        """
        # Try spaCy sentence segmentation; fall back to regex split
        try:
            import spacy
            nlp = spacy.load("en_core_web_sm", disable=["ner", "tagger", "parser"])
            nlp.enable_pipe("senter")
            doc = nlp(answer)
            raw_sentences = [sent.text.strip() for sent in doc.sents]
        except Exception:
            raw_sentences = re.split(r"(?<=[.!?])\s+", answer)

        _DISCLAIMER_STARTS = ("⚠️", "note:", "ℹ️", "source:", "*source", "---")
        _IMPERATIVE_STARTS = ("please", "note that", "see ", "refer ")

        _HAS_FACTUAL_MARKER = re.compile(
            r"(\d[\d,\.]*|"               # numbers
            r"km|kg|GHz|MHz|kN|MW|ms|"   # units
            r"DRDO|ARDE|DRDL|RCI|ASL|LRDE|DLRL|DEAL|"
            r"ADE|GTRE|CABS|CVRDE|HEMRL|NPOL|NSTL|DMRL|"
            r"Agni|Prithvi|Akash|BrahMos|Pinaka|Tejas|Astra|Arjun)",
            re.IGNORECASE,
        )

        claims = []
        for sent in raw_sentences:
            s = sent.strip()
            if len(s.split()) < self._MIN_CLAIM_WORDS:
                continue
            if s.endswith("?"):
                continue
            lower = s.lower()
            if any(lower.startswith(d) for d in _DISCLAIMER_STARTS):
                continue
            if any(lower.startswith(i) for i in _IMPERATIVE_STARTS):
                continue
            if _HAS_FACTUAL_MARKER.search(s):
                claims.append(s)

        logger.debug(f"[HallucinationDetector] Extracted {len(claims)} claims from answer.")
        return claims

    # ── Claim verification ────────────────────────────────────────────────────

    def verify_claim(
        self,
        claim: str,
        source_docs: List[Document],
    ) -> Claim:
        """
        Verify a single claim against the retrieved source documents.

        Uses the NLI cross-encoder if available; otherwise falls back to
        token-overlap F1 scoring.

        Thresholds:
          score > 0.70  → SUPPORTED
          0.40 < score  → UNCERTAIN
          score ≤ 0.40  → UNSUPPORTED

        Args:
            claim       : A factual sentence extracted from the AI answer.
            source_docs : The retrieved LangChain Documents.

        Returns:
            A populated Claim dataclass.
        """
        if not source_docs:
            return Claim(text=claim, verdict="UNSUPPORTED", evidence="", confidence=0.0)

        model_available = self._load_nli_model()
        best_score = 0.0
        best_chunk = ""

        if model_available and self._nli_model is not None:
            # NLI scoring
            for doc in source_docs:
                chunk = doc.page_content[:500]
                try:
                    # [entailment, neutral, contradiction] logits
                    scores = self._nli_model.predict(
                        [(chunk, claim)],
                        apply_softmax=True,
                    )
                    entailment_prob = float(scores[0][0])  # first class = entailment
                    if entailment_prob > best_score:
                        best_score = entailment_prob
                        best_chunk = chunk
                except Exception as e:
                    logger.debug(f"[HallucinationDetector] NLI predict error: {e}")
        else:
            # Token-overlap F1 fallback
            claim_tokens = set(re.findall(r"\b\w+\b", claim.lower()))
            for doc in source_docs:
                doc_tokens = set(re.findall(r"\b\w+\b", doc.page_content.lower()))
                if not claim_tokens or not doc_tokens:
                    continue
                common = claim_tokens & doc_tokens
                precision = len(common) / len(claim_tokens)
                recall = len(common) / len(doc_tokens)
                f1 = (2 * precision * recall / (precision + recall)
                      if (precision + recall) > 0 else 0.0)
                if f1 > best_score:
                    best_score = f1
                    best_chunk = doc.page_content[:200]

        # Apply thresholds
        if best_score > self._SUPPORTED_THRESHOLD:
            verdict = "SUPPORTED"
        elif best_score > self._UNCERTAIN_THRESHOLD:
            verdict = "UNCERTAIN"
        else:
            verdict = "UNSUPPORTED"
            best_chunk = ""

        return Claim(
            text=claim,
            verdict=verdict,
            evidence=best_chunk[:200] if best_chunk else "",
            confidence=round(best_score, 3),
        )

    # ── Hallucination score ───────────────────────────────────────────────────

    def compute_hallucination_score(self, claims: List[Claim]) -> float:
        """
        Compute the fraction of claims that are UNSUPPORTED.

        Returns:
            float in [0.0, 1.0].
            0.0 = no hallucination detected.
            1.0 = all claims are unsupported.
        """
        if not claims:
            return 0.0
        unsupported = sum(1 for c in claims if c.verdict == "UNSUPPORTED")
        return round(unsupported / len(claims), 3)

    # ── Answer annotation ─────────────────────────────────────────────────────

    def annotate_answer(self, answer: str, claims: List[Claim]) -> str:
        """
        Append an '[⚠️ Unverified]' marker to every UNSUPPORTED claim sentence
        found in the answer text.

        Args:
            answer : The original AI answer.
            claims : List of verified Claim objects.

        Returns:
            The answer with inline verification markers.
        """
        annotated = answer
        for claim in claims:
            if claim.verdict == "UNSUPPORTED":
                # Escape special regex chars in the claim text
                escaped = re.escape(claim.text)
                replacement = claim.text + " **[⚠️ Unverified]**"
                annotated = re.sub(escaped, replacement, annotated, count=1)
        return annotated

    # ── Full pipeline ─────────────────────────────────────────────────────────

    def analyze(
        self,
        answer: str,
        source_docs: List[Document],
    ) -> Tuple[str, float, List[Claim]]:
        """
        Full hallucination detection pipeline.

        Steps:
          1. extract_claims(answer)
          2. verify_claim(claim, source_docs) for each claim
          3. compute_hallucination_score(claims)
          4. annotate_answer(answer, claims)

        Args:
            answer      : The AI-generated response.
            source_docs : Retrieved documents used to generate the answer.

        Returns:
            Tuple of:
              (annotated_answer: str,
               hallucination_score: float,
               claims: List[Claim])
        """
        claims_text = self.extract_claims(answer)

        if not claims_text:
            logger.debug("[HallucinationDetector] No verifiable claims found.")
            return answer, 0.0, []

        verified_claims = [self.verify_claim(c, source_docs) for c in claims_text]
        score = self.compute_hallucination_score(verified_claims)
        annotated = self.annotate_answer(answer, verified_claims)

        logger.info(
            f"[HallucinationDetector] Score: {score:.2f} | "
            f"Claims: {len(verified_claims)} | "
            f"Unsupported: {sum(1 for c in verified_claims if c.verdict == 'UNSUPPORTED')}"
        )
        return annotated, score, verified_claims


# ── Module-level singleton ──────────────────────────────────────────────────────
_detector: Optional[HallucinationDetector] = None


def get_hallucination_detector() -> HallucinationDetector:
    """Return (or create) the global HallucinationDetector singleton."""
    global _detector
    if _detector is None:
        _detector = HallucinationDetector(use_nli_model=True)
    return _detector
