FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir . \
    && useradd --system --uid 10001 --create-home publisher

USER 10001:10001
ENTRYPOINT ["overseas-story-producer"]

