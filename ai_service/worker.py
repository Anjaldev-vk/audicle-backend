import json
import logging
import os
import tempfile

import requests
import whisper
from confluent_kafka import Consumer, KafkaError
from summarizer import generate_summary

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Config from environment ───────────────────────────────────────────────────
KAFKA_BROKER        = os.environ.get("KAFKA_BROKER", "kafka:9092")
KAFKA_GROUP         = os.environ.get("KAFKA_GROUP", "ai-service-group")
KAFKA_TOPIC         = os.environ.get("KAFKA_TOPIC", "transcription_tasks")
DJANGO_INTERNAL_URL = os.environ.get("DJANGO_INTERNAL_URL", "http://backend:8000")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "change-me-in-production")
SUMMARIZATION_TOPIC = os.environ.get("SUMMARIZATION_TOPIC", "summarization_tasks")
EMBEDDING_TOPIC     = os.environ.get("EMBEDDING_TOPIC", "embedding_tasks")

AWS_ACCESS_KEY_ID     = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_BUCKET_NAME       = os.environ.get("AWS_STORAGE_BUCKET_NAME")
AWS_REGION            = os.environ.get("AWS_S3_REGION", "ap-south-1")

AI_BACKEND  = os.environ.get("AI_BACKEND", "gemini").lower().strip()
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL    = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434/v1")

# ── Chunk settings ────────────────────────────────────────────────────────────
CHUNK_SIZE    = 512   # tokens (approximate — we use words)
CHUNK_OVERLAP = 50    # words overlap between chunks

# ── Load Whisper model once at startup ────────────────────────────────────────
logger.info("Loading Whisper base model...")
model = whisper.load_model("base")
logger.info("Whisper model loaded successfully")


# ── S3 helpers ────────────────────────────────────────────────────────────────

def get_s3_client():
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def download_from_s3(s3_key: str, local_path: str) -> bool:
    try:
        client = get_s3_client()
        client.download_file(
            Bucket=AWS_BUCKET_NAME,
            Key=s3_key,
            Filename=local_path,
        )
        logger.info("Downloaded %s to %s", s3_key, local_path)
        return True
    except ClientError as exc:
        logger.error("Failed to download %s: %s", s3_key, exc)
        return False


# ── Django callbacks ──────────────────────────────────────────────────────────

def post_transcript_to_django(payload: dict) -> bool:
    url = f"{DJANGO_INTERNAL_URL}/api/v1/internal/transcript/complete/"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type":      "application/json",
                "X-Internal-Secret": INTERNAL_API_SECRET,
            },
            timeout=30,
        )
        if response.status_code == 200:
            logger.info(
                "Transcript saved to Django for meeting %s",
                payload.get("meeting_id"),
            )
            return True
        logger.error(
            "Django returned %d for meeting %s: %s",
            response.status_code,
            payload.get("meeting_id"),
            response.text,
        )
        return False
    except requests.RequestException as exc:
        logger.error(
            "Failed to POST transcript to Django for meeting %s: %s",
            payload.get("meeting_id"),
            exc,
        )
        return False


def post_summary_to_django(payload: dict) -> bool:
    url = f"{DJANGO_INTERNAL_URL}/api/v1/internal/summary/complete/"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type":      "application/json",
                "X-Internal-Secret": INTERNAL_API_SECRET,
            },
            timeout=30,
        )
        if response.status_code == 200:
            logger.info("Summary saved for meeting %s", payload.get("meeting_id"))
            return True
        logger.error(
            "Django returned %d for summary: %s",
            response.status_code,
            response.text,
        )
        return False
    except requests.RequestException as exc:
        logger.error("Failed to POST summary: %s", exc)
        return False


