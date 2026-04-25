import json
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AI_BACKEND     = os.environ.get("AI_BACKEND", "gemini")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434/v1")
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL", "llama3.2")


# ── Abstract Base ─────────────────────────────────────────────────────────────

class AIProvider(ABC):
    """
    Abstract base class for AI providers.

    All providers must implement:
    - generate() → text response
    - generate_json() → structured JSON response

    This abstraction means:
    - Views and workers call provider.generate() — they don't know which AI
    - Switching AI backend = changing one .env variable
    - Testing = swap to MockProvider
    """

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> str | None:
        """
        Generate a text response.
        Returns text string or None on failure.
        """
        raise NotImplementedError

    @abstractmethod
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> dict | None:
        """
        Generate a structured JSON response.
        Returns parsed dict or None on failure.
        """
        raise NotImplementedError

    def get_model_name(self) -> str:
        raise NotImplementedError


# ── Gemini Provider ───────────────────────────────────────────────────────────

class GeminiProvider(AIProvider):
    """
    Google Gemini provider.

    Free tier:
    - gemini-2.0-flash: 15 req/min, 1500 req/day
    - gemini-1.5-pro:   2 req/min, 50 req/day

    Best for: development, cost-sensitive production
    """

    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        self.genai = genai
        self.model_name = GEMINI_MODEL
        logger.info("GeminiProvider initialized — model: %s", self.model_name)

    def get_model_name(self) -> str:
        return self.model_name

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> str | None:
        """
        Generate text using Gemini.
        Combines system + user prompt since Gemini handles
        them differently from OpenAI.
        """
        try:
            model = self.genai.GenerativeModel(
                model_name   = self.model_name,
                system_instruction = system_prompt,
            )
            response = model.generate_content(
                user_prompt,
                generation_config = self.genai.GenerationConfig(
                    temperature      = temperature,
                    max_output_tokens = max_tokens,
                ),
            )
            result = response.text.strip()
            logger.info(
                "Gemini generate successful — model: %s",
                self.model_name,
            )
            return result

        except Exception as exc:
            logger.error("Gemini generate failed: %s", exc)
            return None

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> dict | None:
        """
        Generate structured JSON using Gemini.
        Uses JSON response mime type for reliable parsing.
        """
        try:
            model = self.genai.GenerativeModel(
                model_name         = self.model_name,
                system_instruction = system_prompt,
            )
            response = model.generate_content(
                user_prompt,
                generation_config = self.genai.GenerationConfig(
                    temperature       = temperature,
                    max_output_tokens = max_tokens,
                    response_mime_type = "application/json",
                ),
            )
            text = response.text.strip()

            # Clean up markdown code blocks if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)
            logger.info(
                "Gemini generate_json successful — model: %s",
                self.model_name,
            )
            return result

        except json.JSONDecodeError as exc:
            logger.error("Gemini returned invalid JSON: %s", exc)
            return None
        except Exception as exc:
            logger.error("Gemini generate_json failed: %s", exc)
            return None


# ── OpenAI Provider ───────────────────────────────────────────────────────────

class OpenAIProvider(AIProvider):
    """
    OpenAI provider.

    Pricing:
    - gpt-4o: ~$5/1M input tokens, ~$15/1M output tokens
    - gpt-3.5-turbo: ~$0.50/1M tokens

    Best for: production, highest quality requirements
    """

    def __init__(self):
        from openai import OpenAI
        self.client     = OpenAI(api_key=OPENAI_API_KEY)
        self.model_name = OPENAI_MODEL
        logger.info("OpenAIProvider initialized — model: %s", self.model_name)

    def get_model_name(self) -> str:
        return self.model_name

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> str | None:
        try:
            response = self.client.chat.completions.create(
                model       = self.model_name,
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature = temperature,
                max_tokens  = max_tokens,
            )
            result = response.choices[0].message.content.strip()
            logger.info(
                "OpenAI generate successful — model: %s",
                self.model_name,
            )
            return result

        except Exception as exc:
            logger.error("OpenAI generate failed: %s", exc)
            return None

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> dict | None:
        try:
            response = self.client.chat.completions.create(
                model           = self.model_name,
                messages        = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature     = temperature,
                max_tokens      = max_tokens,
                response_format = {"type": "json_object"},
            )
            text   = response.choices[0].message.content.strip()
            result = json.loads(text)
            logger.info(
                "OpenAI generate_json successful — model: %s",
                self.model_name,
            )
            return result

        except json.JSONDecodeError as exc:
            logger.error("OpenAI returned invalid JSON: %s", exc)
            return None
        except Exception as exc:
            logger.error("OpenAI generate_json failed: %s", exc)
            return None


# ── Ollama Provider ───────────────────────────────────────────────────────────

class OllamaProvider(AIProvider):
    """
    Ollama local model provider.
    Uses OpenAI-compatible API — no API key needed.

    Best for: offline development, privacy-sensitive data,
              zero cost at any scale

    Setup: Add ollama service to docker-compose.yml (Phase 11)
    """

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(
            base_url = OLLAMA_BASE_URL,
            api_key  = "ollama",    # any string works
        )
        self.model_name = OLLAMA_MODEL
        logger.info("OllamaProvider initialized — model: %s", self.model_name)

    def get_model_name(self) -> str:
        return self.model_name

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> str | None:
        try:
            response = self.client.chat.completions.create(
                model       = self.model_name,
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature = temperature,
                max_tokens  = max_tokens,
            )
            result = response.choices[0].message.content.strip()
            logger.info(
                "Ollama generate successful — model: %s",
                self.model_name,
            )
            return result

        except Exception as exc:
            logger.error("Ollama generate failed: %s", exc)
            return None

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> dict | None:
        try:
            # Ollama doesn't always support response_format
            # so we parse manually
            json_prompt = user_prompt + "\n\nRespond with valid JSON only. No explanation."
            response    = self.client.chat.completions.create(
                model       = self.model_name,
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": json_prompt},
                ],
                temperature = temperature,
                max_tokens  = max_tokens,
            )
            text = response.choices[0].message.content.strip()

            # Clean markdown if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)
            logger.info(
                "Ollama generate_json successful — model: %s",
                self.model_name,
            )
            return result

        except json.JSONDecodeError as exc:
            logger.error("Ollama returned invalid JSON: %s", exc)
            return None
        except Exception as exc:
            logger.error("Ollama generate_json failed: %s", exc)
            return None


# ── Provider Factory ──────────────────────────────────────────────────────────

def get_ai_provider() -> AIProvider:
    """
    Factory function — returns the correct AI provider
    based on AI_BACKEND environment variable.

    Usage in any file:
        from ai_client import get_ai_provider
        provider = get_ai_provider()
        result   = provider.generate_json(system_prompt, user_prompt)

    Switch provider by changing .env:
        AI_BACKEND=gemini   ← free, Google
        AI_BACKEND=openai   ← paid, best quality
        AI_BACKEND=ollama   ← local, offline, free
    """
    backend = AI_BACKEND.lower().strip()

    if backend == "gemini":
        return GeminiProvider()
    elif backend == "openai":
        return OpenAIProvider()
    elif backend == "ollama":
        return OllamaProvider()
    else:
        logger.warning(
            "Unknown AI_BACKEND '%s' — falling back to Gemini",
            backend,
        )
        return GeminiProvider()