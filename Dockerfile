FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/logs \
    && chown bot:bot /app/logs
USER bot

COPY --chown=bot:bot src ./src

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--dry-run"]
