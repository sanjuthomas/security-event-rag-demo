FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY security_event_qdrant_etl ./security_event_qdrant_etl

RUN pip install --no-cache-dir .

EXPOSE 8090

CMD ["security-event-monitor"]
