"""
pipeline/generator.py  —  Stage 5: Why-Match Generation & Output Formatting

For each of the top N ranked candidates:
  1. Calls Gemini to write a personalised 2–3 sentence why_match blurb
  2. Attempts email discovery
  3. Assembles the final output JSON per the schema

Input:  List[Dict] ranked candidates + StudentSignal
Output: Final output dict (written to sample_output/student_id.json)
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import utils.openrouter as genai

from models.student_signal import StudentSignal
from utils.logger import get_logger
from utils.cache_manager import cache_get, cache_set
from utils.email_finder import find_email
from config.settings import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    TOP_N_FOR_WHY_MATCH,
    SAMPLE_OUTPUT_DIR,
)

log = get_logger(__name__)


# ── Gemini setup ──────────────────────────────────────────────────────────────

def _init_gemini() -> genai.GenerativeModel:
    if not GEMINI_API_KEY:
        raise EnvironmentError("GEMINI_API_KEY not set.")
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        generation_config=genai.types.GenerationConfig(
            temperature=0.4,   # slightly higher for creative but grounded writing
        ),
    )


# ── Why-match prompt ──────────────────────────────────────────────────────────

_WHY_MATCH_PROMPT = """
You are a PhD application advisor helping a student articulate why they are a strong match
for a specific professor's research group.

Write EXACTLY 2–3 sentences. Be specific — mention the professor's actual work by name,
and the student's actual project/paper. Do NOT write generic praise.
Do NOT start with "This student..." or "The professor...".
Write from a neutral, analytical perspective.

Professor: {prof_name}
Institution: {institution}
Professor's recent work:
{prof_evidence}

Student: {student_name}
Student's thesis topic: {thesis_topic}
Student's key projects/papers: {student_work}
Student's research keywords: {keywords}

