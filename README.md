# LogForge — Backend

FastAPI backend for LogForge, the self-hosted real-time log management platform.

## Tech Stack

- **FastAPI** (Python 3.12) — REST API and WebSocket server
- **MongoDB** — Metadata: users, projects, channels, configuration
- **Elasticsearch / OpenSearch** — High-performance log indexing and full-text search
- **Motor** — Async MongoDB driver
- **PyJWT / bcrypt** — Authentication
- **aiosmtplib** — Async email (SMTP alerts)

## Prerequisites

- Python 3.12+
- MongoDB (local or Docker)
- Elasticsearch 8+ or OpenSearch (local or Docker)

## Installation

```bash
python3.12 -m venv venv
source venv/bin/activate   # Linux / macOS
pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `MONGO_URL` | MongoDB connection string (e.g. `mongodb://localhost:27017/`) |
| `DB_NAME` | Database name (default: `logforge`) |
| `ELASTICSEARCH_URL` | ES / OpenSearch URL (e.g. `http://localhost:9200`) |
| `ELASTICSEARCH_USER` | ES username (TLS setups) |
| `ELASTICSEARCH_PASSWORD` | ES password (TLS setups) |
| `ELASTICSEARCH_CA_CERT` | Path to CA cert (default: `config/cert/elasticsearch_ca.crt`) |
| `JWT_SECRET` | Long, random secret for JWT signing |
| `CORS_ORIGINS` | Comma-separated allowed origins (e.g. `http://localhost:3000`) |
| `GITHUB_CLIENT_ID` | OAuth — GitHub App client ID |
| `GITHUB_CLIENT_SECRET` | OAuth — GitHub App client secret |
| `GITLAB_CLIENT_ID` | OAuth — GitLab App client ID |
| `GITLAB_REDIRECT_URI` | OAuth — GitLab redirect URI |

### Elasticsearch TLS / Secure Connection

1. Place your CA certificate at `config/cert/elasticsearch_ca.crt`
2. Set `ELASTICSEARCH_URL` to `https://...`, and fill in `ELASTICSEARCH_USER`, `ELASTICSEARCH_PASSWORD`, and `ELASTICSEARCH_CA_CERT`

## Development

```bash
uvicorn server:app --reload
```

API runs at `http://localhost:8000`. Interactive docs available at `http://localhost:8000/docs`.

To listen on all interfaces (useful for mobile app testing):

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

## Production

