FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Hosted mode: never let visitors browse or ingest the container's filesystem.
ENV FILEWHISPER_DISABLE_BROWSE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["sh", "-c", "uvicorn filewhisper.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
