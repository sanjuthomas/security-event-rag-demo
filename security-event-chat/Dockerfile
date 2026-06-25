FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY security_event_chat ./security_event_chat

RUN pip install --no-cache-dir .

EXPOSE 8092

CMD ["security-event-chat"]
