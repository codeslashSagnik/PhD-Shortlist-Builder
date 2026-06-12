"""
pipeline/verification.py  —  Stage 3: Verification & Filtering

Takes the raw ~500–1000 candidate pool and runs 4 sequential checks using a cascade funnel.
A candidate must PASS ALL 4 checks or is dropped.

Check 1 — Country Filter        (hard, no exceptions) — pure in-memory, instant
Check 2 — Career Stage Filter   (is this a real PI / faculty?) — parallelized
Check 3 — Domain Relevance      (LLM/embedding check on abstract) — parallelized
Check 4 — Eligibility Filter    (open positions only) — parallelized

Input:  List[Dict] raw candidates + StudentSignal
Output: List[Dict] verified candidates (~100–300)
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
import utils.gemini as genai
from sentence_transformers import SentenceTransformer, util
from utils.gemini import ResourceExhausted

from models.student_signal import StudentSignal
from utils.logger import get_logger
from utils.cache_manager import cache_get, cache_set
from utils.rate_limiter import get_limiter
from config.settings import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    COUNTRY_ALIASES,
    MAX_CANDIDATES_AFTER_FILTER,
    DOMAIN_SIMILARITY_MIN_THRESHOLD,
    DOMAIN_SIMILARITY_MAX_THRESHOLD,
)

log = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PhDShortlistBot/1.0; academic research tool)"
    )
}

# ── Faculty title indicators ───────────────────────────────────────────────────
_FACULTY_TITLES = {
    "professor", "associate professor", "assistant professor",
    "reader", "senior lecturer", "lecturer", "chair", "principal investigator",
    "pi ", "faculty", "head of", "director of",
}
_NON_FACULTY_TITLES = {
    "phd candidate", "phd student", "doctoral candidate", "doctoral student",
    "postdoctoral", "postdoc", "research fellow", "research associate",
    "research assistant", "teaching assistant", "visiting researcher", "adjunct",
}


def _init_gemini() -> genai.GenerativeModel:
    if not GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY not set.")
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        generation_config=genai.types.GenerationConfig(
            temperature=GEMINI_TEMPERATURE,
            response_mime_type="application/json",
        ),
    )


def _clean_json(text: str) -> dict:
    """Robust JSON parsing to strip markdown backticks and preambles."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
        
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("JSON decode failed, attempting regex fix. Raw: %s", text)
        # Final fallback: just try to find curly braces
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        raise e


def _safe_llm_call(model: genai.GenerativeModel, prompt: str) -> dict:
    """Calls LLM with rate limit retries and caching."""
    cached = cache_get("llm", prompt)
    if cached:
        return cached

    retries = 5
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            result = _clean_json(response.text)
            cache_set("llm", prompt, result)
            return result
        except ResourceExhausted:
            wait = 30 * (attempt + 1)
            log.warning("LLM 429 rate limit (attempt %d/%d). Waiting %ds...", attempt + 1, retries, wait)
            time.sleep(wait)
        except Exception as e:
            log.warning("LLM API error (attempt %d/%d): %s", attempt + 1, retries, e)
            time.sleep(5)
            
    log.error("All LLM retries failed for prompt.")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 1 — Country Filter
# ─────────────────────────────────────────────────────────────────────────────

def _check_country(candidate: Dict, target_countries: List[str]) -> Tuple[bool, str]:
    country = candidate.get("country", "").strip()
    normalised = COUNTRY_ALIASES.get(country.lower(), country)

    if normalised in target_countries:
        return True, f"Country OK: {normalised}"
    if not country:
        return True, "Country unknown — passed"

    return False, f"Country mismatch: '{country}' not in {target_countries}"


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 2 — Career Stage Filter
# ─────────────────────────────────────────────────────────────────────────────

def _title_heuristic(text: str) -> Optional[bool]:
    text_lower = text.lower()
    for bad in _NON_FACULTY_TITLES:
        if bad in text_lower:
            return False
    for good in _FACULTY_TITLES:
        if good in text_lower:
            return True
    return None

def _fetch_faculty_page(url: str) -> Optional[str]:
    if not url or not url.startswith("http"):
        return None
    cached = cache_get("scrape", url)
    if cached:
        return cached.get("text", "")

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=5)  # 5s timeout
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)[:5000]
        cache_set("scrape", url, {"text": text})
        return text
    except Exception as e:
        log.debug("Scrape failed: %s — %s", url, e)
        return None

