import json
import logging
import os
import time
import whisper
from confluent_kafka import Consumer, KafkaError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-worker")

# Load Whisper model (CPU version)
logger.info("Loading Whisper model...")
model = whisper.load_model("base")
logger.info("Whisper model loaded successfully.")

# Kafka Configuration
KAFKA_CONFIG = {
    'bootstrap.servers': 'kafka:29092',
    'group.id': 'ai-service-group',
    'auto.offset.reset': 'earliest'
}

def start_worker():
    consumer = Consumer(KAFKA_CONFIG)
    consumer.subscribe(['transcription_tasks'])

    logger.info("AI Service Worker is active and waiting for audio tasks...")

    try:
        while True:
            msg = consumer.poll(1.0)

            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    logger.error(f"Consumer error: {msg.error()}")
                    break

            try:
                task_data = json.loads(msg.value().decode('utf-8'))
                meeting_id = task_data.get('meeting_id')
                # The shared media volume is mounted at /app/media inside the container
                relative_file_path = task_data.get('file_path') 
                
                # Check for absolute vs relative paths
                if relative_file_path.startswith('/media/'):
                    file_path = relative_file_path
                else:
                    file_path = os.path.join('/app/media', relative_file_path.lstrip('/'))

                logger.info(f"Processing transcription for meeting {meeting_id}. File: {file_path}")

                if not os.path.exists(file_path):
                    logger.error(f"File not found: {file_path}")
                    continue

                # Run Whisper Transcription
                start_time = time.time() if 'time' in globals() else 0
                result = model.transcribe(file_path)
                transcription_text = result['text']
                
                logger.info(f"Transcription complete for meeting {meeting_id}!")
                logger.info(f"Result Preview: {transcription_text[:100]}...")

                # TODO: In the next step, we will send this back to Django 
                # or save it directly to the database.

            except Exception as e:
                logger.error(f"Error during transcription: {e}")

    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()

if __name__ == "__main__":
    start_worker()
