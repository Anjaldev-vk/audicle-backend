import json
import logging
from confluent_kafka import Producer
from django.conf import settings

logger = logging.getLogger(__name__)

# Kafka configuration
# In Docker, the bootstrap server is 'kafka:29092'
KAFKA_CONFIG = {
    'bootstrap.servers': 'kafka:29092',
    'client.id': 'django-backend'
}

producer = Producer(KAFKA_CONFIG)


def get_producer():
    return producer


def delivery_report(err, msg):
    """Called once for each message produced to indicate delivery result."""
    if err is not None:
        logger.error('Message delivery failed: %s', err)
    else:
        logger.info(
            'Message delivered to %s [%s]',
            msg.topic(),
            msg.partition(),
        )


def send_transcription_task(meeting_id, file_path, user_id):
    """
    Sends a message to the 'transcription_tasks' topic.
    """
    task_data = {
        'meeting_id': meeting_id,
        'file_path':  file_path,
        'user_id':    user_id,
        'status':     'pending',
    }
    try:
        producer.produce(
            'transcription_tasks',
            key=str(meeting_id),
            value=json.dumps(task_data).encode('utf-8'),
            callback=delivery_report,
        )
        producer.flush()
        return True
    except Exception as e:
        logger.error('Error sending transcription task to Kafka: %s', e)
        return False


def send_summarization_task(meeting_id: str, transcript_text: str) -> None:
    """
    Send summarization task to summarization_tasks Kafka topic.
    Called automatically after transcript is saved.
    """
    message = {
        "meeting_id":      meeting_id,
        "transcript_text": transcript_text,
    }
    producer.produce(
        topic    = "summarization_tasks",
        key      = meeting_id,
        value    = json.dumps(message).encode("utf-8"),
        callback = delivery_report,
    )
    producer.flush()
    logger.info(
        "Summarization task sent for meeting %s",
        meeting_id,
    )


def send_bot_task(
    meeting_id: str,
    meeting_url: str,
    platform: str,
    duration_cap: int = 3600,
) -> bool:
    """
    Send a bot dispatch message to the 'bot_tasks' Kafka topic.
    Consumed by bot_service/worker.py → BotRunner.
    """
    message = {
        "meeting_id":   meeting_id,
        "meeting_url":  meeting_url,
        "platform":     platform,
        "duration_cap": duration_cap,
    }
    try:
        producer.produce(
            topic    = "bot_tasks",
            key      = meeting_id,
            value    = json.dumps(message).encode("utf-8"),
            callback = delivery_report,
        )
        producer.flush()
        logger.info("Bot task sent for meeting %s", meeting_id)
        return True
    except Exception as exc:
        logger.error("Error sending bot task to Kafka: %s", exc)
        return False


def send_embedding_task(
    transcript_id: str,
    raw_text: str,
    segments: list,
) -> None:
    """
    Send embedding task to embedding_tasks Kafka topic.
    Called automatically after transcript is saved.
    ai_worker chunks the text, embeds it, and POSTs
    vectors to /internal/rag/embed/.
    """
    message = {
        "transcript_id": transcript_id,
        "raw_text":      raw_text,
        "segments":      segments,
    }
    producer.produce(
        topic    = "embedding_tasks",
        key      = transcript_id,
        value    = json.dumps(message).encode("utf-8"),
        callback = delivery_report,
    )
    producer.flush()
    logger.info(
        "Embedding task sent for transcript %s",
        transcript_id,
    )