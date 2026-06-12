"""
config/settings.py

Central configuration for the PhD Shortlist Builder.
All tuneable parameters live here. No magic numbers buried in pipeline code.

API keys are loaded from environment variables (set in .env).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ── API Keys ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
SEMANTIC_SCHOLAR_API_KEY: str = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")  # optional — raises rate limit

# ── Gemini Model ──────────────────────────────────────────────────────────────
GEMINI_MODEL: str = "gemini-2.5-flash"         # free tier model
GEMINI_TEMPERATURE: float = 0.1                  # low temp for factual extraction

# ── Pipeline Limits ───────────────────────────────────────────────────────────
MAX_CANDIDATES_DISCOVERY: int = 1000   # cap on raw pool from Stage 2
MAX_CANDIDATES_AFTER_FILTER: int = 300 # cap on verified pool from Stage 3
MAX_CANDIDATES_RANKED: int = 200       # cap on scored output from Stage 4
import os
TOP_N_FOR_WHY_MATCH: int = int(os.getenv("TOP_N_FOR_WHY_MATCH", "50")) # how many get the LLM why-match blurb

# ── Domain Similarity Thresholds (Stage 3) ────────────────────────────────────
DOMAIN_SIMILARITY_MIN_THRESHOLD: float = 0.3
DOMAIN_SIMILARITY_MAX_THRESHOLD: float = 0.6

# ── Scoring Weights ───────────────────────────────────────────────────────────
WEIGHT_RESEARCH_OVERLAP: float = 0.40  # embedding cosine similarity
WEIGHT_RECENCY: float = 0.20           # papers from last 3 years
WEIGHT_OPEN_POSITION: float = 0.25    # has a funded PhD vacancy
WEIGHT_CITATION: float = 0.05          # h-index proxy
WEIGHT_COUNTRY_QUALITY: float = 0.10   # primary appointment in target country

RECENCY_YEARS: int = 3                 # papers within this window get full recency score

# ── Tier Thresholds (based on QS World Ranking) ───────────────────────────────
TIER_REACH_RANK_MAX: int = 100         # QS rank 1–100 → Reach
TIER_TARGET_RANK_MAX: int = 400        # QS rank 101–400 → Target
# QS rank 401+ or unranked → Safety

# ── University Ranking Data ───────────────────────────────────────────────────
# Minimal embedded QS 2024 ranking for UK + Canada universities.
# Full ranking lookup is handled in scoring.py.
QS_RANKINGS: dict = {
    # UK
    "University of Oxford": 3,
    "University of Cambridge": 2,
    "Imperial College London": 8,
    "UCL": 9,
    "University of Edinburgh": 27,
    "University of Manchester": 32,
    "King's College London": 40,
    "University of Bristol": 54,
    "University of Warwick": 67,
    "University of Glasgow": 78,
    "University of Sheffield": 99,
    "University of Nottingham": 100,
    "University of Southampton": 104,
    "University of Leeds": 105,
    "University of Birmingham": 119,
    "University of St Andrews": 121,
    "Durham University": 134,
    "University of Exeter": 163,
    "Lancaster University": 198,
    "University of Bath": 209,
    "Queen Mary University of London": 245,
    "University of Sussex": 297,
    "University of York": 310,
    "Heriot-Watt University": 325,
    "University of Aberdeen": 382,
    "Brunel University London": 401,
    # Canada
    "University of Toronto": 21,
    "McGill University": 30,
    "University of British Columbia": 34,
    "University of Alberta": 111,
    "McMaster University": 152,
    "University of Waterloo": 154,
    "Western University": 203,
    "University of Calgary": 246,
    "Dalhousie University": 308,
    "University of Ottawa": 325,
    "Queen's University": 358,
    "Simon Fraser University": 369,
    "Concordia University": 491,
}

# ── Country Normalisation Map ─────────────────────────────────────────────────
# Maps various source representations to canonical country names used in filtering.
COUNTRY_ALIASES: dict = {
    "gb": "UK", "united kingdom": "UK", "england": "UK",
    "scotland": "UK", "wales": "UK", "northern ireland": "UK",
    "ca": "Canada", "can": "Canada",
}

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT: Path = _PROJECT_ROOT
SAMPLE_INPUT_DIR: Path = _PROJECT_ROOT / "sample_input"
SAMPLE_OUTPUT_DIR: Path = _PROJECT_ROOT / "sample_output"
CACHE_DIR: Path = _PROJECT_ROOT / "cache"
LOG_DIR: Path = _PROJECT_ROOT / "logs"
