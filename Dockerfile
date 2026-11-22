FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch && pip install ".[voice]"
COPY scripts ./scripts
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && useradd -m gk && mkdir -p data voices && chown -R gk /srv
USER gk
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
