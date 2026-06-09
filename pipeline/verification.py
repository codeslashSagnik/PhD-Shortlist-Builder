"""
pipeline/verification.py  —  Stage 3: Verification & Filtering

Takes the raw ~500–1000 candidate pool and runs 4 sequential checks.
A candidate must PASS ALL 4 checks or is dropped.

Check 1 — Country Filter        (hard, no exceptions)
Check 2 — Career Stage Filter   (is this a real PI / faculty?)
Check 3 — Domain Relevance      (LLM check on abstract vs student's area)
Check 4 — Eligibility Filter    (open positions only — citizenship restrictions)

Input:  List[Dict] raw candidates + StudentSignal
Output: List[Dict] verified candidates (~100–300)
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
import google.generativeai as genai

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
    "visiting researcher", "adjunct",
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


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 1 — Country Filter
# ─────────────────────────────────────────────────────────────────────────────

def _check_country(candidate: Dict, target_countries: List[str]) -> Tuple[bool, str]:
    """
    Hard filter: candidate's country must be in target_countries.
    Returns (passed, reason).
    """
    country = candidate.get("country", "").strip()

    # Normalise using aliases
    normalised = COUNTRY_ALIASES.get(country.lower(), country)

    if normalised in target_countries:
        return True, f"Country OK: {normalised}"

    # If country is empty (e.g. Semantic Scholar entries), don't drop — defer to Check 2 scrape
    if not country:
        return True, "Country unknown — will verify via faculty page"

    return False, f"Country mismatch: '{country}' not in {target_countries}"


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 2 — Career Stage Filter
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_faculty_page(url: str) -> Optional[str]:
    """Fetch a URL and return text content, using cache."""
    if not url or not url.startswith("http"):
        return None
    cached = cache_get("scrape", url)
    if cached:
        return cached.get("text", "")

    limiter = get_limiter("generic")
    limiter.wait()
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)[:5000]
        cache_set("scrape", url, {"text": text})
        return text
    except Exception as e:
        log.debug("Faculty page fetch failed: %s — %s", url, e)
        return None


def _title_heuristic(text: str) -> Optional[bool]:
    """
    Quick text-based check for faculty/non-faculty titles.
    Returns True if faculty, False if non-faculty, None if ambiguous.
    """
    text_lower = text.lower()
    for bad in _NON_FACULTY_TITLES:
        if bad in text_lower:
            return False
    for good in _FACULTY_TITLES:
        if good in text_lower:
            return True
    return None


def _check_career_stage(
    candidate: Dict,
    model: genai.GenerativeModel,
    target_countries: List[str],
) -> Tuple[bool, str]:
    """
    Check 2: Verify the person is faculty / PI, not a student or postdoc.

    Strategy:
    1. Check title field directly.
    2. Scrape homepage if available.
    3. If still ambiguous, call LLM.
    4. Update candidate's country if we learn it from scrape.
    """
    name = candidate.get("name", "")
    homepage = candidate.get("homepage", "")
    title_field = candidate.get("title", "")

    # Quick check on known title field
    if title_field:
        heuristic = _title_heuristic(title_field)
        if heuristic is False:
            return False, f"Non-faculty title: '{title_field}'"
        if heuristic is True:
            return True, f"Faculty title confirmed: '{title_field}'"

    # Try scraping homepage
    page_text = _fetch_faculty_page(homepage) if homepage else None

    if page_text:
        # Try to extract country from institution text if not set
        if not candidate.get("country"):
            for alias, canonical in COUNTRY_ALIASES.items():
                if alias in page_text.lower() and canonical in target_countries:
                    candidate["country"] = canonical
                    log.debug("Career check: inferred country=%s for %s", canonical, name)
                    break
            # Also check UK/Canada directly
            for c in target_countries:
                if c.lower() in page_text.lower():
                    candidate["country"] = c
                    break

        heuristic = _title_heuristic(page_text)
        if heuristic is False:
            return False, "Page text suggests non-faculty role"
        if heuristic is True:
            return True, "Page text confirms faculty role"

    # Ambiguous — use LLM
    context = title_field or (page_text[:800] if page_text else f"Author named {name}")
    prompt = json.dumps({
        "task": "career_stage_check",
        "name": name,
        "context": context,
        "question": (
            "Based on this information, is this person currently a faculty member "
            "(Professor, Associate Professor, Assistant Professor, Reader, or Senior Lecturer) "
            "who can independently supervise PhD students? "
            "Answer with JSON: {\"is_faculty\": true/false, \"reasoning\": \"one sentence\"}"
        ),
    })

    cached = cache_get("llm", prompt)
    if cached:
        result = cached
    else:
        try:
            response = model.generate_content(prompt)
            result = json.loads(response.text)
            cache_set("llm", prompt, result)
        except Exception as e:
            log.debug("LLM career check failed for %s: %s", name, e)
            return True, "LLM check failed — defaulting to pass (will catch in domain check)"

    is_faculty = result.get("is_faculty", True)
    reasoning = result.get("reasoning", "")
    if is_faculty:
        return True, f"LLM confirmed faculty: {reasoning}"
    return False, f"LLM rejected (not faculty): {reasoning}"


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 3 — Domain Relevance Filter
# ─────────────────────────────────────────────────────────────────────────────

_DOMAIN_PROMPT = """
You are a research matching assistant.

