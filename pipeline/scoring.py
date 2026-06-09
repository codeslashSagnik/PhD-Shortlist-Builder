"""
pipeline/scoring.py  —  Stage 4: Scoring & Ranking

Takes verified candidates and scores each one across multiple dimensions.
Uses sentence-transformers locally (free, no API) for embedding-based
research overlap scoring.

Scoring dimensions:
  - Research overlap   (0.40) — cosine similarity of embeddings
  - Recency            (0.20) — papers from last 3 years
  - Open position      (0.25) — has a funded PhD vacancy
  - Citation signal    (0.05) — h-index proxy
  - Country quality    (0.10) — primary appointment in target country

Tier assignment:
  - Reach   → QS rank 1–100
  - Target  → QS rank 101–400
  - Safety  → QS rank 401+ or unranked

Input:  List[Dict] verified candidates + StudentSignal
Output: List[Dict] ranked candidates with score + tier attached
"""

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from models.student_signal import StudentSignal
from utils.logger import get_logger
from config.settings import (
    WEIGHT_RESEARCH_OVERLAP,
    WEIGHT_RECENCY,
    WEIGHT_OPEN_POSITION,
    WEIGHT_CITATION,
    WEIGHT_COUNTRY_QUALITY,
    RECENCY_YEARS,
    TIER_REACH_RANK_MAX,
    TIER_TARGET_RANK_MAX,
    QS_RANKINGS,
    MAX_CANDIDATES_RANKED,
)

log = get_logger(__name__)

_CURRENT_YEAR = datetime.now().year


# ─────────────────────────────────────────────────────────────────────────────
# Embedding setup  (sentence-transformers — local, free)
# ─────────────────────────────────────────────────────────────────────────────

_embedding_model = None

def _get_embedding_model():
    """Lazy-load the sentence-transformer model (downloads once, cached by HF)."""
    global _embedding_model
    if _embedding_model is None:
        log.info("Loading sentence-transformer model (first run may download ~90MB)...")
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            log.info("Sentence-transformer model loaded: all-MiniLM-L6-v2")
        except ImportError:
            log.error(
                "sentence-transformers not installed. Run: pip install sentence-transformers"
            )
            raise
    return _embedding_model


def _embed(texts: List[str]) -> np.ndarray:
    """Embed a list of texts. Returns (N, dim) array."""
    model = _get_embedding_model()
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return embeddings


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ─────────────────────────────────────────────────────────────────────────────
# Individual scoring functions
# ─────────────────────────────────────────────────────────────────────────────

def _build_candidate_text(candidate: Dict) -> str:
    """Concatenate all evidence text for a candidate into one string."""
    parts = []
    for ev in candidate.get("evidence", []):
        title = ev.get("title", "")
        abstract = ev.get("abstract", "")
        if title:
            parts.append(title)
        if abstract:
            parts.append(abstract)
    return " ".join(parts) if parts else candidate.get("name", "")


def _score_research_overlap(
    student_embedding: np.ndarray,
    candidate_text: str,
) -> float:
    """
    Compute cosine similarity between student embedding and candidate's evidence text.
    Returns score in [0, 1].
    """
    if not candidate_text.strip():
        return 0.0
    cand_embedding = _embed([candidate_text])[0]
    sim = _cosine_similarity(student_embedding, cand_embedding)
    # Cosine similarity can be negative — clamp to [0, 1]
    return max(0.0, min(1.0, sim))


def _score_recency(candidate: Dict) -> float:
    """
    Score based on whether the candidate has recent papers.
    Full score (1.0) if any paper is within RECENCY_YEARS.
    Partial score for older papers.
    Returns score in [0, 1].
    """
    years = [
        ev.get("year", 0)
        for ev in candidate.get("evidence", [])
        if ev.get("year", 0) > 0
    ]
    if not years:
        return 0.3  # no year info — partial credit
    most_recent = max(years)
    gap = _CURRENT_YEAR - most_recent
    if gap <= 0:
        return 1.0
    if gap <= RECENCY_YEARS:
        return 1.0 - (gap / RECENCY_YEARS) * 0.5   # linear decay, floor at 0.5
    return max(0.0, 1.0 - gap * 0.15)               # further decay for older work


def _score_open_position(candidate: Dict) -> float:
    """1.0 if has an open funded PhD vacancy, 0.0 otherwise."""
    return 1.0 if candidate.get("open_position", False) else 0.0


def _score_citation(candidate: Dict) -> float:
    """
    Normalise h-index to [0, 1] using a soft cap at h=80.
    h=0  → 0.0
    h=20 → 0.25
    h=40 → 0.5
    h=80 → 1.0
    """
    h = candidate.get("h_index", 0) or 0
    return min(1.0, h / 80.0)