```bash
pip install gunicorn
gunicorn server:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

See [deployment.md](deployment.md) for the full production guide including Docker, Nginx, and SSL.

## API Overview

| Prefix | Description |
|---|---|
| `/api/auth` | Login, signup, OAuth, password reset |
| `/api/projects` | Project management, API key rotation |
| `/api/channels` | Channel management |
| `/api/logs` | Log ingestion (single, batch, GELF, agent) and querying |
| `/api/settings` | App settings, SMTP, alert rules, agent keys |
| `/api/roles` | User management and permissions |
| `/api/ws/logs` | WebSocket — real-time log streaming |
| `/api/setup` | Initial onboarding |

## Features

### Core Platform
- **Interactive Onboarding** — Secure 3-step setup flow for initial configuration and administrator creation with live preview
- **Multi-Project Management** — Separate logs by application with dedicated API keys per project
- **Logical Channels** — Segment logs within a project (e.g. `auth`, `billing`, `worker`)
- **Powerful Log Explorer** — Full-text search, filtering by level, channel, project, tags, and time range
- **Log Grouping** — Automatic aggregation of similar logs to quickly identify recurring issues
- **Real-time Dashboards** — Live charts driven by WebSocket — project-scoped connections reduce network overhead
- **User-Specific Dashboards** — Every user can add, remove, and customise their own set of dashboard widgets
- **Session Replay** — Record user actions leading up to an error and replay the session directly from the dashboard (RRWeb)
- **Enriched Metadata** — Automatic capture of OS, hardware stats (CPU, RAM), client IP, and user context on every event

### Ingestion & Protocols
- **High-Performance Ingestion** — Asynchronous queue with a background worker to handle massive traffic spikes (263.84 req/sec, 100k logs at 100% success)
- **Batch HTTP Ingestion** — `POST /api/logs/ingest/batch` for high-throughput clients
- **GELF Protocol Support** — Native ingestion via HTTP and UDP (port 12201), compatible with Docker, Fluentd, and Graylog shippers
- **Docker Agent** — Platform-wide log collection for all Docker containers with no code modification; intelligent label-based routing per project and channel
- **Official SDKs (v0.1.8)** — `npm install logforge-js` and `pip install logforge-python[requests]`; both support batch ingestion, retry logic, user context, and exception capture

### Alerting & Notifications
- **Alerting System** — Configure email notification rules (SMTP) based on log severity
- **Notification Lifecycle Tracker** — Log detail pages display whether an SMTP alert was successfully dispatched via a permanent visual badge
- **Actionable Alert Emails** — Professional HTML templates with direct CTA links and explicit attribution of the administrator who triggered the change
- **Agent Key Expiration Alerts** — Automated email notifications before agent keys expire

### User & Access Management
- **Granular RBAC** — Distinct Admin and Member roles; administrators dynamically assign, revoke, and manage per-project access
- **Administrative User Management** — Enable, disable, and manage user accounts from the global settings panel
- **Unified User Management UI** — Single high-performance table with dedicated modal controls for permissions
- **Admin Password Recovery** — "Contact Administrator" flow for members; dedicated CLI recovery script for system administrators
- **Global Notification Preferences** — Administrators define which administrative events trigger emails (role changes, account activation/deactivation, etc.)
- **Automated Access Notifications** — Instant email notifications via SMTP when access or permissions are granted or revoked
- **Social Login (OAuth)** — Secure sign-in via GitHub and GitLab

### Customisation & UX
- **Dynamic App Customisation** — Upload a logo, set an application name, and pick a brand primary color from the Settings panel
- **Dark / Light Mode & Theming** — Full dark/light support with a customisable accent color, configurable at setup and at runtime
- **Collapsible Sidebar** — Smooth collapsible side menu with icon-only mode to reclaim horizontal space
- **Internationalization (i18n)** — Native English and French support across the entire platform
- **Built-in Help & FAQ** — Integrated guidance system directly in the UI
- **Global Pagination** — All list views capped at 99 items for consistent performance

### Storage & Infrastructure
- **Automated Index Rotation** — Rolling Elasticsearch indices (max 5 × 10 GB) with atomic retention policy
- **Automated Channel Management** — Dynamic on-the-fly creation of new log channels on first reception
- **Scalable Real-Time Updates** — Project-level WebSocket isolation; clients only receive events for their active project
- **Project Identification** — Clickable project UUIDs and copy buttons across all views for easier agent setup

### Mobile
- **Premium Mobile App** — Fully-featured iOS & Android app (React Native + Expo) with "Liquid Glass" UI (iOS), haptic feedback, and real-time log monitoring

---

## Performance

Stress test results (100k logs, 200 parallel workers):

| Metric | Value |
|---|---|
| Throughput | **263.84 req/sec** |
| Duration | 379s |
| Data volume | 26.4 MB |
| Success rate | **100%** |

## Log Ingestion Protocols

- **HTTP** — `POST /api/logs/ingest` (single) and `POST /api/logs/ingest/batch`
- **GELF HTTP** — `POST /api/logs/gelf`
- **GELF UDP** — port `12201`
- **Docker Agent** — `POST /api/logs/ingest/agent`

## Project Structure

```
logforge-backend/
├── routes/         # Auth, logs, projects, channels, settings, roles
├── utils/          # Email, GELF helpers
├── templates/      # HTML email templates
├── scripts/        # CLI utilities (reset admin password, agent key notifier)
├── tests/          # Integration tests and load scripts
├── server.py       # FastAPI app entry point
├── database.py     # MongoDB client
├── es_client.py    # Elasticsearch / OpenSearch client
├── ws_manager.py   # WebSocket connection manager
└── auth.py         # JWT and password hashing
```

## License

MIT — see [LICENCE](LICENCE)
