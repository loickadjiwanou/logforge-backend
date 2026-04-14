FROM python:3.12

# Installer les dépendances système pour psycopg2
RUN apt-get update && \
    apt-get install -y gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY .env.example .env
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip install gunicorn
COPY . .

CMD ["gunicorn", "server:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]