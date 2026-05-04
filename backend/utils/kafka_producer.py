import json
import logging
from confluent_kafka import Producer
from django.conf import settings

logger = logging.getLogger(__name__)

# Kafka configuration
KAFKA_CONFIG = {
    'bootstrap.servers': 'kafka:29092',
    'client.id': 'django-backend'
}

_producer = None


def get_producer():
    global _producer
    if _producer is None:
        try:
            _producer = Producer(KAFKA_CONFIG)
        except Exception as e:
            logger.error("Failed to initialize Kafka producer: %s", e)
            # In testing or if Kafka is down, we might want a dummy producer
            class DummyProducer:
                def produce(self, *args, **kwargs): pass
                def flush(self, *args, **kwargs): pass
            _producer = DummyProducer()
    return _producer


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
        p = get_producer()
        p.produce(
            'transcription_tasks',
            key=str(meeting_id),
            value=json.dumps(task_data).encode('utf-8'),
            callback=delivery_report,
        )
        p.flush()
        return True
    except Exception as e:
        logger.error('Error sending transcription task to Kafka: %s', e)
        return False


def send_summarization_task(meeting_id: str, transcript_text: str) -> None:
    """
    Send summarization task to summarization_tasks Kafka topic.
    """
    message = {
        "meeting_id":      meeting_id,
        "transcript_text": transcript_text,
    }
    try:
        p = get_producer()
        p.produce(
            topic    = "summarization_tasks",
            key      = meeting_id,
            value    = json.dumps(message).encode("utf-8"),
            callback = delivery_report,
        )
        p.flush()
        logger.info("Summarization task sent for meeting %s", meeting_id)
    except Exception as e:
        logger.error("Error sending summarization task: %s", e)


def send_bot_task(
    meeting_id: str,
    meeting_url: str,
    platform: str,
    duration_cap: int = 3600,
) -> bool:
    """
    Send a bot dispatch message to the 'bot_tasks' Kafka topic.
    """
    message = {
        "meeting_id":   meeting_id,
        "meeting_url":  meeting_url,
        "platform":     platform,
        "duration_cap": duration_cap,
    }
    try:
        p = get_producer()
        p.produce(
            topic    = "bot_tasks",
            key      = meeting_id,
            value    = json.dumps(message).encode("utf-8"),
            callback = delivery_report,
        )
        p.flush()
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
    """
    message = {
        "transcript_id": transcript_id,
        "raw_text":      raw_text,
        "segments":      segments,
    }
    try:
        p = get_producer()
        p.produce(
            topic    = "embedding_tasks",
            key      = transcript_id,
            value    = json.dumps(message).encode("utf-8"),
            callback = delivery_report,
        )
        p.flush()
        logger.info("Embedding task sent for transcript %s", transcript_id)
    except Exception as e:
        logger.error("Error sending embedding task: %s", e)