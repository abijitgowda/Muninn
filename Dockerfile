FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml .
COPY muninn/ muninn/
COPY Muninn-Demo/ Muninn-Demo/
COPY wiki.example.yaml .env.example scripts/crontab ./
COPY scripts/docker-entrypoint.sh /usr/local/bin/

# Install cron + muninn
RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir ".[all]"

VOLUME /vault
ENV OLLAMA_HOST=http://host.docker.internal:11434

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "19828"]