def _check_career_stage(candidate: Dict, _unused=None) -> Tuple[bool, str]:
    """Pure heuristic career stage check. No LLM calls."""
    title_field = candidate.get("title", "")
    homepage = candidate.get("homepage", "")
    
    # 1. Quick title heuristic
    if title_field:
        heuristic = _title_heuristic(title_field)
        if heuristic is False:
            return False, f"Non-faculty title: '{title_field}'"
        if heuristic is True:
            return True, f"Faculty title confirmed: '{title_field}'"

    # 2. OpenAlex year heuristic (papers older than 5 years -> highly likely PI if still publishing)
    oldest_paper_year = 9999
    newest_paper_year = 0
    for ev in candidate.get("evidence", []):
        year = ev.get("year", 0)
        if year > 0:
            if year < oldest_paper_year:
                oldest_paper_year = year
            if year > newest_paper_year:
                newest_paper_year = year
            
    # If they haven't published anything in 4+ years, they might be inactive/emeritus
    if newest_paper_year > 0 and newest_paper_year < 2022:
        candidate["flag_inactive_researcher"] = True
        
    if oldest_paper_year < 2021:
         return True, f"Evidence of publishing since {oldest_paper_year} (likely PI)"

    # 3. Web scrape fallback
    page_text = _fetch_faculty_page(homepage) if homepage else None
    if page_text:
        heuristic = _title_heuristic(page_text)
        if heuristic is False:
            return False, "Page text suggests non-faculty role"
        if heuristic is True:
            return True, "Page text confirms faculty role"

    # 4. Default to True with unverified flag — no LLM needed here
    # The domain relevance check (Check 3) will catch irrelevant candidates anyway
    candidate["unverified_career"] = True
    return True, "Insufficient data, assumed PI"


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 3 — Domain Relevance Filter
# ─────────────────────────────────────────────────────────────────────────────

def _check_domain_relevance(
    candidate: Dict, signal: StudentSignal, embedder: SentenceTransformer, student_emb
) -> Tuple[bool, str]:
    """Pure embedding-based domain check. No LLM calls."""
    evidence = candidate.get("evidence", [])
    if not evidence:
        return True, "No abstract available"

    best_abstract = ""
    for ev in evidence:
        abstract = ev.get("abstract", "")
        if len(abstract) > 50:
            best_abstract = abstract
            break

    if not best_abstract:
        return True, "No substantive abstract"

    # Pure embedding similarity — no LLM needed
    candidate_emb = embedder.encode(best_abstract[:1000], convert_to_tensor=True)
    sim = float(util.cos_sim(candidate_emb, student_emb)[0][0])
    candidate["domain_similarity"] = round(sim, 3)
    
    # Hard cutoff at 0.3, auto-pass above 0.6, use 0.45 for the ambiguous band
    AMBIGUOUS_CUTOFF = 0.45
    if sim < DOMAIN_SIMILARITY_MIN_THRESHOLD:
        return False, f"Embedding similarity {sim:.2f} < {DOMAIN_SIMILARITY_MIN_THRESHOLD} (clear mismatch)"
    if sim > DOMAIN_SIMILARITY_MAX_THRESHOLD:
        return True, f"Embedding similarity {sim:.2f} > {DOMAIN_SIMILARITY_MAX_THRESHOLD} (strong match)"
    if sim >= AMBIGUOUS_CUTOFF:
        return True, f"Embedding similarity {sim:.2f} >= {AMBIGUOUS_CUTOFF} (borderline pass)"
    return False, f"Embedding similarity {sim:.2f} < {AMBIGUOUS_CUTOFF} (borderline reject)"


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 4 — Eligibility Filter
# ─────────────────────────────────────────────────────────────────────────────

