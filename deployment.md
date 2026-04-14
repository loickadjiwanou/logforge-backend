# Backend Deployment — LogForge

This guide covers deploying the LogForge backend (`logforge-backend`) to a production server.

## Prerequisites

- Python 3.12+
- MongoDB (local, Docker, or managed service such as MongoDB Atlas)
- Elasticsearch 8+ or OpenSearch (local, Docker, or managed)
- A reverse proxy such as Nginx (recommended)

---

## 1. Environment Configuration

```bash
cp .env.example .env
```

Fill in the required values:

```env
MONGO_URL=mongodb://your-mongodb-server:27017/
DB_NAME=logforge
ELASTICSEARCH_URL=http://your-elasticsearch-server:9200
JWT_SECRET=your_very_long_and_secure_random_secret

# Comma-separated list of allowed frontend origins
CORS_ORIGINS=https://your-domain.com

# OAuth — GitHub (optional)
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=

# OAuth — GitLab (optional)
GITLAB_CLIENT_ID=
GITLAB_REDIRECT_URI=
```

### Elasticsearch with TLS

1. Place the CA certificate at `config/cert/elasticsearch_ca.crt`
2. Add to `.env`:

```env
ELASTICSEARCH_URL=https://your-es-host:9200
ELASTICSEARCH_USER=elastic
ELASTICSEARCH_PASSWORD=your_password
ELASTICSEARCH_CA_CERT=config/cert/elasticsearch_ca.crt
```

---

## 2. Installation

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Running in Production

Use **Gunicorn** with Uvicorn workers for multi-process production deployments:

```bash
pip install gunicorn
gunicorn server:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

Adjust `-w` to the number of CPU cores available.

### Systemd Service (Linux)

```ini
# /etc/systemd/system/logforge-backend.service
[Unit]
Description=LogForge Backend
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/logforge-backend
EnvironmentFile=/opt/logforge-backend/.env
ExecStart=/opt/logforge-backend/venv/bin/gunicorn server:app \
    -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now logforge-backend
```

---

## 4. Databases with Docker Compose

Use Docker Compose to spin up MongoDB and Elasticsearch alongside the backend:

```yaml
version: '3.8'
services:
  mongodb:
    image: mongo:latest
    restart: always
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db

  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.0
    restart: always
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - "ES_JAVA_OPTS=-Xms512m -Xmx512m"
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data

volumes:
  mongo_data:
  es_data:
```

```bash
docker compose up -d
```

---

## 5. Deploying the Backend with Docker

A `Dockerfile` is included at the root of `logforge-backend/`.

```bash
docker build -t logforge-backend .
docker run -d \
  -p 8000:8000 \
  --env-file .env \
  logforge-backend
```

---

## 6. Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name api.your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support
    location /api/ws {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
    }
}
```

Enable HTTPS:

```bash
sudo certbot --nginx -d api.your-domain.com
```

---

## 7. Maintenance

- **Logs** — View service logs with `sudo journalctl -u logforge-backend -f`
- **MongoDB backups** — Schedule regular `mongodump` exports
- **Elasticsearch** — Monitor disk usage; logs can grow quickly. Index rotation is handled automatically (max 5 × 10 GB rolling indices)
- **Security** — Rotate `JWT_SECRET` periodically and use HTTPS in all environments