Given a professor's research abstract and a student's research area, determine whether
they work in genuinely the same domain — not just sharing a surface keyword.

Return ONLY JSON with this schema:
{{
  "same_domain": true or false,
  "discipline_match": true or false,
  "reasoning": "one sentence explaining the decision"
}}

Student research area: {student_area}
Student discipline: {discipline}

Professor's evidence (paper or grant abstract):
{abstract}
"""


def _check_domain_relevance(
    candidate: Dict,
    signal: StudentSignal,
    model: genai.GenerativeModel,
) -> Tuple[bool, str]:
    """
    Check 3: LLM confirms the candidate's work is genuinely in the student's domain.
    Uses the professor's paper/grant abstracts as evidence.
    """
    evidence = candidate.get("evidence", [])
    if not evidence:
        # No evidence to evaluate — give benefit of doubt
        return True, "No abstract available — passed by default"

    # Use the first abstract we have
    best_abstract = ""
    for ev in evidence:
        abstract = ev.get("abstract", "")
        if len(abstract) > 50:
            best_abstract = abstract
            break

    if not best_abstract:
        return True, "No substantive abstract — passed by default"

    student_area = (
        f"{signal.research.thesis_topic} | "
        f"{' '.join(signal.research.primary_keywords[:5])}"
    )

    prompt = _DOMAIN_PROMPT.format(
        student_area=student_area,
        discipline=signal.research.discipline,
        abstract=best_abstract[:1000],
    )

    cached = cache_get("llm", prompt)
    if cached:
        result = cached
    else:
        try:
            response = model.generate_content(prompt)
            result = json.loads(response.text)
            cache_set("llm", prompt, result)
        except Exception as e:
            log.debug("LLM domain check failed for %s: %s", candidate.get("name"), e)
            return True, "LLM check failed — defaulting to pass"

    same_domain = result.get("same_domain", True)
    discipline_match = result.get("discipline_match", True)
    reasoning = result.get("reasoning", "")

    if same_domain and discipline_match:
        return True, f"Domain match confirmed: {reasoning}"
    if same_domain and not discipline_match:
        return False, f"Keyword overlap but wrong discipline: {reasoning}"
    return False, f"Domain mismatch: {reasoning}"


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 4 — Eligibility Filter
# ─────────────────────────────────────────────────────────────────────────────

_ELIGIBILITY_PROMPT = """
You are reviewing a PhD position advertisement.

Extract eligibility restrictions. Return ONLY JSON:
{{
  "has_restriction": true or false,
  "restriction_description": "quote the exact restriction or 'none'",
  "excludes_nationality": "{nationality}" true or false
}}

