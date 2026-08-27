FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/study_bot.sqlite3

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
COPY entrypoint.sh /usr/local/bin/study-bot-entrypoint
RUN chmod 0755 /usr/local/bin/study-bot-entrypoint \
    && mkdir -p /app/data \
    && chown -R app:app /app

EXPOSE 3000

ENTRYPOINT ["/usr/local/bin/study-bot-entrypoint"]
CMD ["python", "main.py"]
