FROM python:3.12-slim

# The new bot service is a lightweight Kafka consumer.
# We no longer need Playwright, Chromium, Xvfb, or PulseAudio.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

CMD ["bash", "entrypoint.sh"]
