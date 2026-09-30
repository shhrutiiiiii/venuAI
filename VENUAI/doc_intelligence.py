"""
DRDO VenuAI — Document Intelligence (Auto-Tagger)
====================================================
Automatically extracts metadata from uploaded documents to improve
multi-agent routing, filtered search, and knowledge organisation.

Uses regex + TF-IDF (sklearn) for fast, local extraction — no LLM call.

Usage:
    di = DocumentIntelligence()
    tags = di.analyze(full_text, "missile_propulsion_report.pdf")
    agents = di.suggest_agents(tags)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("drdo-rag")

# ── DRDO entity patterns ────────────────────────────────────────────────────────

_LAB_CODE_PATTERN = re.compile(
    r"\b(DRDL|RCI|ASL|LRDE|DLRL|DEAL|ADE|GTRE|CABS|CVRDE|ARDE|HEMRL|"
    r"NPOL|NSTL|DMRL|DEBEL|DFRL|DIPAS|DYSL-AI|DYSL-QT|DYSL-CT|DYSL-SM|DYSL-AT)\b"
)

_WEAPON_PATTERN = re.compile(
    r"\b(Agni|Prithvi|Akash|BrahMos|Pinaka|Tejas|LCA|Uttam|Astra|Varunastra|"
    r"Arjun|TAPAS|Ghatak|Kaveri|ATAGS|INSAS|Helina|Nag|Rustom|Netra|HUMSA|Swathi)\b"
)

_DOMAIN_KEYWORDS: dict[str, List[str]] = {
    "missiles":     ["missile", "ballistic", "rocket", "propellant", "guidance", "seeker",
                     "ICBM", "IRBM", "hypersonic", "trajectory", "warhead"],
    "electronics":  ["radar", "AESA", "electronic", "warfare", "sonar", "signal", "frequency",
                     "EW", "SIGINT", "COMINT", "communication", "antenna", "transceiver"],
    "aeronautics":  ["aircraft", "UAV", "drone", "turbine", "aeroengine", "fighter", "rotary",
                     "airborne", "wing", "avionics", "flight control"],
    "armaments":    ["gun", "artillery", "explosive", "ammunition", "rocket launcher",
                     "howitzer", "mortar", "rifle", "grenade", "propulsion", "calibre"],
    "naval":        ["torpedo", "sonar", "submarine", "warship", "mine", "underwater",
                     "oceanographic", "hydrodynamics", "stealth"],
    "materials":    ["alloy", "composite", "titanium", "ceramic", "polymer", "metallurgy",
                     "armour", "steel", "carbon fibre", "crystal"],
    "life_sciences":["physiology", "bioengineering", "ration", "food", "medicine", "altitude",
                     "oxygen", "life support", "ergonomics"],
    "ai_quantum":   ["artificial intelligence", "machine learning", "quantum", "neural network",
                     "computer vision", "deep learning", "cryptography"],
    "weapons":      ["weapon", "platform", "system", "combat", "defence", "operational"],
}

_CLASSIFICATION_SIGNALS = {
    "RESTRICTED": ["restricted", "sensitive", "limited distribution", "for official use"],
    "PUBLIC":     ["public", "press release", "open source", "unclassified", "available"],
}


# ── DocumentTags dataclass ──────────────────────────────────────────────────────

@dataclass
class DocumentTags:
    """Structured metadata extracted from a DRDO document."""
    domain_tags:          List[str] = field(default_factory=list)
    drdo_labs:            List[str] = field(default_factory=list)
    technology_areas:     List[str] = field(default_factory=list)
    classification_level: str = "UNKNOWN"
    key_entities:         List[str] = field(default_factory=list)
    summary:              str = ""

    def to_dict(self) -> dict:
        return {
            "domain_tags":          self.domain_tags,
            "drdo_labs":            self.drdo_labs,
            "technology_areas":     self.technology_areas,
            "classification_level": self.classification_level,
            "key_entities":         self.key_entities,
            "summary":              self.summary,
        }


# ── DocumentIntelligence ────────────────────────────────────────────────────────

class DocumentIntelligence:
    """
    Extracts structured metadata tags from a document's raw text.
    Designed for fast, CPU-only execution during PDF upload.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, text: str, filename: str) -> DocumentTags:
        """
        Extract domain tags, lab codes, technology areas, classification level,
        key entities, and an auto-summary from document text.

        Args:
            text     : Full raw text extracted from the PDF.
            filename : Original filename (used as a hint for domain classification).

        Returns:
            A populated DocumentTags dataclass.
        """
        tags = DocumentTags()
        text_lower = text.lower()

        # 1. Lab codes
        tags.drdo_labs = list(set(_LAB_CODE_PATTERN.findall(text)))[:10]

        # 2. Key entities (weapons + lab codes merged)
        weapon_hits = list(set(_WEAPON_PATTERN.findall(text)))
        tags.key_entities = (weapon_hits + tags.drdo_labs)[:15]

        # 3. Domain classification via keyword counting
        domain_scores: dict[str, int] = {}
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            score = sum(text_lower.count(kw.lower()) for kw in keywords)
            if score > 0:
                domain_scores[domain] = score

        # Sort by score descending, take top 3 domains
        tags.domain_tags = sorted(domain_scores, key=domain_scores.get, reverse=True)[:3]

        # 4. Technology areas — derive from domain tags + filename
        tags.technology_areas = self._infer_technology_areas(text_lower, filename)

        # 5. Classification level
        tags.classification_level = self._detect_classification(text_lower)

        # 6. TF-IDF auto-summary (top sentences by keyword density)
        tags.summary = self._extractive_summary(text)

        logger.info(
            f"[DocumentIntelligence] Analyzed '{filename}': "
            f"domains={tags.domain_tags}, labs={tags.drdo_labs[:3]}, "
            f"classification={tags.classification_level}"
        )
        return tags

    def suggest_agents(self, tags: DocumentTags) -> List[str]:
        """
        Suggest which specialist agents are best suited for this document.

        Args:
            tags: Output of analyze().

        Returns:
            List of agent names from ['technical', 'labs', 'research', 'general'].
        """
        agents = set()

        domain_to_agent = {
            "missiles":     "technical",
            "electronics":  "technical",
            "aeronautics":  "technical",
            "armaments":    "technical",
            "naval":        "technical",
            "materials":    "technical",
            "life_sciences":"research",
            "ai_quantum":   "research",
            "weapons":      "technical",
        }

        for domain in tags.domain_tags:
            if domain in domain_to_agent:
                agents.add(domain_to_agent[domain])

        if tags.drdo_labs:
            agents.add("labs")

        if not agents:
            agents.add("general")

        return list(agents)

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _infer_technology_areas(text_lower: str, filename: str) -> List[str]:
        """Map domain keywords to human-readable technology area names."""
        tech_areas = []
        tech_map = {
            "radar":          "Radar & Sensing",
            "missile":        "Missile Systems",
            "propellant":     "Propulsion",
            "drone":          "Unmanned Aerial Vehicles",
            "uav":            "Unmanned Aerial Vehicles",
            "torpedo":        "Underwater Weapons",
            "sonar":          "Sonar & Acoustics",
            "alloy":          "Advanced Materials",
            "composite":      "Composite Materials",
            "quantum":        "Quantum Technologies",
            "machine learning":"Artificial Intelligence",
            "encryption":     "Cyber & Cryptography",
            "artillery":      "Artillery Systems",
            "tank":           "Combat Vehicles",
            "aircraft":       "Aeronautical Systems",
        }
        for kw, area in tech_map.items():
            if kw in text_lower or kw in filename.lower():
                if area not in tech_areas:
                    tech_areas.append(area)
        return tech_areas[:5]

    @staticmethod
    def _detect_classification(text_lower: str) -> str:
        """Detect document classification level from text signals."""
        for level, signals in _CLASSIFICATION_SIGNALS.items():
            if any(sig in text_lower for sig in signals):
                return level
        return "UNKNOWN"

    @staticmethod
    def _extractive_summary(text: str) -> str:
        """
        Generate a 2-3 sentence summary using TF-IDF sentence scoring.
        Falls back to first 3 sentences if sklearn is unavailable.
        """
        # Split into sentences
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 30]
        if not sentences:
            return text[:300]

        if len(sentences) <= 3:
            return " ".join(sentences)

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            import numpy as np

            vec = TfidfVectorizer(stop_words="english", max_features=50)
            tfidf_matrix = vec.fit_transform(sentences[:80])  # limit for speed
            scores = tfidf_matrix.sum(axis=1).A1
            top_indices = np.argsort(scores)[-3:][::-1]
            top_indices_sorted = sorted(top_indices)
            return " ".join(sentences[i] for i in top_indices_sorted)

        except ImportError:
            # sklearn not available — use first + last + middle sentence
            mid = len(sentences) // 2
            return " ".join([sentences[0], sentences[mid], sentences[-1]])
        except Exception as e:
            logger.debug(f"[DocumentIntelligence] TF-IDF summary error: {e}")
            return " ".join(sentences[:3])
