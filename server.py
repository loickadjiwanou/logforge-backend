from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from pathlib import Path
from contextlib import asynccontextmanager
import os
import logging
import asyncio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from database import client, db
from es_client import log_store
from ws_manager import ws_manager
from auth import hash_password
from routes.auth_routes import router as auth_router
from routes.project_routes import router as project_router
from routes.channel_routes import router as channel_router
from routes.log_routes import router as log_router, log_worker
from routes.settings_routes import router as settings_router
from routes.roles_routes import router as roles_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def is_app_setup():
    """Check if at least one admin account exists."""
    admin_count = await db.users.count_documents({"role": "admin"})
    return admin_count > 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    await log_store.init(db)
    # Start the log ingestion worker
    worker_task = asyncio.create_task(log_worker())
    
    # Start the agent key expiration notifier loop
    from scripts.agent_key_notifier import check_expiring_keys_loop
    notifier_task = asyncio.create_task(check_expiring_keys_loop())

    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.projects.create_index("id", unique=True)
    await db.projects.create_index("api_key", unique=True)
    await db.channels.create_index("id", unique=True)

    # App is now setup manually through /api/setup/admin

    from utils.gelf import GelfUdpProtocol
    from routes.log_routes import ingestion_queue
    loop = asyncio.get_running_loop()
    try:
        udp_transport, udp_protocol = await loop.create_datagram_endpoint(
            lambda: GelfUdpProtocol(ingestion_queue, db, ws_manager, log_store, None),
            local_addr=('0.0.0.0', 12201)
        )
        logger.info("GELF UDP Server started on port 12201")
    except OSError as e:
        if e.errno == 98: # Address already in use
            logger.info("GELF UDP Server port 12201 already in use, skipping (likely another worker is listening)")
            udp_transport = None
        else:
            raise e

    logger.info("LogForge API started")
    yield
    if udp_transport:
        udp_transport.close()
    worker_task.cancel()
    notifier_task.cancel()
    client.close()
    await log_store.close()


description = """
LogForge API provides a robust and scalable log management and ingestion platform.
It supports multiple ingestion protocols (HTTP, GELF UDP) and provides real-time log monitoring through WebSockets.

### Features:
* **Log Ingestion**: High-performance log capture using Elasticsearch/OpenSearch.
* **Organization**: Manage Projects and Channels with granular permissions.
* **Real-time**: Live log streaming via WebSockets.
* **Security**: JWT-based authentication with role-based access control (RBAC).
* **Multi-tenant**: Secure isolated environments per project.
"""

tags_metadata = [
    {"name": "auth", "description": "Operations for user authentication and account management."},
    {"name": "projects", "description": "Project lifecycle management and API key configuration."},
    {"name": "channels", "description": "Manage logical log groupings (channels) within projects."},
    {"name": "logs", "description": "Query, search, and ingest log data."},
    {"name": "settings", "description": "Global application configuration and branding."},
    {"name": "roles", "description": "Fine-grained permissions and role management."},
]

app = FastAPI(
    title="LogForge API",
    description=description,
    version="0.1.8",
    contact={
        "name": "LogForge Support",
        "url": "https://github.com/loickadjiwanou/logforge-backend",
    },
    license_info={
        "name": "MIT",
    },
    openapi_tags=tags_metadata,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(project_router, prefix="/api/projects", tags=["projects"])
app.include_router(channel_router, prefix="/api/channels", tags=["channels"])
app.include_router(log_router, prefix="/api/logs", tags=["logs"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(roles_router, prefix="/api/roles", tags=["roles"])


@app.get("/api")
async def root():
    return {"message": "LogForge API", "status": "running", "version": "0.1.8"}


@app.get("/api/health")
async def health_check():
    from datetime import datetime, timezone
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "logforge-api"
    }


class SetupAdminRequest(BaseModel):
    name: str
    email: str
    password: str
    app_name: str = "LogForge"
    primary_color: str = "#10b981"
    theme: str = "dark"


@app.get("/api/setup/status")
async def get_setup_status():
    setup = await is_app_setup()
    return {"is_setup": setup}


@app.post("/api/setup/admin")
async def setup_admin(req: SetupAdminRequest):
    if await is_app_setup():
        raise HTTPException(status_code=400, detail="App is already setup")

    from datetime import datetime, timezone
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    admin_doc = {
        "id": str(uuid.uuid4()),
        "email": req.email,
        "password": hash_password(req.password),
        "name": req.name,
        "created_at": now,
        "theme": req.theme,
        "auth_provider": "email",
        "role": "admin",
        "permissions": [],
        "allowed_projects": [],
        "dashboard_config": {}
    }
    await db.users.insert_one(admin_doc)
    logger.info(f"Admin account created during setup: {req.email}")

    # Save App Settings
    await db.settings.update_one(
        {"type": "app_settings"},
        {"$set": {
            "type": "app_settings",
            "app_name": req.app_name,
            "primary_color": req.primary_color,
            "logo_url": None,
            "language": "en"
        }},
        upsert=True
    )
    logger.info(f"App settings initialized during setup: {req.app_name}")
    
    from auth import create_token
    access_token = create_token(admin_doc["id"])
    
    # Return user info similar to login response
    from routes.auth_routes import UserResponse
    user_data = UserResponse(
        id=admin_doc["id"],
        email=admin_doc["email"],
        name=admin_doc["name"],
        created_at=admin_doc["created_at"],
        theme=admin_doc["theme"],
        role=admin_doc["role"],
        permissions=admin_doc["permissions"],
        allowed_projects=admin_doc["allowed_projects"],
        dashboard_config=admin_doc["dashboard_config"]
    )
    
    return {
        "message": "Admin account created successfully",
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_data.model_dump()
    }


@app.websocket("/api/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "subscribe":
                project_id = data.get("project_id")
                if project_id:
                    ws_manager.subscribe(websocket, project_id)
            elif data.get("type") == "unsubscribe":
                project_id = data.get("project_id")
                if project_id:
                    ws_manager.unsubscribe(websocket, project_id)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)
