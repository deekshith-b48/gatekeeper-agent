FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
# Light by default (~300 MB image): browser does speech. Server-side voice: docker compose build --build-arg EXTRAS=voice
ARG EXTRAS=""
RUN if [ "$EXTRAS" = "voice" ]; then pip install --extra-index-url https://download.pytorch.org/whl/cpu torch && pip install ".[voice]"; else pip install .; fi
COPY scripts ./scripts
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && useradd -m gk && mkdir -p data voices && chown -R gk /srv
USER gk
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
