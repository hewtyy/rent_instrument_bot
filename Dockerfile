FROM python:3.11-slim
WORKDIR /app

# Ensure timezone data is available for ZoneInfo/cron triggers
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot/ .

ENV TZ=Asia/Tokyo
CMD ["python", "main.py"]