def _check_eligibility(candidate: Dict, nationality: str) -> Tuple[bool, str]:
    """Pure keyword-based eligibility check. No LLM calls."""
    if not candidate.get("open_position", False):
        return True, "Not an open position"

    ad_text = candidate.get("position_ad_text", "")
    if not ad_text:
        for ev in candidate.get("evidence", []):
            if ev.get("type") == "open_position":
                ad_text = ev.get("title", "") + " " + ev.get("abstract", "")
                break

    if not ad_text or len(ad_text) < 20:
        return True, "No ad text"

    text_lower = ad_text.lower()
    hard_exclusions = [
        "home fees only", "uk/eu residents", "uk residents only", "uk applicants only",
        "domestic students only", "must have right to work in uk",
        "open to uk students", "open to canadian citizens", "home students only",
        "must be a canadian citizen", "must be a uk citizen", "uk only",
        "not open to international students", "requires settled status", "eu settled status",
        "uk applicants", "home fees", "uk/eu students", "funding for uk students",
        "eligible for home fees", "home fee status"
    ]
    for phrase in hard_exclusions:
        if phrase in text_lower:
            return False, f"Exclusion detected: '{phrase}'"

    return True, "No exclusion phrases detected"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 Main (Cascade Pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def verify_candidates(candidates: List[Dict[str, Any]], signal: StudentSignal) -> List[Dict[str, Any]]:
    log.info("=" * 60)
    log.info("STAGE 3 — Verification & Filtering (Cascade Funnel)")
    log.info("=" * 60)
    log.info("Starting with %d raw deduplicated candidates", len(candidates))

    # No LLM model needed — Stage 3 is 100% embedding + heuristic based
    target_countries = signal.constraints.countries
    nationality = signal.constraints.nationality

    # Load embedder just in time for Check 3
    log.info("Loading SentenceTransformers model for domain checks...")
    embedder = SentenceTransformer("all-MiniLM-L6-v2")

    survivors = candidates
    stats = {}

    def _parallel_filter(check_name, check_func, input_pool, max_workers=2):
        log.info("  Running %s on %d candidates...", check_name, len(input_pool))
        passed = []
        dropped = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_cand = {executor.submit(check_func, c): c for c in input_pool}
            for future in as_completed(future_to_cand):
                c = future_to_cand[future]
                try:
                    is_valid, reason = future.result()
                    if is_valid:
                        passed.append(c)
                    else:
                        dropped += 1
                        log.debug("    ✗ %s FAIL — %s: %s", check_name, c.get("name"), reason)
                except Exception as e:
                    log.error("    ERROR in %s for %s: %s", check_name, c.get("name"), e)
                    passed.append(c)  # Fail open
        stats[check_name] = dropped
        log.info("  %s finished: %d passed, %d dropped", check_name, len(passed), dropped)
        return passed

    # CHECK 1: Country (Sequential, Instant)
    c1_survivors = []
    dropped_c1 = 0
    for c in survivors:
        is_valid, reason = _check_country(c, target_countries)
        if is_valid:
            c1_survivors.append(c)
        else:
            dropped_c1 += 1
    stats["Check 1 (Country)"] = dropped_c1
    log.info("  Check 1 (Country) finished: %d passed, %d dropped", len(c1_survivors), dropped_c1)
    survivors = c1_survivors

    # CHECK 2: Career Stage (Parallel — pure heuristics, no LLM)
    if survivors:
        survivors = _parallel_filter(
            "Check 2 (Career)",
            lambda c: _check_career_stage(c, None),
            survivors
        )

    # Pre-compute student embedding once for Check 3
    student_area = f"{signal.research.thesis_topic} | {' '.join(signal.research.primary_keywords[:5])}"
    student_emb = embedder.encode(student_area, convert_to_tensor=True)
    log.info("  Student embedding computed for domain matching")

    # CHECK 3: Domain Relevance (Parallel — pure embeddings, no LLM)
    if survivors:
        survivors = _parallel_filter(
            "Check 3 (Domain)",
            lambda c: _check_domain_relevance(c, signal, embedder, student_emb),
            survivors
        )

    # CHECK 4: Eligibility (Parallel — pure keyword heuristics, no LLM)
    if survivors:
        survivors = _parallel_filter(
            "Check 4 (Eligibility)",
            lambda c: _check_eligibility(c, nationality),
            survivors
        )

    for c in survivors:
        c["verification_status"] = "verified"

    log.info("Stage 3 | Verification complete:")
    for step, dropped in stats.items():
        log.info("  Dropped %-20s: %d", step, dropped)
    log.info("  ✓ Verified & kept      : %d", len(survivors))

    if len(survivors) > MAX_CANDIDATES_AFTER_FILTER:
        log.info("Stage 3 | Capping verified pool to %d", MAX_CANDIDATES_AFTER_FILTER)
        survivors = survivors[:MAX_CANDIDATES_AFTER_FILTER]

    log.info("=" * 60)
    return survivors