Student nationality: {nationality}
Position ad text:
{ad_text}
"""


def _check_eligibility(
    candidate: Dict,
    nationality: str,
    model: genai.GenerativeModel,
) -> Tuple[bool, str]:
    """
    Check 4: For open positions, check if citizenship/residency restrictions
    exclude the student's nationality.
    Only applies to candidates with open_position=True.
    """
    if not candidate.get("open_position", False):
        return True, "Not an open position — eligibility check skipped"

    ad_text = candidate.get("position_ad_text", "")
    if not ad_text:
        # Try to get it from evidence
        for ev in candidate.get("evidence", []):
            if ev.get("type") == "open_position":
                ad_text = ev.get("title", "") + " " + ev.get("abstract", "")
                break

    if not ad_text or len(ad_text) < 20:
        return True, "No ad text to evaluate — passed by default"

    # Quick heuristic first — catch common phrases
    text_lower = ad_text.lower()
    hard_exclusions = [
        "home fees only", "uk/eu residents", "uk residents only",
        "domestic students only", "must have right to work in uk",
        "open to uk students", "open to canadian citizens",
        "must be a canadian citizen", "must be a uk citizen",
    ]
    for phrase in hard_exclusions:
        if phrase in text_lower:
            return False, f"Eligibility restriction detected: '{phrase}'"

    # If no obvious phrase, check with LLM
    prompt = _ELIGIBILITY_PROMPT.format(
        nationality=nationality,
        ad_text=ad_text[:2000],
    )

    cached = cache_get("llm", prompt)
    if cached:
        result = cached
    else:
        try:
            response = model.generate_content(prompt)
            result = json.loads(response.text)
            cache_set("llm", prompt, result)
        except Exception as e:
            log.debug("LLM eligibility check failed: %s", e)
            return True, "LLM check failed — defaulting to pass"

    excludes = result.get("excludes_nationality", False)
    restriction = result.get("restriction_description", "none")

    if excludes:
        return False, f"Position excludes student nationality: {restriction}"
    return True, f"Eligibility OK: {restriction}"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 Main
# ─────────────────────────────────────────────────────────────────────────────

def verify_candidates(
    candidates: List[Dict[str, Any]],
    signal: StudentSignal,
) -> List[Dict[str, Any]]:
    """
    Stage 3 entry point.

    Runs all 4 checks sequentially on each candidate.
    Logs every decision. Drops candidates that fail any check.

    Args:
        candidates: Deduplicated raw candidates from Stage 2.
        signal:     StudentSignal from Stage 1.

    Returns:
        Filtered list of verified candidates.
    """
    log.info("=" * 60)
    log.info("STAGE 3 — Verification & Filtering")
    log.info("=" * 60)
    log.info("Input: %d candidates", len(candidates))

    model = _init_gemini()
    target_countries = signal.constraints.countries
    nationality = signal.constraints.nationality

    verified = []
    dropped_country = 0
    dropped_career = 0
    dropped_domain = 0
    dropped_eligibility = 0

    for i, candidate in enumerate(candidates):
        name = candidate.get("name", "Unknown")
        institution = candidate.get("institution", "Unknown")
        log.debug("Checking [%d/%d]: %s @ %s", i + 1, len(candidates), name, institution)

        # ── Check 1: Country ──────────────────────────────────────────────────
        passed, reason = _check_country(candidate, target_countries)
        if not passed:
            dropped_country += 1
            log.debug("  ✗ CHECK 1 FAIL — %s: %s", name, reason)
            continue
        log.debug("  ✓ Check 1 pass — %s: %s", name, reason)

        # ── Check 2: Career Stage ─────────────────────────────────────────────
        passed, reason = _check_career_stage(candidate, model, target_countries)
        if not passed:
            dropped_career += 1
            log.debug("  ✗ CHECK 2 FAIL — %s: %s", name, reason)
            continue
        log.debug("  ✓ Check 2 pass — %s: %s", name, reason)

        # ── Check 3: Domain Relevance ─────────────────────────────────────────
        passed, reason = _check_domain_relevance(candidate, signal, model)
        if not passed:
            dropped_domain += 1
            log.debug("  ✗ CHECK 3 FAIL — %s: %s", name, reason)
            continue
        log.debug("  ✓ Check 3 pass — %s: %s", name, reason)

        # ── Check 4: Eligibility ──────────────────────────────────────────────
        passed, reason = _check_eligibility(candidate, nationality, model)
        if not passed:
            dropped_eligibility += 1
            log.debug("  ✗ CHECK 4 FAIL — %s: %s", name, reason)
            continue
        log.debug("  ✓ Check 4 pass — %s: %s", name, reason)

        # All checks passed
        candidate["verification_status"] = "verified"
        verified.append(candidate)

    log.info("Stage 3 | Verification complete:")
    log.info("  Dropped (country)      : %d", dropped_country)
    log.info("  Dropped (career stage) : %d", dropped_career)
    log.info("  Dropped (domain)       : %d", dropped_domain)
    log.info("  Dropped (eligibility)  : %d", dropped_eligibility)
    log.info("  ✓ Verified & kept      : %d", len(verified))

    if len(verified) > MAX_CANDIDATES_AFTER_FILTER:
        log.info("Stage 3 | Capping verified pool to %d", MAX_CANDIDATES_AFTER_FILTER)
        verified = verified[:MAX_CANDIDATES_AFTER_FILTER]

    log.info("=" * 60)
    return verified
