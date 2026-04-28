import json
import logging
import os
from confluent_kafka import Consumer, KafkaError

from bot_runner import BotRunner

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BROKER        = os.environ.get('KAFKA_BROKER', 'kafka:29092')
KAFKA_GROUP         = os.environ.get('KAFKA_GROUP', 'bot-service-group')
BOT_TOPIC           = os.environ.get('BOT_TOPIC', 'bot_tasks')
DJANGO_INTERNAL_URL = os.environ.get('DJANGO_INTERNAL_URL', 'http://backend:8000')
INTERNAL_API_SECRET = os.environ.get('INTERNAL_API_SECRET', 'change-me')


def process_bot_message(message: dict) -> None:
    """
    Full pipeline for one bot Kafka message:
    1. Extract meeting details
    2. Launch Playwright bot for platform
    3. Join meeting, record audio
    4. Upload to S3
    5. Fire transcription task
    """
    meeting_id   = message.get('meeting_id')
    meeting_url  = message.get('meeting_url')
    platform     = message.get('platform')
    duration_cap = message.get('duration_cap', 3600)  # max 1 hour default

    logger.info(
        'Bot task received — meeting: %s platform: %s',
        meeting_id,
        platform,
    )

    if not meeting_url:
        logger.error('No meeting_url for meeting %s — skipping', meeting_id)
        _post_bot_failed(meeting_id, 'No meeting URL provided')
        return

    runner = BotRunner(
        meeting_id=meeting_id,
        meeting_url=meeting_url,
        platform=platform,
        duration_cap=duration_cap,
        django_url=DJANGO_INTERNAL_URL,
        internal_secret=INTERNAL_API_SECRET,
    )
    runner.run()


def _post_bot_failed(meeting_id: str, reason: str) -> None:
    import requests
    try:
        requests.post(
            f'{DJANGO_INTERNAL_URL}/internal/bot/status/',
            json={
                'meeting_id':    meeting_id,
                'status':        'failed',
                'error_message': reason,
            },
            headers={'X-Internal-Secret': INTERNAL_API_SECRET},
            timeout=10,
        )
    except Exception as exc:
        logger.error('Failed to POST bot status: %s', exc)


def main():
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id':          KAFKA_GROUP,
        'auto.offset.reset': 'earliest',
    })

    consumer.subscribe([BOT_TOPIC])
    logger.info('Bot worker started — listening on: %s', BOT_TOPIC)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error('Kafka error: %s', msg.error())
                continue
            try:
                message = json.loads(msg.value().decode('utf-8'))
                process_bot_message(message)
            except json.JSONDecodeError as exc:
                logger.error('Invalid JSON: %s', exc)
            except Exception as exc:
                logger.error('Unexpected error: %s', exc)
    except KeyboardInterrupt:
        logger.info('Bot worker shutting down...')
    finally:
        consumer.close()


if __name__ == '__main__':
    main()
