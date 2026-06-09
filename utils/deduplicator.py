"""
utils/deduplicator.py

Deduplication of raw candidate entries before verification.
The same professor can appear from multiple sources (OpenAlex + Semantic Scholar + grants).
We deduplicate by name + institution and merge evidence from all sources.
"""

from typing import List, Dict, Any
import re

from utils.logger import get_logger

log = get_logger(__name__)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _name_key(name: str) -> str:
    """Create a normalized key from a person's name."""
    parts = _normalize(name).split()
    # Sort parts so "John Smith" and "Smith John" hash the same
    return " ".join(sorted(parts))


def _institution_key(institution: str) -> str:
    """Normalize institution name for matching."""
    # Drop common suffixes that vary across sources
    text = _normalize(institution)
    for suffix in ["university of", "the university", "university", "college", "institute"]:
        text = text.replace(suffix, "").strip()
    return text


def deduplicate_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge duplicate candidate entries.

    Two entries are considered the same person if their normalized
    name AND institution key match.

    When merging, we:
    - Keep the union of all evidence (papers, grants, links)
    - Keep the union of all sources
    - Keep whichever entry has the richer metadata (email, h-index, etc.)

    Args:
        candidates: Raw list of candidate dicts from Stage 2.

    Returns:
        Deduplicated list with merged evidence.
    """
    log.info("Deduplication: received %d raw candidates", len(candidates))

    seen: Dict[str, Dict[str, Any]] = {}  # composite_key -> merged entry

    for c in candidates:
        name = c.get("name", "")
        institution = c.get("institution", "")

        if not name:
            log.debug("Skipping candidate with no name: %s", c)
            continue

        key = f"{_name_key(name)}||{_institution_key(institution)}"

        if key not in seen:
            # First time we've seen this person — deep copy the entry
            seen[key] = {
                "name": name,
                "institution": institution,
                "country": c.get("country", ""),
                "email": c.get("email", ""),
                "homepage": c.get("homepage", ""),
                "title": c.get("title", ""),
                "h_index": c.get("h_index", 0),
                "sources": list(c.get("sources", [c.get("source", "unknown")])),
                "evidence": list(c.get("evidence", [])),
                "open_position": c.get("open_position", False),
                "position_ad_text": c.get("position_ad_text", ""),
                "raw": [c],
            }
        else:
            # Merge into existing entry
            existing = seen[key]

            # Prefer non-empty values
            if not existing["email"] and c.get("email"):
                existing["email"] = c["email"]
            if not existing["homepage"] and c.get("homepage"):
                existing["homepage"] = c["homepage"]
            if not existing["title"] and c.get("title"):
                existing["title"] = c["title"]
            if c.get("h_index", 0) > existing["h_index"]:
                existing["h_index"] = c["h_index"]
            if c.get("open_position"):
                existing["open_position"] = True
                if c.get("position_ad_text"):
                    existing["position_ad_text"] = c["position_ad_text"]

            # Merge sources
            src = c.get("source", c.get("sources", ["unknown"]))
            if isinstance(src, str):
                src = [src]
            existing["sources"] = list(set(existing["sources"]) | set(src))

            # Merge evidence (papers, grants)
            new_evidence = c.get("evidence", [])
            existing_titles = {e.get("title", "") for e in existing["evidence"]}
            for ev in new_evidence:
                if ev.get("title", "") not in existing_titles:
                    existing["evidence"].append(ev)
                    existing_titles.add(ev.get("title", ""))

            existing["raw"].append(c)

    result = list(seen.values())
    log.info("Deduplication: %d unique candidates after merging", len(result))
    return result
