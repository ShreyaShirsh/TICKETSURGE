FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps: libpq for psycopg2 at runtime (build-essential only needed
# if you uncomment mysqlclient/oracledb build-from-source in requirements.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# The default command runs the ASGI app (HTTP + WebSockets) via Daphne.
# Override the command to run `celery -A ticketsurge worker` for the
# worker service — see docker-compose.yml.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "ticketsurge.asgi:application"]
