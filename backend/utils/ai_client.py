import json
import logging
import os

from django.conf import settings

logger = logging.getLogger("utils")


def get_ai_provider():
    """
    Django-side AI provider factory.
    Used by views that need direct AI calls
    (e.g. translation endpoint, RAG search, chat).

    Returns provider instance based on AI_BACKEND setting.
    """
    backend = settings.AI_BACKEND.lower().strip()

    if backend == "gemini":
        return _get_gemini_provider()
    elif backend == "openai":
        return _get_openai_provider()
    elif backend == "ollama":
        return _get_ollama_provider()
    else:
        logger.warning(
            "Unknown AI_BACKEND '%s' — falling back to Gemini",
            backend,
        )
        return _get_gemini_provider()


def _get_gemini_provider():
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)

    class GeminiProvider:
        def generate(self, system_prompt, user_prompt, temperature=0.1, max_tokens=2000):
            try:
                model = genai.GenerativeModel(
                    model_name=settings.GEMINI_MODEL,
                    system_instruction=system_prompt,
                )
                response = model.generate_content(
                    user_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    ),
                )
                return response.text.strip()
            except Exception as exc:
                logger.error("Gemini generate failed: %s", exc)
                return None

        def generate_json(self, system_prompt, user_prompt, temperature=0.3, max_tokens=1500):
            try:
                model = genai.GenerativeModel(
                    model_name=settings.GEMINI_MODEL,
                    system_instruction=system_prompt,
                )
                response = model.generate_content(
                    user_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                        response_mime_type="application/json",
                    ),
                )
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                return json.loads(text)
            except Exception as exc:
                logger.error("Gemini generate_json failed: %s", exc)
                return None

        def embed(self, text):
            """
            Generate embedding vector for RAG.
            Gemini: text-embedding-004 → 768 dimensions.
            """
            try:
                result = genai.embed_content(
                    model="models/text-embedding-004",
                    content=text,
                    task_type="retrieval_query",
                )
                return result["embedding"]
            except Exception as exc:
                logger.error("Gemini embed failed: %s", exc)
                raise

        def complete(self, prompt):
            """
            Single-prompt completion for RAG answer generation.
            No system/user split — prompt contains full context.
            """
            try:
                model = genai.GenerativeModel(model_name=settings.GEMINI_MODEL)
                response = model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=0.1,
                        max_output_tokens=2000,
                    ),
                )
                return response.text.strip()
            except Exception as exc:
                logger.error("Gemini complete failed: %s", exc)
                raise

    return GeminiProvider()


def _get_openai_provider():
    from openai import OpenAI
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    class OpenAIProvider:
        def generate(self, system_prompt, user_prompt, temperature=0.1, max_tokens=2000):
            try:
                response = client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                logger.error("OpenAI generate failed: %s", exc)
                return None

        def generate_json(self, system_prompt, user_prompt, temperature=0.3, max_tokens=1500):
            try:
                response = client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                return json.loads(response.choices[0].message.content)
            except Exception as exc:
                logger.error("OpenAI generate_json failed: %s", exc)
                return None

        def embed(self, text):
            """
            Generate embedding vector for RAG.
            OpenAI: text-embedding-ada-002 → 1536 dimensions.
            """
            try:
                response = client.embeddings.create(
                    model="text-embedding-ada-002",
                    input=text,
                )
                return response.data[0].embedding
            except Exception as exc:
                logger.error("OpenAI embed failed: %s", exc)
                raise

        def complete(self, prompt):
            """
            Single-prompt completion for RAG answer generation.
            """
            try:
                response = client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2000,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                logger.error("OpenAI complete failed: %s", exc)
                raise

    return OpenAIProvider()


def _get_ollama_provider():
    from openai import OpenAI
    client = OpenAI(
        base_url=settings.OLLAMA_BASE_URL,
        api_key="ollama",
    )

    class OllamaProvider:
        def generate(self, system_prompt, user_prompt, temperature=0.1, max_tokens=2000):
            try:
                response = client.chat.completions.create(
                    model=settings.OLLAMA_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                logger.error("Ollama generate failed: %s", exc)
                return None

        def generate_json(self, system_prompt, user_prompt, temperature=0.3, max_tokens=1500):
            try:
                json_prompt = user_prompt + "\n\nRespond with valid JSON only."
                response = client.chat.completions.create(
                    model=settings.OLLAMA_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = response.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                return json.loads(text)
            except Exception as exc:
                logger.error("Ollama generate_json failed: %s", exc)
                return None

        def embed(self, text):
            """
            Generate embedding vector for RAG.
            Ollama: nomic-embed-text → 768 dimensions.
            """
            try:
                response = client.embeddings.create(
                    model="nomic-embed-text",
                    input=text,
                )
                return response.data[0].embedding
            except Exception as exc:
                logger.error("Ollama embed failed: %s", exc)
                raise

        def complete(self, prompt):
            """
            Single-prompt completion for RAG answer generation.
            """
            try:
                response = client.chat.completions.create(
                    model=settings.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2000,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                logger.error("Ollama complete failed: %s", exc)
                raise

    return OllamaProvider()