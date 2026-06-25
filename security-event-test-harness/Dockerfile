FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY harness ./harness

RUN pip install --no-cache-dir .

EXPOSE 8091

CMD ["security-event-test-harness-ui"]
