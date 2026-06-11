"""
pipeline/discovery.py  —  Stage 2: Candidate Discovery

Queries multiple data sources in parallel and returns a raw pool of
~500–1000 candidate professors. Intentionally over-inclusive —
noise is removed in Stage 3.

Sources:
  - OpenAlex API        (papers by research area, author affiliations)
  - Semantic Scholar    (author profiles, citation counts)
  - NIH Reporter        (active grants, PI names — US-adjacent, useful for cross-ref)
  - FindAPhD.com        (open PhD positions with eligibility text)

Input:  StudentSignal
Output: List[Dict] — raw candidate entries
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

from models.student_signal import StudentSignal
from utils.logger import get_logger
from utils.cache_manager import cache_get, cache_set
from utils.rate_limiter import get_limiter
from config.settings import (
    SEMANTIC_SCHOLAR_API_KEY,
    MAX_CANDIDATES_DISCOVERY,
)

log = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PhDShortlistBot/1.0; "
        "academic research tool)"
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# OpenAlex
# ─────────────────────────────────────────────────────────────────────────────

def _query_openalex(keyword: str, countries: List[str]) -> List[Dict]:
    """
    Search OpenAlex for papers matching a keyword, filtered to target countries.
    Returns candidate dicts (one per unique author in results).
    """
    # Map country names to OpenAlex country codes
    country_codes = {"UK": "GB", "Canada": "CA"}
    target_codes = [country_codes.get(c, c) for c in countries]
    filter_str = f"institutions.country_code:{'|'.join(target_codes)}"

    url = "https://api.openalex.org/works"
    params = {
        "search": keyword,
        "filter": filter_str,
        "per-page": 50,
        "sort": "publication_date:desc",
        "select": "id,title,publication_year,authorships,abstract_inverted_index,doi",
        "mailto": "phd.shortlist.builder@example.com",  # polite pool
    }

    cache_key = f"openalex|{keyword}|{','.join(target_codes)}"
    cached = cache_get("openalex", cache_key)
    if cached is not None:
        raw_results = cached
    else:
        limiter = get_limiter("openalex")
        limiter.wait()
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            raw_results = resp.json().get("results", [])
            cache_set("openalex", cache_key, raw_results)
            log.debug("OpenAlex [%s]: %d papers returned", keyword, len(raw_results))
        except Exception as e:
            log.warning("OpenAlex query failed for '%s': %s", keyword, e)
            return []

    candidates = []
    for paper in raw_results:
        title = paper.get("title", "")
        year = paper.get("publication_year", 0)
        doi = paper.get("doi", "")

        # Reconstruct abstract from inverted index if available
        abstract = ""
        inv_idx = paper.get("abstract_inverted_index")
        if inv_idx:
            try:
                words = sorted(inv_idx.items(), key=lambda x: x[1][0])
                abstract = " ".join(w for w, _ in words)
            except Exception:
                abstract = ""

        for authorship in paper.get("authorships", []):
            author = authorship.get("author", {})
            author_name = author.get("display_name", "")
            if not author_name:
                continue

            for inst in authorship.get("institutions", []):
                inst_name = inst.get("display_name", "")
                inst_country = (inst.get("country_code") or "").upper()

                # Map back to canonical country name
                country_map = {"GB": "UK", "CA": "Canada"}
                country = country_map.get(inst_country, inst_country)

                if country not in countries:
                    continue

                candidates.append({
                    "name": author_name,
                    "institution": inst_name,
                    "country": country,
                    "source": "openalex",
                    "sources": ["openalex"],
                    "email": "",
                    "homepage": author.get("orcid", ""),
                    "title": "",
                    "h_index": 0,
                    "open_position": False,
                    "evidence": [{
                        "type": "paper",
                        "title": title,
                        "year": year,
                        "doi": doi,
                        "abstract": abstract[:500],
                        "url": doi if doi else "",
                    }],
                })

    log.info("OpenAlex [%s]: %d candidate entries extracted", keyword, len(candidates))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Scholar
# ─────────────────────────────────────────────────────────────────────────────

def _query_semantic_scholar(keyword: str, countries: List[str]) -> List[Dict]:
    """
    Search Semantic Scholar for papers matching the keyword.
    Returns candidate dicts. Country filtering is post-hoc (S2 doesn't filter by country).
    """
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": keyword,
        "limit": 50,
        "fields": "title,year,authors,abstract,externalIds,venue",
    }
    headers = dict(_HEADERS)
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    cache_key = f"s2|{keyword}"
    cached = cache_get("semantic", cache_key)
    if cached is not None:
        papers = cached
    else:
        limiter = get_limiter("semantic_scholar")
        limiter.wait()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            papers = resp.json().get("data", [])
            cache_set("semantic", cache_key, papers)
            log.debug("Semantic Scholar [%s]: %d papers", keyword, len(papers))
        except Exception as e:
            log.warning("Semantic Scholar query failed for '%s': %s", keyword, e)
            return []

    candidates = []
    for paper in papers:
        title = paper.get("title", "")
        year = paper.get("year", 0)
        doi = paper.get("externalIds", {}).get("DOI", "")
        abstract = paper.get("abstract", "") or ""

        for author in paper.get("authors", []):
            author_name = author.get("name", "")
            author_id = author.get("authorId", "")
            if not author_name:
                continue

            candidates.append({
                "name": author_name,
                "institution": "",           # populated by author enrichment below
                "country": "",               # will be filtered in Stage 3
                "source": "semantic_scholar",
                "sources": ["semantic_scholar"],
                "email": "",
                "homepage": f"https://www.semanticscholar.org/author/{author_id}" if author_id else "",
                "title": "",
                "h_index": 0,
                "open_position": False,
                "_s2_author_id": author_id,  # for potential enrichment
                "evidence": [{
                    "type": "paper",
                    "title": title,
                    "year": year,
                    "doi": doi,
                    "abstract": abstract[:500],
                    "url": f"https://doi.org/{doi}" if doi else "",
                }],
            })

    log.info("Semantic Scholar [%s]: %d candidate entries", keyword, len(candidates))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# FindAPhD
# ─────────────────────────────────────────────────────────────────────────────

def _query_findaphd(keyword: str, countries: List[str]) -> List[Dict]:
    """
    Query FindAPhD.com for open PhD positions matching the keyword.
    Returns candidate entries with open_position=True and the ad text.
    """
    country_map = {"UK": "gb", "Canada": "ca"}
    candidates = []

    for country in countries:
        country_code = country_map.get(country, "")
        if not country_code:
            continue

        url = "https://www.findaphd.com/phds/?"
        params = {
            "Keywords": keyword,
            "CountryCode": country_code,
            "PhdPosition": "1",
        }

        cache_key = f"findaphd|{keyword}|{country_code}"
        cached = cache_get("findaphd", cache_key)
        if cached is not None:
            html = cached.get("html", "")
        else:
            limiter = get_limiter("findaphd")
            limiter.wait()
            try:
                resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                html = resp.text
                cache_set("findaphd", cache_key, {"html": html[:100000]})
            except Exception as e:
                log.warning("FindAPhD query failed for '%s'/%s: %s", keyword, country_code, e)
                continue

        # Parse listings with BeautifulSoup
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            listings = soup.select(".phd-result__details")
            log.debug("FindAPhD [%s/%s]: %d listings in HTML", keyword, country_code, len(listings))

            for listing in listings[:20]:  # cap per keyword
                title_tag = listing.select_one("h3 a, h2 a")
                supervisor_tag = listing.select_one(".phd-result__key-info")
                institution_tag = listing.select_one(".phd-result__key-info--institution")

                ad_title = title_tag.get_text(strip=True) if title_tag else ""
                supervisor = supervisor_tag.get_text(strip=True) if supervisor_tag else ""
                institution = institution_tag.get_text(strip=True) if institution_tag else ""
                ad_url = "https://www.findaphd.com" + title_tag["href"] if title_tag and title_tag.get("href") else ""

                if not ad_title:
                    continue

                candidates.append({
                    "name": supervisor if supervisor else "Unknown Supervisor",
                    "institution": institution,
                    "country": country,
                    "source": "findaphd",
                    "sources": ["findaphd"],
                    "email": "",
                    "homepage": ad_url,
                    "title": "",
                    "h_index": 0,
                    "open_position": True,
                    "position_ad_text": ad_title,
                    "evidence": [{
                        "type": "open_position",
                        "title": ad_title,
                        "year": 2024,
                        "url": ad_url,
                        "abstract": "",
                    }],
                })
        except Exception as e:
            log.warning("FindAPhD parse error for '%s'/%s: %s", keyword, country_code, e)

    log.info("FindAPhD [%s]: %d open position candidates", keyword, len(candidates))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 Main
# ─────────────────────────────────────────────────────────────────────────────

def discover_candidates(signal: StudentSignal) -> List[Dict[str, Any]]:
    """
    Stage 2 entry point.

    Fans out searches across all data sources in parallel using
    ThreadPoolExecutor. Each (keyword, source) pair is one task.

    Args:
        signal: StudentSignal from Stage 1.

    Returns:
        Raw candidate list (before deduplication and filtering).
    """
    log.info("=" * 60)
    log.info("STAGE 2 — Candidate Discovery")
    log.info("=" * 60)

    keywords = signal.research.primary_keywords + signal.research.secondary_keywords[:3]
    countries = signal.constraints.countries

    log.info("Keywords to search: %s", keywords)
    log.info("Target countries  : %s", countries)

    tasks = []
    for kw in keywords:
        tasks.append(("openalex", kw))
        tasks.append(("semantic_scholar", kw))
        tasks.append(("findaphd", kw))

    log.info("Launching %d parallel search tasks...", len(tasks))

    all_candidates: List[Dict] = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for source, kw in tasks:
            if source == "openalex":
                fut = executor.submit(_query_openalex, kw, countries)
            elif source == "semantic_scholar":
                fut = executor.submit(_query_semantic_scholar, kw, countries)
            elif source == "findaphd":
                fut = executor.submit(_query_findaphd, kw, countries)
            futures[fut] = (source, kw)

        for fut in as_completed(futures):
            source, kw = futures[fut]
            try:
                results = fut.result()
                all_candidates.extend(results)
                log.info("  [%s | '%s'] → %d candidates", source, kw, len(results))
            except Exception as e:
                log.error("  [%s | '%s'] → ERROR: %s", source, kw, e)

    all_candidates = [c for c in all_candidates if len(c.get("evidence", [])) > 0]
    log.info("Stage 2 | Total raw candidates collected: %d", len(all_candidates))

    # Cap to prevent memory issues
    if len(all_candidates) > MAX_CANDIDATES_DISCOVERY:
        log.info("Stage 2 | Capping to %d candidates", MAX_CANDIDATES_DISCOVERY)
        all_candidates = all_candidates[:MAX_CANDIDATES_DISCOVERY]

    log.info("=" * 60)
    return all_candidates
