import logging
import os

import tiktoken

from ai_client import get_ai_provider

logger = logging.getLogger(__name__)

MAX_TOKENS_PER_CHUNK = 4000
CHUNK_OVERLAP_WORDS  = 100

# ------- Summarization system prompt -------------------------------

SUMMARIZATION_SYSTEM_PROMPT = """
You are an expert meeting analyst. Analyze meeting transcripts and extract
structured, actionable information.

Always respond with valid JSON containing exactly these keys:
{
    "summary": "A clear 3-5 sentence overview of the meeting in English",
    "key_points": ["point 1", "point 2", "point 3"],
    "action_items": [
        {
            "task": "specific task",
            "assigned_to": "person name or null",
            "due_date": "date string or null",
            "priority": "high or medium or low"
        }
    ],
    "decisions": ["decision 1", "decision 2"],
    "next_steps": ["step 1", "step 2"]
}

Rules:
- Always respond in English regardless of transcript language
- Be concise and specific — no filler words
- Extract real action items with owners when mentioned
- Return empty lists when nothing found
- Never add information not present in the transcript
"""

# ------- Translation system prompt -------------------------------

TRANSLATION_SYSTEM_PROMPT = """
You are a professional translator.
Translate the provided text accurately to the requested language.
Preserve meaning, tone, and formatting exactly.
Return only the translated text — no explanation or preamble.
"""


# --------- Token counting -------------------------------

def count_tokens(text: str) -> int:
    """
    Count tokens accurately using tiktoken.
    Falls back to word estimate if tiktoken fails.
    """
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return int(len(text.split()) * 1.3)


# ------- Text chunking -------------------------------

def chunk_text(text: str) -> list[str]:
    """
    Split long transcript into overlapping chunks.
    Each chunk ends at a sentence boundary.
    Returns list of text chunks.
    """
    if count_tokens(text) <= MAX_TOKENS_PER_CHUNK:
        return [text]

    words           = text.split()
    chunks          = []
    start           = 0
    chunk_word_size = int(MAX_TOKENS_PER_CHUNK / 1.3)

    while start < len(words):
        end        = min(start + chunk_word_size, len(words))
        chunk      = " ".join(words[start:end])

        # End at sentence boundary
        last_period = chunk.rfind(".")
        if last_period > len(chunk) * 0.7:
            chunk = chunk[:last_period + 1]

        chunks.append(chunk)
        start += chunk_word_size - CHUNK_OVERLAP_WORDS

    logger.info("Text split into %d chunks", len(chunks))
    return chunks


# ------- Summarization pipeline -------------------------------

def summarize_chunk(
    provider,
    chunk: str,
    chunk_number: int,
    total_chunks: int,
) -> dict | None:
    """Summarize a single transcript chunk."""
    context = ""
    if total_chunks > 1:
        context = (
            f"This is part {chunk_number} of {total_chunks} "
            f"of a longer meeting transcript. "
        )

    user_prompt = f"{context}Analyze this meeting transcript:\n\n{chunk}"

    return provider.generate_json(
        system_prompt = SUMMARIZATION_SYSTEM_PROMPT,
        user_prompt   = user_prompt,
        temperature   = 0.3,
        max_tokens    = 1500,
    )


def merge_chunk_summaries(provider, summaries: list[dict]) -> dict:
    """Merge multiple chunk summaries into one final summary."""
    if len(summaries) == 1:
        return summaries[0]

    combined = "\n\n".join([
        f"Part {i+1}:\n"
        f"Summary: {s.get('summary', '')}\n"
        f"Key points: {', '.join(s.get('key_points', []))}\n"
        f"Action items: {s.get('action_items', [])}\n"
        f"Decisions: {', '.join(s.get('decisions', []))}\n"
        f"Next steps: {', '.join(s.get('next_steps', []))}"
        for i, s in enumerate(summaries)
    ])

    user_prompt = (
        "Merge these partial meeting summaries into one "
        "comprehensive final summary:\n\n" + combined
    )

    merged = provider.generate_json(
        system_prompt = SUMMARIZATION_SYSTEM_PROMPT,
        user_prompt   = user_prompt,
        temperature   = 0.3,
        max_tokens    = 1500,
    )

    if not merged:
        logger.warning("Merge failed — returning first chunk summary")
        return summaries[0]

    logger.info("Merged %d summaries successfully", len(summaries))
    return merged


def generate_summary(transcript_text: str) -> dict | None:
    """
    Main summarization entry point.

    Pipeline:
    1. Get AI provider from factory
    2. Chunk text if needed
    3. Summarize each chunk
    4. Merge into final summary

    Returns structured dict or None on failure.
    """
    if not transcript_text or not transcript_text.strip():
        logger.warning("Empty transcript — skipping summarization")
        return None

    provider = get_ai_provider()
    chunks   = chunk_text(transcript_text)
    total    = len(chunks)

    logger.info(
        "Starting summarization — provider: %s model: %s chunks: %d",
        provider.__class__.__name__,
        provider.get_model_name(),
        total,
    )

    summaries = []
    for i, chunk in enumerate(chunks, 1):
        result = summarize_chunk(provider, chunk, i, total)
        if result:
            summaries.append(result)
        else:
            logger.warning("Chunk %d/%d failed — continuing", i, total)

    if not summaries:
        logger.error("All chunk summarizations failed")
        return None

    return merge_chunk_summaries(provider, summaries)


# ------- Translation -------------------------------

def translate_summary(summary_text: str, target_language: str) -> str | None:
    """
    Translate summary text to any language on demand.
    Uses the same AI provider as summarization.
    Returns translated text or None on failure.
    """
    if not summary_text or not summary_text.strip():
        logger.warning("Empty text — skipping translation")
        return None

    provider    = get_ai_provider()
    user_prompt = f"Translate the following text to {target_language}:\n\n{summary_text}"

    logger.info(
        "Translating to %s using %s",
        target_language,
        provider.__class__.__name__,
    )

    return provider.generate(
        system_prompt = TRANSLATION_SYSTEM_PROMPT,
        user_prompt   = user_prompt,
        temperature   = 0.1,
        max_tokens    = 2000,
    )