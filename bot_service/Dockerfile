FROM python:3.12-slim

# Install system dependencies for Playwright + ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    gnupg \
    pulseaudio \
    pulseaudio-utils \
    xvfb \
    dbus-x11 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcursor1 \
    libxi6 \
    libxtst6 \
    libxss1 \
    libglib2.0-0 \
    libdbus-1-3 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright chromium browser
RUN playwright install chromium

COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

CMD ["bash", "entrypoint.sh"]
