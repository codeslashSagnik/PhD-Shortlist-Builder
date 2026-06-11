"""
utils/email_finder.py

Best-effort email discovery for verified professors.
We try sources in order of reliability:
  1. University faculty directory page (highest reliability)
  2. Paper PDF metadata / abstract pages
  3. Department contact page

We NEVER guess or construct emails (e.g. firstname.lastname@university.ac.uk).
If we can't find one, we return None and log it.
"""

import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from utils.logger import get_logger
from utils.rate_limiter import get_limiter
from utils.cache_manager import cache_get, cache_set

log = get_logger(__name__)

# Regex for a plausible academic email
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.(?:ac\.uk|edu|ca|org|net|com)",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PhDShortlistBot/1.0; "
        "research tool — contact: builder@example.com)"
    )
}


def _scrape_page_for_email(url: str) -> Optional[str]:
    """
    Fetch a URL and extract the first plausible email address found in the HTML.
    Returns None if no email found or request fails.
    """
    cached = cache_get("scrape", url)
    if cached is not None:
        text = cached.get("text", "")
    else:
        limiter = get_limiter("generic")
        limiter.wait()
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            text = resp.text
            cache_set("scrape", url, {"text": text[:50000]})  # cap stored size
        except Exception as e:
            log.debug("Email finder: failed to fetch %s — %s", url, e)
            return None

    # Search raw HTML first (emails often appear as mailto: links)
    matches = _EMAIL_RE.findall(text)
    if matches:
        return matches[0]

    # Also parse visible text via BeautifulSoup
    try:
        soup = BeautifulSoup(text, "html.parser")
        visible_text = soup.get_text(" ")
        matches = _EMAIL_RE.findall(visible_text)
        if matches:
            return matches[0]
    except Exception:
        pass

    return None


def find_email(
    name: str,
    institution: str,
    homepage: Optional[str] = None,
    paper_urls: Optional[list] = None,
) -> Optional[str]:
    """
    Attempt to find a professor's email address.

    Args:
        name:        Professor's full name.
        institution: Their institution (for logging).
        homepage:    Their faculty page URL if known.
        paper_urls:  List of paper abstract/PDF URLs to try.

    Returns:
        Email string if found, else None.
    """
    log.info("Email search: %s @ %s", name, institution)

    # Source 1: faculty homepage
    if homepage:
        email = _scrape_page_for_email(homepage)
        if email:
            log.info("Email found via homepage: %s → %s", name, email)
            return email

    # Source 2: paper abstract pages
    if paper_urls:
        for url in paper_urls[:3]:  # try up to 3 papers
            email = _scrape_page_for_email(url)
            if email:
                log.info("Email found via paper page: %s → %s", name, email)
                return email

    log.info("Email not found for: %s @ %s", name, institution)
    return None