def _score_country_quality(candidate: Dict, target_countries: List[str]) -> float:
    """1.0 if primary country is in target list, 0.5 if uncertain, 0.0 otherwise."""
    country = candidate.get("country", "")
    if country in target_countries:
        return 1.0
    if not country:
        return 0.5
    return 0.0


def _get_qs_rank(candidate: Dict) -> Optional[int]:
    """Look up QS rank for a candidate's institution. Returns None if not found."""
    institution = candidate.get("institution", "")
    if not institution:
        return None
    # Exact match first
    if institution in QS_RANKINGS:
        return QS_RANKINGS[institution]
    # Partial match (e.g. "University of Edinburgh" vs "The University of Edinburgh")
    inst_lower = institution.lower()
    for ranked_inst, rank in QS_RANKINGS.items():
        if ranked_inst.lower() in inst_lower or inst_lower in ranked_inst.lower():
            return rank
    return None


def _assign_tier(qs_rank: Optional[int], score: float) -> str:
    """
    Assign reach / target / safety tier.
    Uses QS rank primarily, score as tiebreaker for unranked.
    """
    if qs_rank is None:
        # Unranked — use score to estimate
        if score >= 0.75:
            return "target"
        return "safety"
    if qs_rank <= TIER_REACH_RANK_MAX:
        return "reach"
    if qs_rank <= TIER_TARGET_RANK_MAX:
        return "target"
    return "safety"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 Main
# ─────────────────────────────────────────────────────────────────────────────

def score_and_rank(
    candidates: List[Dict[str, Any]],
    signal: StudentSignal,
) -> List[Dict[str, Any]]:
    """
    Stage 4 entry point.

    Computes a weighted composite score for each candidate,
    assigns tier, and returns the list sorted by score descending.

    Args:
        candidates: Verified candidates from Stage 3.
        signal:     StudentSignal from Stage 1.

    Returns:
        Sorted list with score, tier, and score_breakdown attached.
    """
    log.info("=" * 60)
    log.info("STAGE 4 — Scoring & Ranking")
    log.info("=" * 60)
    log.info("Input: %d verified candidates", len(candidates))

    # ── Embed student profile once ─────────────────────────────────────────────
    log.info("Embedding student profile...")
    student_embedding = _embed([signal.embedding_text])[0]
    log.info("Student embedding shape: %s", student_embedding.shape)

    target_countries = signal.constraints.countries
    scored = []

    for i, candidate in enumerate(candidates):
        name = candidate.get("name", "Unknown")

        # Build candidate evidence text for embedding
        cand_text = _build_candidate_text(candidate)

        # Individual dimension scores
        s_research = _score_research_overlap(student_embedding, cand_text)
        s_recency = _score_recency(candidate)
        s_position = _score_open_position(candidate)
        s_citation = _score_citation(candidate)
        s_country = _score_country_quality(candidate, target_countries)

        # Weighted composite score
        composite = (
            WEIGHT_RESEARCH_OVERLAP * s_research
            + WEIGHT_RECENCY        * s_recency
            + WEIGHT_OPEN_POSITION  * s_position
            + WEIGHT_CITATION       * s_citation
            + WEIGHT_COUNTRY_QUALITY * s_country
        )

        # QS rank + tier
        qs_rank = _get_qs_rank(candidate)
        tier = _assign_tier(qs_rank, composite)

        candidate["score"] = round(composite, 4)
        candidate["tier"] = tier
        candidate["qs_rank"] = qs_rank
        candidate["score_breakdown"] = {
            "research_overlap": round(s_research, 4),
            "recency":          round(s_recency, 4),
            "open_position":    round(s_position, 4),
            "citation":         round(s_citation, 4),
            "country_quality":  round(s_country, 4),
        }

        scored.append(candidate)

        if (i + 1) % 50 == 0:
            log.info("  Scored %d / %d candidates", i + 1, len(candidates))

    # Sort by composite score descending
    scored.sort(key=lambda c: c["score"], reverse=True)

    # Cap output
    if len(scored) > MAX_CANDIDATES_RANKED:
        scored = scored[:MAX_CANDIDATES_RANKED]

    log.info("Stage 4 | Scoring complete. Top 5 candidates:")
    for rank, c in enumerate(scored[:5], 1):
        log.info(
            "  #%d  %-40s  score=%.4f  tier=%-8s  qs=%s",
            rank, c.get("name", "?")[:40], c["score"], c["tier"], c.get("qs_rank", "?"),
        )

    # Log tier distribution
    tiers = {"reach": 0, "target": 0, "safety": 0}
    for c in scored:
        tiers[c.get("tier", "safety")] = tiers.get(c.get("tier", "safety"), 0) + 1
    log.info("Stage 4 | Tier distribution: %s", tiers)
    log.info("=" * 60)

    return scored