Write the why_match blurb now (2–3 sentences only).
You MUST output a valid JSON object with exactly one key "why_match".
"""

def _template_why_match(candidate: Dict, signal: StudentSignal) -> str:
    """
    Template-based why_match fallback when LLM is unavailable.
    Uses the professor's actual paper titles/abstracts for specificity.
    """
    name = candidate.get("name", "This researcher")
    evidence = candidate.get("evidence", [])
    keywords = signal.research.primary_keywords[:3]
    thesis = signal.research.thesis_topic

    # Find the best paper (prefer one with an abstract)
    best_paper = None
    for ev in evidence[:5]:
        if ev.get("abstract"):
            best_paper = ev
            break
    if not best_paper and evidence:
        best_paper = evidence[0]

    if best_paper:
        paper_title = best_paper.get("title", "")
        abstract = best_paper.get("abstract", "")
        year = best_paper.get("year", "")

        # Extract a key phrase from the abstract (first sentence)
        abstract_snippet = ""
        if abstract:
            first_sentence = abstract.split(".")[0]
            if len(first_sentence) > 30:
                abstract_snippet = first_sentence[:200]

        kw_str = " and ".join(keywords[:2]) if keywords else "mental health NLP"

        if abstract_snippet:
            return (
                f"{name}'s work on '{paper_title}' ({year}) directly addresses {kw_str}, "
                f"which is the core of {signal.student_name}'s research agenda. "
                f"Specifically, their research explores {abstract_snippet.lower()}, "
                f"aligning with {signal.student_name}'s thesis on {thesis[:120]}."
            )
        else:
            return (
                f"{name}'s publication '{paper_title}' ({year}) places their research "
                f"squarely within {kw_str} — the primary domain of {signal.student_name}'s work. "
                f"Their focus on {paper_title.lower()} is a direct complement to "
                f"{signal.student_name}'s thesis: {thesis[:120]}."
            )
    else:
        kw_str = ", ".join(keywords[:3]) if keywords else "mental health informatics"
        return (
            f"{name}'s research profile demonstrates strong alignment with {kw_str}, "
            f"which form the foundation of {signal.student_name}'s PhD thesis. "
            f"Their work in this space makes them a relevant supervisor candidate for "
            f"{signal.student_name}'s proposed research on {thesis[:120]}."
        )


def _generate_why_match(
    model: genai.GenerativeModel,
    candidate: Dict,
    signal: StudentSignal,
) -> str:
    """Generate a personalised why_match blurb for one candidate."""
    # Build professor evidence summary
    evidence_lines = []
    for ev in candidate.get("evidence", [])[:3]:
        title = ev.get("title", "")
        abstract = ev.get("abstract", "")
        year = ev.get("year", "")
        if title:
            line = f"- '{title}' ({year})"
            if abstract:
                line += f": {abstract[:200]}"
            evidence_lines.append(line)
    prof_evidence = "\n".join(evidence_lines) if evidence_lines else "No specific papers available"

    # Student work summary
    student_work_parts = []
    for pub in signal.research.publication_titles[:2]:
        student_work_parts.append(f"Publication: {pub}")
    for desc in signal.research.project_descriptions[:2]:
        student_work_parts.append(f"Project: {desc[:150]}")
    student_work = "\n".join(student_work_parts) if student_work_parts else signal.research.thesis_topic

    prompt = _WHY_MATCH_PROMPT.format(
        prof_name=candidate.get("name", ""),
        institution=candidate.get("institution", ""),
        prof_evidence=prof_evidence,
        student_name=signal.student_name,
        thesis_topic=signal.research.thesis_topic,
        student_work=student_work,
        keywords=", ".join(signal.research.primary_keywords[:5]),
    )

    # Cache key must be unique per professor — use name + first paper title
    first_paper_title = ""
    for ev in candidate.get("evidence", [])[:1]:
        first_paper_title = ev.get("title", "")
    cache_key = f"why_match|{candidate.get('name', '')}|{first_paper_title}|{signal.student_id}"

    cached = cache_get("llm", cache_key)
    if cached:
        cached_blurb = cached.get("blurb", "")
        if cached_blurb:
            log.info("  Cache hit for why_match: %s", candidate.get('name', ''))
            return cached_blurb

    try:
        log.info("  Calling LLM for why_match: %s", candidate.get('name', ''))
        response = model.generate_content(prompt)
        try:
            blurb_json = json.loads(response.text)
            blurb = blurb_json.get("why_match", "")
        except Exception:
            # LLM returned plain text instead of JSON — use it directly
            blurb = response.text.strip()
        if blurb:
            cache_set("llm", cache_key, {"blurb": blurb})
            log.info("  LLM blurb generated for: %s", candidate.get('name', ''))
            return blurb
    except Exception as e:
        log.warning("Why-match LLM failed for %s: %s — using template fallback", candidate.get("name"), e)

    # Template-based fallback when LLM is unavailable
    blurb = _template_why_match(candidate, signal)
    cache_set("llm", cache_key, {"blurb": blurb})
    log.info("  Template fallback used for: %s", candidate.get('name', ''))
    return blurb



# ── Output schema assembly ─────────────────────────────────────────────────────

def _format_candidate_output(
    candidate: Dict,
    why_match: str,
    rank: int,
) -> Dict:
    """
    Convert a scored candidate dict into the final output schema.
    """
    evidence_out = []
    for ev in candidate.get("evidence", []):
        entry = {
            "type": ev.get("type", "paper"),
            "title": ev.get("title", ""),
            "year": ev.get("year", 0),
        }
        doi = ev.get("doi", "")
        if doi:
            # Normalise: strip any https://doi.org/ prefix already in the field
            clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
            entry["doi"] = clean_doi
            entry["url"] = f"https://doi.org/{clean_doi}"
        elif ev.get("url"):
            entry["url"] = ev["url"]
        evidence_out.append(entry)

    return {
        "rank": rank,
        "name": candidate.get("name", ""),
        "institution": candidate.get("institution", ""),
        "country": candidate.get("country", ""),
        "email": candidate.get("email", None),
        "homepage": candidate.get("homepage", "") or None,
        "research_focus": _build_research_focus(candidate),
        "tier": candidate.get("tier", "safety"),
        "score": candidate.get("score", 0.0),
        "score_breakdown": candidate.get("score_breakdown", {}),
        "qs_rank": candidate.get("qs_rank", None),
        "open_position": candidate.get("open_position", False),
        "why_match": why_match,
        "evidence": evidence_out,
        "sources": candidate.get("sources", [candidate.get("source", "unknown")]),
    }


def _build_research_focus(candidate: Dict) -> str:
    """Derive a short research focus description from evidence titles."""
    titles = [ev.get("title", "") for ev in candidate.get("evidence", [])[:3] if ev.get("title")]
    if not titles:
        return ""
    # Return the titles joined, trimmed
    return " | ".join(titles[:2])[:200]


# ── Stage 5 Main ──────────────────────────────────────────────────────────────

def generate_output(
    ranked_candidates: List[Dict[str, Any]],
    signal: StudentSignal,
) -> Dict[str, Any]:
    """
    Stage 5 entry point.

    Generates why_match blurbs for top candidates, finds emails,
    assembles final JSON, and writes it to sample_output/.

    Args:
        ranked_candidates: Scored and sorted candidates from Stage 4.
        signal:            StudentSignal from Stage 1.

    Returns:
        The complete output dict (also written to disk).
    """
    log.info("=" * 60)
    log.info("STAGE 5 — Why-Match Generation & Output Formatting")
    log.info("=" * 60)

    model = _init_gemini()
    top_candidates = ranked_candidates[:TOP_N_FOR_WHY_MATCH]

    log.info("Generating why_match blurbs for top %d candidates...", len(top_candidates))

    output_candidates = []
    for i, candidate in enumerate(ranked_candidates):
        name = candidate.get("name", "Unknown")
        
        # Only do LLM blurbs and email discovery for the top N
        if i < TOP_N_FOR_WHY_MATCH:
            log.info("  [%d/%d] Generating blurb for: %s", i + 1, TOP_N_FOR_WHY_MATCH, name)
            why_match = _generate_why_match(model, candidate, signal)

            # Email discovery (best-effort)
            paper_urls = [
                ev.get("url", "") for ev in candidate.get("evidence", []) if ev.get("url")
            ]
            if not candidate.get("email"):
                email = find_email(
                    name=name,
                    institution=candidate.get("institution", ""),
                    homepage=candidate.get("homepage") or None,
                    paper_urls=paper_urls[:3],
                )
                if email:
                    candidate["email"] = email
        else:
            why_match = ""

        formatted = _format_candidate_output(candidate, why_match, rank=i + 1)
        output_candidates.append(formatted)

    # Assemble final output document
    output = {
        "schema_version": "1.0",
        "student_id": signal.student_id,
        "student_name": signal.student_name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "total_candidates": len(output_candidates),
            "reach": sum(1 for c in output_candidates if c["tier"] == "reach"),
            "target": sum(1 for c in output_candidates if c["tier"] == "target"),
            "safety": sum(1 for c in output_candidates if c["tier"] == "safety"),
            "with_open_positions": sum(1 for c in output_candidates if c["open_position"]),
            "with_email": sum(1 for c in output_candidates if c["email"]),
        },
        "student_signal": {
            "primary_keywords": signal.research.primary_keywords,
            "thesis_topic": signal.research.thesis_topic,
            "target_countries": signal.constraints.countries,
        },
        "candidates": output_candidates,
    }

    # Write to disk
    SAMPLE_OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = SAMPLE_OUTPUT_DIR / f"{signal.student_id}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    log.info("Stage 5 | Output written to: %s", output_path)
    log.info("Stage 5 | Summary: %s", output["summary"])
    log.info("=" * 60)

    return output
