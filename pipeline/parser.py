"""
pipeline/parser.py  —  Stage 1: Profile Parser

Reads the raw student JSON and returns a clean StudentSignal object.
Uses Gemini to extract research keywords and themes from free-text fields
(resume, call notes, project descriptions).

Input:  dict (loaded from sample_input/student_XXX.json)
Output: StudentSignal
"""

import json
import time
from typing import Any, Dict, List

import utils.openrouter as genai

from config.settings import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TEMPERATURE
from models.student_signal import StudentSignal, ResearchKeywords, TargetConstraints, SoftSignals
from utils.logger import get_logger
from utils.cache_manager import cache_get, cache_set

log = get_logger(__name__)


# ── Gemini setup ──────────────────────────────────────────────────────────────

def _init_gemini() -> genai.GenerativeModel:
    if not GEMINI_API_KEY:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Add it to your .env file."
        )
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        generation_config=genai.types.GenerationConfig(
            temperature=GEMINI_TEMPERATURE,
            response_mime_type="application/json",
        ),
    )
    log.debug("Gemini model initialised: %s", GEMINI_MODEL)
    return model


# ── LLM extraction ────────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """
You are a research assistant helping match PhD applicants to professors.

Analyse the student profile below and extract the following as JSON.
Be precise and specific — generic terms are less useful than specific ones.

Return ONLY valid JSON matching this exact schema (no markdown, no explanation):
{{
  "primary_keywords": ["list of 5-10 specific research terms, e.g. 'depression detection', 'clinical NLP'"],
  "secondary_keywords": ["list of 3-6 broader related areas"],
  "thesis_topic_refined": "one sentence — the core research question",
  "excluded_subfields": ["fields the student explicitly does NOT want"],
  "mentioned_professors": ["names of professors the student has cited or read"],
  "preferred_lab_size": "small | medium | large | unknown",
  "interdisciplinary_ok": true or false,
  "additional_signals": "any other important signal for matching (1-2 sentences max)"
}}

STUDENT PROFILE:
---
Thesis topic: {thesis_topic}
Primary area: {primary_area}
Secondary area: {secondary_area}
Projects: {projects}
Publications: {publications}
Relevant coursework: {coursework}
---
"""


def _extract_via_llm(model: genai.GenerativeModel, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Call Gemini to extract structured signals from free-text fields."""
    research = profile.get("research", {})
    background = profile.get("academic_background", {})

    projects_text = "\n".join(
        f"- {p['name']}: {p['description']}"
        for p in background.get("projects", [])
    )
    pubs_text = "\n".join(
        f"- {p['title']} ({p['year']})"
        for p in background.get("publications", [])
    )
    coursework_text = ", ".join(background.get("relevant_coursework", []))

    prompt = _EXTRACTION_PROMPT.format(
        thesis_topic=research.get("thesis_topic", ""),
        primary_area=research.get("primary_area", ""),
        secondary_area=research.get("secondary_area", ""),
        projects=projects_text,
        publications=pubs_text,
        coursework=coursework_text,
    )

    # Cache key = hash of the prompt
    cached = cache_get("llm", prompt)
    if cached:
        log.info("Stage 1 | LLM extraction: cache hit")
        return cached

    log.info("Stage 1 | Calling Gemini for keyword extraction...")
    start = time.time()
    response = model.generate_content(prompt)
    elapsed = time.time() - start
    log.info("Stage 1 | Gemini responded in %.2fs", elapsed)

    raw_text = response.text.strip()
    log.debug("Stage 1 | Raw LLM output: %s", raw_text[:500])

    try:
        extracted = json.loads(raw_text)
    except json.JSONDecodeError as e:
        log.warning("Stage 1 | LLM returned invalid JSON: %s — falling back to profile keywords", e)
        extracted = {}

    cache_set("llm", prompt, extracted)
    return extracted


# ── Main parser function ───────────────────────────────────────────────────────

