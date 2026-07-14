FROM python:3.11-slim

# System deps: zbar for pyzbar, libgl/glib for opencv/pillow, and build tools for easyocr/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
    libzbar0 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# users.json will be created/read here; mount a volume to persist it across container restarts
VOLUME ["/app/data"]
ENV USERS_FILE=/app/data/users.json

CMD ["python", "bot.py"]
