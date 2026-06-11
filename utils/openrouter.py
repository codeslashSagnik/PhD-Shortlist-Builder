import json
import time
import requests
from config.settings import OPENROUTER_API_KEY, GEMINI_TEMPERATURE
from utils.logger import get_logger

log = get_logger(__name__)

class ResourceExhausted(Exception):
    pass

class ResponseWrapper:
    def __init__(self, text):
        self.text = text

class GenerativeModel:
    def __init__(self, model_name, generation_config=None):
        self.model_name = model_name if model_name else "openrouter/free"
        self.temperature = GEMINI_TEMPERATURE
        self.json_mode = False  # only True when response_mime_type == "application/json"
        if generation_config:
            if hasattr(generation_config, 'temperature'):
                self.temperature = generation_config.temperature
            if hasattr(generation_config, 'response_mime_type') and generation_config.response_mime_type == "application/json":
                self.json_mode = True

    def generate_content(self, prompt: str):
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "PhD Shortlist Builder"
        }
        
        # Append a random nonce to bypass any strict semantic caching on OpenRouter's side
        import uuid
        prompt_with_nonce = f"{prompt}\n\n[System Nonce: {uuid.uuid4().hex}]"
        
        data = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": [
                {"role": "user", "content": prompt_with_nonce}
            ],
        }
        # Only force json_object mode for structured extraction calls (Stage 1)
        # NOT for why-match blurbs (Stage 5) — those need natural language output
        if self.json_mode:
            data["response_format"] = {"type": "json_object"}

        # Retry loop with Retry-After header support
        max_retries = 5
        for attempt in range(max_retries):
            try:
                # 30s connect timeout, 30s read timeout
                resp = requests.post(url, headers=headers, json=data, timeout=(30, 30))
            except requests.exceptions.RequestException as e:
                log.warning(f"OpenRouter connection error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                else:
                    raise ResourceExhausted(f"OpenRouter API unreachable after {max_retries} retries: {e}")
            
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                return ResponseWrapper(content)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "30"))
                log.warning(
                    "OpenRouter 429 (attempt %d/%d). Retry-After: %ds",
                    attempt + 1, max_retries, retry_after
                )
                if attempt < max_retries - 1:
                    time.sleep(retry_after)
                    continue
                else:
                    raise ResourceExhausted(
                        f"OpenRouter rate limit exceeded after {max_retries} retries"
                    )
            
            # Any other error
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                log.warning(f"OpenRouter HTTP Error (attempt {attempt + 1}/{max_retries}): {e} - Response: {resp.text}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                raise

def configure(api_key):
    pass

class types:
    class GenerationConfig:
        def __init__(self, temperature=0.7, response_mime_type=""):
            self.temperature = temperature
            self.response_mime_type = response_mime_type
