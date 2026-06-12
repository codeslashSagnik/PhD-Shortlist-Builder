"""
utils/gemini.py  —  Native Google Gemini SDK wrapper (using the new google.genai SDK)

Provides a thin wrapper around the google-genai SDK with:
  - Automatic retry with exponential backoff for 429/503 errors
  - Compatible interface with the rest of the pipeline
  - Rate-limit-aware request pacing

Default model: gemini-2.5-flash (free tier, fast)
"""

import time
from google import genai
from google.genai import types as genai_types

from config.settings import GEMINI_API_KEY, GEMINI_TEMPERATURE
from utils.logger import get_logger

log = get_logger(__name__)

# ── Exceptions ────────────────────────────────────────────────────────────────

class ResourceExhausted(Exception):
    """Raised when Gemini API rate limit is exceeded after all retries."""
    pass


# ── SDK Configuration ─────────────────────────────────────────────────────────

_client = None

def configure(api_key: str = None):
    """Configure the Gemini client with the given API key."""
    global _client
    key = api_key or GEMINI_API_KEY
    if not key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Add it to your .env file. "
            "Get a free key at: https://aistudio.google.com/app/apikey"
        )
    _client = genai.Client(api_key=key)
    log.debug("Gemini client configured successfully")


def _ensure_configured():
    """Auto-configure the client if not already done."""
    global _client
    if _client is None:
        configure()


# ── Response Wrapper ──────────────────────────────────────────────────────────

class ResponseWrapper:
    """Wraps the Gemini SDK response to provide a consistent .text interface."""
    def __init__(self, text: str):
        self.text = text


# ── GenerativeModel Wrapper ──────────────────────────────────────────────────

class GenerativeModel:
    """
    Wrapper to match the original GenerativeModel interface,
    but using the modern google.genai Client under the hood.
    
    Handles 429 and 503 errors with exponential backoff.
    """

    def __init__(self, model_name: str = None, generation_config=None):
        _ensure_configured()

        self.model_name = model_name or "gemini-2.5-flash"
        self.temperature = GEMINI_TEMPERATURE
        self.response_mime_type = None

        if generation_config:
            if hasattr(generation_config, 'temperature'):
                self.temperature = generation_config.temperature
            if hasattr(generation_config, 'response_mime_type') and generation_config.response_mime_type:
                self.response_mime_type = generation_config.response_mime_type

        # Build the native SDK generation config
        kwargs = {"temperature": self.temperature}
        if self.response_mime_type:
            kwargs["response_mime_type"] = self.response_mime_type
            
        self._config = genai_types.GenerateContentConfig(**kwargs)
        log.debug("Gemini model initialised: %s (temp=%.2f)", self.model_name, self.temperature)

    def generate_content(self, prompt: str) -> ResponseWrapper:
        """
        Generate content with automatic retry on rate limits.
        
        Retries up to 5 times with exponential backoff.
        Raises ResourceExhausted if all retries are exhausted.
        """
        max_retries = 5
        base_delay = 10  # seconds

        for attempt in range(max_retries):
            try:
                response = _client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=self._config
                )

                text = response.text
                if not text:
                    log.warning("Gemini returned empty text (possibly blocked). Attempt %d/%d",
                                attempt + 1, max_retries)
                    if attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    return ResponseWrapper("")

                return ResponseWrapper(text)

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = any(k in error_str for k in [
                    "429", "resource_exhausted", "resourceexhausted",
                    "rate limit", "quota", "too many requests"
                ])
                is_transient = any(k in error_str for k in [
                    "503", "service unavailable", "unavailable", "deadline exceeded",
                    "internal error", "500"
                ])

                if is_rate_limit or is_transient:
                    delay = min(base_delay * (2 ** attempt), 60)
                    error_type = "rate limit" if is_rate_limit else "transient error"
                    log.warning(
                        "Gemini %s (attempt %d/%d): %s — retrying in %ds",
                        error_type, attempt + 1, max_retries, str(e)[:200], delay
                    )
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        continue
                    raise ResourceExhausted(
                        f"Gemini API rate limit exceeded after {max_retries} retries: {e}"
                    )

                # Non-retryable error
                log.error("Gemini API error (non-retryable): %s", e)
                raise


# ── Types (compatible interface) ──────────────────────────────────────────────

class types:
    """Namespace for generation config types, matching the pipeline's expected interface."""
    class GenerationConfig:
        def __init__(self, temperature: float = 0.7, response_mime_type: str = ""):
            self.temperature = temperature
            self.response_mime_type = response_mime_type