def post_embeddings_to_django(payload: dict) -> bool:
    """POST embedding chunks to Django internal RAG API."""
    url = f"{DJANGO_INTERNAL_URL}/internal/rag/embed/"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type":      "application/json",
                "X-Internal-Secret": INTERNAL_API_SECRET,
            },
            timeout=60,  # longer timeout — large payloads
        )
        if response.status_code == 201:
            logger.info(
                "Embeddings stored for transcript %s",
                payload.get("transcript_id"),
            )
            return True
        logger.error(
            "Django returned %d for embeddings: %s",
            response.status_code,
            response.text,
        )
        return False
    except requests.RequestException as exc:
        logger.error("Failed to POST embeddings: %s", exc)
        return False


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    Split transcript text into overlapping chunks.
    Uses word-level splitting to approximate token count.

    Returns list of:
    {
        "chunk_text":    "...",
        "chunk_index":   0,
        "start_seconds": None,  # populated when segments are available
        "end_seconds":   None,
    }
    """
    words = text.split()
    chunks = []
    start = 0
    index = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunks.append({
            "chunk_text":    " ".join(chunk_words),
            "chunk_index":   index,
            "start_seconds": None,
            "end_seconds":   None,
        })
        index += 1
        start += chunk_size - overlap  # slide window with overlap

    logger.info("Chunked transcript into %d chunks", len(chunks))
    return chunks


def assign_timestamps_to_chunks(chunks: list[dict], segments: list[dict]) -> list[dict]:
    """
    Map segment timestamps to chunks using word position alignment.
    Best-effort — assigns start/end seconds from nearest segment.
    """
    if not segments:
        return chunks

    # Build word → timestamp map from segments
    word_timestamps = []
    for seg in segments:
        words = seg["text"].strip().split()
        if not words:
            continue
        per_word_duration = (seg["end_seconds"] - seg["start_seconds"]) / max(len(words), 1)
        for i, _ in enumerate(words):
            word_timestamps.append({
                "start": seg["start_seconds"] + i * per_word_duration,
                "end":   seg["start_seconds"] + (i + 1) * per_word_duration,
            })

    word_pos = 0
    for chunk in chunks:
        chunk_word_count = len(chunk["chunk_text"].split())
        chunk_start_pos  = word_pos
        chunk_end_pos    = min(word_pos + chunk_word_count - 1, len(word_timestamps) - 1)

        if chunk_start_pos < len(word_timestamps):
            chunk["start_seconds"] = round(word_timestamps[chunk_start_pos]["start"], 3)
        if chunk_end_pos < len(word_timestamps):
            chunk["end_seconds"] = round(word_timestamps[chunk_end_pos]["end"], 3)

        word_pos += chunk_word_count - CHUNK_OVERLAP

    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> list[float] | None:
    """
    Generate embedding vector using the configured AI backend.
    Gemini  → text-embedding-004  (768 dims)
    OpenAI  → text-embedding-ada-002 (1536 dims)
    Ollama  → nomic-embed-text (768 dims)
    """
    try:
        if AI_BACKEND == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_document",  # document side of RAG
            )
            return result["embedding"]

        elif AI_BACKEND == "openai":
            from openai import OpenAI
            client   = OpenAI(api_key=OPENAI_API_KEY)
            response = client.embeddings.create(
                model="text-embedding-ada-002",
                input=text,
            )
            return response.data[0].embedding

        elif AI_BACKEND == "ollama":
            from openai import OpenAI
            client   = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
            response = client.embeddings.create(
                model="nomic-embed-text",
                input=text,
            )
            return response.data[0].embedding

        else:
            logger.error("Unknown AI_BACKEND for embedding: %s", AI_BACKEND)
            return None

    except Exception as exc:
        logger.error("Embedding failed: %s", exc)
        return None


# ── Embedding pipeline ────────────────────────────────────────────────────────

def process_embedding_message(message: dict) -> None:
    """
    Full pipeline for one embedding Kafka message:
    1. Extract transcript_id, raw_text, segments
    2. Chunk the transcript text
    3. Assign timestamps from segments
    4. Embed each chunk
    5. POST all chunks to Django internal API
    """
    transcript_id = message.get("transcript_id")
    raw_text      = message.get("raw_text")
    segments      = message.get("segments", [])

    logger.info("Starting embedding pipeline for transcript %s", transcript_id)

    if not raw_text:
        logger.error(
            "No raw_text for transcript %s — skipping embedding",
            transcript_id,
        )
        return

    # Step 1 — Chunk
    chunks = chunk_text(raw_text)

    # Step 2 — Assign timestamps
    chunks = assign_timestamps_to_chunks(chunks, segments)

    # Step 3 — Embed each chunk
    embedded_chunks = []
    for chunk in chunks:
        embedding = get_embedding(chunk["chunk_text"])
        if embedding is None:
            logger.error(
                "Failed to embed chunk %d for transcript %s — aborting",
                chunk["chunk_index"],
                transcript_id,
            )
            return
        embedded_chunks.append({
            "chunk_text":    chunk["chunk_text"],
            "chunk_index":   chunk["chunk_index"],
            "start_seconds": chunk["start_seconds"],
            "end_seconds":   chunk["end_seconds"],
            "embedding":     embedding,
        })

    logger.info(
        "Embedded %d chunks for transcript %s",
        len(embedded_chunks),
        transcript_id,
    )

    # Step 4 — POST to Django
    post_embeddings_to_django({
        "transcript_id": str(transcript_id),
        "chunks":        embedded_chunks,
    })


# ── Existing pipelines ────────────────────────────────────────────────────────

def process_summarization_message(message: dict) -> None:
    meeting_id      = message.get("meeting_id")
    transcript_text = message.get("transcript_text")

    logger.info("Starting summarization for meeting %s", meeting_id)

    if not transcript_text:
        post_summary_to_django({
            "meeting_id":    meeting_id,
            "status":        "failed",
            "error_message": "No transcript text provided.",
        })
        return

    result = generate_summary(transcript_text)

    if not result:
        post_summary_to_django({
            "meeting_id":    meeting_id,
            "status":        "failed",
            "error_message": "Summarization failed.",
        })
        return

    post_summary_to_django({
        "meeting_id":   meeting_id,
        "status":       "completed",
        "summary":      result.get("summary", ""),
        "key_points":   result.get("key_points", []),
        "action_items": result.get("action_items", []),
        "decisions":    result.get("decisions", []),
        "next_steps":   result.get("next_steps", []),
    })


def transcribe_audio(local_path: str) -> dict | None:
    try:
        logger.info("Starting Whisper transcription: %s", local_path)
        result = model.transcribe(
            local_path,
            verbose=False,
            fp16=False,
        )

        segments = [
            {
                "text":          seg["text"].strip(),
                "start_seconds": round(seg["start"], 3),
                "end_seconds":   round(seg["end"], 3),
                "confidence":    round(abs(seg.get("avg_logprob", 0)), 3),
            }
            for seg in result.get("segments", [])
        ]

        logger.info(
            "Whisper transcription complete — %d segments, language: %s",
            len(segments),
            result.get("language", "unknown"),
        )

        return {
            "text":     result.get("text", "").strip(),
            "language": result.get("language", "en"),
            "duration": result["segments"][-1]["end"] if result.get("segments") else None,
            "segments": segments,
        }

    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return None


def process_message(message: dict) -> None:
    meeting_id = message.get("meeting_id")
    s3_key     = message.get("file_path")
    action     = message.get("action", "transcribe")

    logger.info(
        "Processing message — meeting: %s action: %s",
        meeting_id,
        action,
    )

    if action == "join" or not s3_key:
        logger.info(
            "Skipping transcription for meeting %s — action is '%s' or no file",
            meeting_id,
            action,
        )
        return

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
        local_path = tmp_file.name

    try:
        downloaded = download_from_s3(s3_key, local_path)
        if not downloaded:
            post_transcript_to_django({
                "meeting_id":    meeting_id,
                "status":        "failed",
                "error_message": "Failed to download audio from S3.",
            })
            return

        result = transcribe_audio(local_path)
        if not result:
            post_transcript_to_django({
                "meeting_id":    meeting_id,
                "status":        "failed",
                "error_message": "Whisper transcription failed.",
            })
            return

        post_transcript_to_django({
            "meeting_id":       meeting_id,
            "status":           "completed",
            "language":         result["language"],
            "raw_text":         result["text"],
            "duration_seconds": result["duration"],
            "segments":         result["segments"],
        })

    finally:
        if os.path.exists(local_path):
            os.remove(local_path)
            logger.info("Cleaned up temp file: %s", local_path)


# ── Kafka consumer loop ───────────────────────────────────────────────────────

def main():
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id":          KAFKA_GROUP,
        "auto.offset.reset": "earliest",
    })

    consumer.subscribe([KAFKA_TOPIC, SUMMARIZATION_TOPIC, EMBEDDING_TOPIC])

    logger.info(
        "Worker started — listening on: %s, %s, %s",
        KAFKA_TOPIC,
        SUMMARIZATION_TOPIC,
        EMBEDDING_TOPIC,
    )

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka error: %s", msg.error())
                continue
            try:
                message = json.loads(msg.value().decode("utf-8"))
                topic   = msg.topic()

                if topic == KAFKA_TOPIC:
                    process_message(message)
                elif topic == SUMMARIZATION_TOPIC:
                    process_summarization_message(message)
                elif topic == EMBEDDING_TOPIC:
                    process_embedding_message(message)

            except json.JSONDecodeError as exc:
                logger.error("Invalid JSON: %s", exc)
            except Exception as exc:
                logger.error("Unexpected error: %s", exc)
    except KeyboardInterrupt:
        logger.info("Worker shutting down...")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()