def parse_profile(profile: Dict[str, Any]) -> StudentSignal:
    """
    Stage 1 entry point.

    Args:
        profile: Raw dict loaded from the student JSON file.

    Returns:
        A fully populated StudentSignal object.
    """
    log.info("=" * 60)
    log.info("STAGE 1 — Profile Parser")
    log.info("=" * 60)

    student_id = profile.get("student_id", "unknown")
    personal = profile.get("personal", {})
    research = profile.get("research", {})
    target = profile.get("target", {})
    intake = target.get("intake", {})

    log.info("Parsing profile for: %s (id=%s)", personal.get("name", "?"), student_id)

    # ── Step 1: LLM extraction ─────────────────────────────────────────────────
    model = _init_gemini()
    llm_data = _extract_via_llm(model, profile)

    log.info("Stage 1 | LLM extracted %d primary keywords, %d secondary",
             len(llm_data.get("primary_keywords", [])),
             len(llm_data.get("secondary_keywords", [])))

    # ── Step 2: Merge LLM output with explicit profile fields ─────────────────
    # LLM keywords take priority, but we supplement with profile's own keywords
    # to ensure nothing is lost.
    profile_keywords: List[str] = research.get("keywords", [])
    primary_kw: List[str] = llm_data.get("primary_keywords", [])
    secondary_kw: List[str] = llm_data.get("secondary_keywords", [])

    # Add any profile keywords not already in primary/secondary
    all_llm_kw = set(k.lower() for k in primary_kw + secondary_kw)
    for kw in profile_keywords:
        if kw.lower() not in all_llm_kw:
            secondary_kw.append(kw)

    # ── Step 3: Build sub-objects ──────────────────────────────────────────────
    projects = profile.get("academic_background", {}).get("projects", [])
    publications = profile.get("academic_background", {}).get("publications", [])

    research_obj = ResearchKeywords(
        primary_keywords=primary_kw,
        secondary_keywords=secondary_kw,
        thesis_topic=llm_data.get("thesis_topic_refined", research.get("thesis_topic", "")),
        discipline=research.get("discipline", "Computer Science"),
        primary_area=research.get("primary_area", ""),
        secondary_area=research.get("secondary_area", ""),
        project_descriptions=[p.get("description", "") for p in projects],
        publication_titles=[p.get("title", "") for p in publications],
    )

    constraints_obj = TargetConstraints(
        countries=[c.strip() for c in target.get("countries", [])],
        intake_semester=intake.get("semester", ""),
        intake_year=intake.get("year", 0),
        funding_required=target.get("funding_required", True),
        nationality=personal.get("nationality", ""),
    )

    soft_obj = SoftSignals(
        preferred_lab_size=llm_data.get("preferred_lab_size", "unknown"),
        interdisciplinary_ok=llm_data.get("interdisciplinary_ok", True),
        excluded_subfields=llm_data.get("excluded_subfields", []),
        mentioned_professors=llm_data.get("mentioned_professors", []),
        additional_notes=llm_data.get("additional_signals", ""),
    )

    # ── Step 4: Build embedding text ──────────────────────────────────────────
    # This is what gets embedded in Stage 4 for cosine similarity scoring.
    embedding_parts = [
        research_obj.thesis_topic,
        " ".join(research_obj.primary_keywords),
        " ".join(research_obj.secondary_keywords),
        research_obj.primary_area,
        research_obj.secondary_area,
    ] + research_obj.project_descriptions[:3]  # top 3 project descriptions

    embedding_text = " | ".join(p for p in embedding_parts if p)

    # ── Step 5: Assemble StudentSignal ────────────────────────────────────────
    signal = StudentSignal(
        student_id=student_id,
        student_name=personal.get("name", ""),
        student_level=personal.get("student_level", "Bachelors"),
        research=research_obj,
        constraints=constraints_obj,
        soft_signals=soft_obj,
        embedding_text=embedding_text,
    )

    log.info("Stage 1 | StudentSignal built successfully")
    log.info("  → Target countries  : %s", signal.constraints.countries)
    log.info("  → Primary keywords  : %s", signal.research.primary_keywords[:5])
    log.info("  → Thesis topic      : %s", signal.research.thesis_topic[:100])
    log.info("  → Funding required  : %s", signal.constraints.funding_required)
    log.info("  → Excluded fields   : %s", signal.soft_signals.excluded_subfields)
    log.info("  → Embedding text    : %s...", signal.embedding_text[:120])
    log.info("=" * 60)

    return signal
