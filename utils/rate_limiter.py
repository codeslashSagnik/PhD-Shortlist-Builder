"""
utils/rate_limiter.py

Token-bucket rate limiter for all external API calls.
Each API has its own limiter instance with configured requests/second.

Usage:
    limiter = get_limiter("openalex")
    limiter.wait()          # blocks until a token is available
    response = requests.get(url)
"""

import time
import threading
from typing import Dict

from utils.logger import get_logger

log = get_logger(__name__)

# Configured limits per data source (requests per second)
_API_LIMITS: Dict[str, float] = {
    "openalex":        10.0,   # OpenAlex allows ~10 req/sec
    "semantic_scholar": 1.0,   # Semantic Scholar: ~1 req/sec without API key
    "nih":              5.0,
    "findaphd":         0.5,   # Scraping — be polite
    "scholar":          0.2,   # Google Scholar — very conservative
    "generic":          2.0,
}


class TokenBucketLimiter:
    """
    Thread-safe token bucket rate limiter.
    """

    def __init__(self, name: str, rate: float):
        """
        Args:
            name: Human-readable name (for logging).
            rate: Maximum requests per second.
        """
        self.name = name
        self.rate = rate                  # tokens per second
        self.tokens = rate                # start full
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def wait(self) -> None:
        """Block until a request token is available, then consume one."""
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                # Calculate how long until next token
                wait_time = (1.0 - self.tokens) / self.rate
            log.debug("Rate limiter [%s] sleeping %.2fs", self.name, wait_time)
            time.sleep(wait_time)


# Singleton registry of limiters
_limiters: Dict[str, TokenBucketLimiter] = {}
_registry_lock = threading.Lock()


def get_limiter(api_name: str) -> TokenBucketLimiter:
    """
    Return the singleton limiter for the given API.
    Creates one if it doesn't exist yet.
    """
    with _registry_lock:
        if api_name not in _limiters:
            rate = _API_LIMITS.get(api_name, _API_LIMITS["generic"])
            _limiters[api_name] = TokenBucketLimiter(api_name, rate)
            log.debug("Created rate limiter [%s] at %.1f req/s", api_name, rate)
        return _limiters[api_name]
