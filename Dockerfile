# Stallo backend (Django + DRF) as a production image: gunicorn, static files by WhiteNoise, SQLite on a volume.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=False \
    DJANGO_DB_PATH=/data/db.sqlite3 \
    DJANGO_MEDIA_ROOT=/data/media

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Gather the admin's static files into /app/staticfiles. The key below is used only for this step.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

# Run as a normal user; /data holds the database and must be writable by it.
RUN useradd --create-home --uid 10001 app && mkdir /data && chown -R app:app /data /app
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=4)" || exit 1

# Applies migrations, loads the menu on the very first start, then runs the command below.
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
# One worker with threads: SQLite is happiest with a single process, and rate limits stay accurate.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--access-logfile", "-"]
