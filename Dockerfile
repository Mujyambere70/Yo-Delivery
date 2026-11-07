# Yo Delivery on Fly.io (or any Docker host). The data lives in PostgreSQL (Neon): set the DATABASE_URL secret.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# version 5: build the read-only database of 334 470 places of the world once, inside the image
# (from data/geo/places.tsv.gz, about 20 seconds), so the first visitor does not wait for it
RUN python -c "import geo; geo.ensure(); print(geo.stats()['total'], 'places')"
EXPOSE 8080
# Gunicorn (production server), not the Flask development server.
# 1 worker + threads: the minute jobs (courier network, messages, backups) run in one process;
# the long timeout gives the very first start time to fill an empty database with the demo data.
CMD gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 180
