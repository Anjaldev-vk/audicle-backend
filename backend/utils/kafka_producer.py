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
    """ Called once for each message produced to indicate delivery result. """
    if err is not None:
        logger.error(f'Message delivery failed: {err}')
    else:
        logger.info(f'Message delivered to {msg.topic()} [{msg.partition()}]')

def send_transcription_task(meeting_id, file_path, user_id):
    """
    Sends a message to the 'transcription_tasks' topic.
    """
    task_data = {
        'meeting_id': meeting_id,
        'file_path': file_path,
        'user_id': user_id,
        'status': 'pending'
    }
    
    try:
        producer.produce(
            'transcription_tasks',
            key=str(meeting_id),
            value=json.dumps(task_data).encode('utf-8'),
            callback=delivery_report
        )
        # Flush to ensure the message is sent
        producer.flush()
        return True
    except Exception as e:
        logger.error(f"Error sending message to Kafka: {e}")
        return False

def send_summarization_task(meeting_id: str, transcript_text: str) -> None:
    """
    Send summarization task to summarization_tasks Kafka topic.
    Called automatically after transcript is saved.
    """
    producer = get_producer()
    message  = {
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
