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
KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "kafka:9092")
KAFKA_GROUP = os.environ.get("KAFKA_GROUP", "ai-service-group")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "transcription_tasks")
DJANGO_INTERNAL_URL = os.environ.get("DJANGO_INTERNAL_URL", "http://backend:8000")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "change-me-in-production")
SUMMARIZATION_TOPIC = os.environ.get("SUMMARIZATION_TOPIC", "summarization_tasks")

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_S3_REGION", "ap-south-1")

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
    """
    Download audio file from S3 to a local temp path.
    Returns True on success, False on failure.
    """
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


# ── Django callback ───────────────────────────────────────────────────────────

def post_transcript_to_django(payload: dict) -> bool:
    """
    POST transcription results back to Django internal API.
    Uses shared secret for authentication.
    Returns True on success, False on failure.
    """
    url = f"{DJANGO_INTERNAL_URL}/api/v1/internal/transcript/complete/"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type":     "application/json",
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
        else:
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
    """POST summary results to Django internal API."""
    url = f"{DJANGO_INTERNAL_URL}/api/v1/internal/summary/complete/"
    try:
        response = requests.post(
            url,
            json    = payload,
            headers = {
                "Content-Type":      "application/json",
                "X-Internal-Secret": INTERNAL_API_SECRET,
            },
            timeout = 30,
        )
        if response.status_code == 200:
            logger.info(
                "Summary saved for meeting %s",
                payload.get("meeting_id"),
            )
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


def process_summarization_message(message: dict) -> None:
    """Full pipeline for one summarization Kafka message."""
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


# ── Transcription ─────────────────────────────────────────────────────────────

def transcribe_audio(local_path: str) -> dict | None:
    """
    Run Whisper transcription on a local audio file.

    Returns:
    {
        "text":     "full transcript text",
        "language": "en",
        "duration": 3600.0,
        "segments": [
            {
                "text":          "Hello everyone.",
                "start_seconds": 0.0,
                "end_seconds":   2.5,
                "confidence":    0.95,
            }
        ]
    }
    Returns None on failure.
    """
    try:
        logger.info("Starting Whisper transcription: %s", local_path)
        result = model.transcribe(
            local_path,
            verbose=False,
            fp16=False,   # fp16 requires GPU — safe default
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
            "Whisper transcription complete — %d segments detected language: %s",
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


# ── Main processing function ──────────────────────────────────────────────────

def process_message(message: dict) -> None:
    """
    Full pipeline for one Kafka message:
    1. Extract meeting_id and s3_key
    2. Download audio from S3
    3. Transcribe with Whisper
    4. POST results to Django
    5. Clean up temp file
    """
    meeting_id = message.get("meeting_id")
    s3_key = message.get("file_path")
    action = message.get("action", "transcribe")

    logger.info(
        "Processing message — meeting: %s action: %s",
        meeting_id,
        action,
    )

    # Bot dispatch messages have no file yet — skip transcription
    if action == "join" or not s3_key:
        logger.info(
            "Skipping transcription for meeting %s — action is '%s' or no file",
            meeting_id,
            action,
        )
        return

    # Use a temp file — automatically cleaned up after processing
    with tempfile.NamedTemporaryFile(
        suffix=".mp3",
        delete=False,
    ) as tmp_file:
        local_path = tmp_file.name

    try:
        # Step 1 — Download from S3
        downloaded = download_from_s3(s3_key, local_path)
        if not downloaded:
            post_transcript_to_django({
                "meeting_id":    meeting_id,
                "status":        "failed",
                "error_message": "Failed to download audio from S3.",
            })
            return

        # Step 2 — Transcribe
        result = transcribe_audio(local_path)
        if not result:
            post_transcript_to_django({
                "meeting_id":    meeting_id,
                "status":        "failed",
                "error_message": "Whisper transcription failed.",
            })
            return

        # Step 3 — POST results to Django
        post_transcript_to_django({
            "meeting_id":      meeting_id,
            "status":          "completed",
            "language":        result["language"],
            "raw_text":        result["text"],
            "duration_seconds": result["duration"],
            "segments":        result["segments"],
        })

    finally:
        # Always clean up temp file
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

    consumer.subscribe([KAFKA_TOPIC, SUMMARIZATION_TOPIC])

    logger.info(
        "Worker started — listening on: %s, %s",
        KAFKA_TOPIC,
        SUMMARIZATION_TOPIC,
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
