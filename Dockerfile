FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/data/greenwatch.sqlite3 TZ=Asia/Bangkok
WORKDIR /app
RUN groupadd --gid 10001 greenwatch && useradd --uid 10001 --gid greenwatch --no-create-home greenwatch \
    && mkdir /data && chown greenwatch:greenwatch /data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=greenwatch:greenwatch greenwatch ./greenwatch
COPY gunicorn.conf.py .
USER greenwatch
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1
CMD ["gunicorn", "--config", "gunicorn.conf.py", "greenwatch.web:create_app()"]
