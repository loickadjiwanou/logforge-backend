from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks, Request
from pydantic import BaseModel, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone as dt_timezone
import uuid
import hashlib
import logging

from database import db
from auth import get_current_user, verify_api_key, verify_agent_key
from es_client import log_store
from ws_manager import ws_manager
from utils.gelf import parse_gelf_payload

router = APIRouter()
logger = logging.getLogger(__name__)


class AgentLogEntry(BaseModel):
    project_id: Optional[str] = None
    channel: str = "default"
    level: str = "info"
    message: str
    timestamp: Optional[datetime] = None
    environment: str = "production"
    metadata: Dict[str, Any] = {}

    @field_validator('timestamp', mode='before')
    @classmethod
    def ensure_utc(cls, v):
        if not v: return v
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
        else:
            dt = v
        if dt.tzinfo is None:
            return dt.replace(tzinfo=dt_timezone.utc)
        return dt.astimezone(dt_timezone.utc)


class AgentLogBatch(BaseModel) :
    logs: List[AgentLogEntry]


class LogIngest(BaseModel):
    level: str = "info"
    message: str
    channel: str = "default"
    environment: str = "production"
    metadata: Dict[str, Any] = {}
    stack_trace: Optional[str] = None
    user_info: Optional[Dict[str, Any]] = None
    device_info: Optional[Dict[str, Any]] = None
    tags: List[str] = []


class LogBatchIngest(BaseModel):
    logs: List[LogIngest]


class ReplayIngest(BaseModel):
    log_id: str
    events: List[Dict[str, Any]]


class LogIngestResponse(BaseModel):
    id: str
    status: str
    emailSent: bool


class LogBatchResponse(BaseModel):
    count: int
    results: List[LogIngestResponse]


class LogResponse(BaseModel):
    id: str
    level: str
    message: str
    channel: str
    environment: str
    project_id: Optional[str] = None
    project_name: str
    timestamp: datetime

    @field_validator('timestamp', mode='before')
    @classmethod
    def ensure_utc(cls, v):
        if not v: return v
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
        else:
            dt = v
        if dt.tzinfo is None:
            return dt.replace(tzinfo=dt_timezone.utc)
        return dt.astimezone(dt_timezone.utc)
    metadata: Dict[str, Any] = {}
    stack_trace: Optional[str] = None
    user_info: Optional[Dict[str, Any]] = None
    device_info: Optional[Dict[str, Any]] = None
    tags: List[str] = []
    has_replay: bool = False
    grouped_hash: Optional[str] = None
    alert_email_sent: Optional[bool] = None
    ingest_protocol: Optional[str] = None
    gelf_version: Optional[str] = None
    source_host: Optional[str] = None
    syslog_level: Optional[int] = None


class LogListResponse(BaseModel):
    logs: List[LogResponse]
    total: int
    page: int
    size: int


class LogGroupResponse(BaseModel):
    id: Optional[str] = None
    grouped_hash: str
    level: str
    message: str
    count: int
    first_seen: datetime
    last_seen: datetime

    @field_validator('first_seen', 'last_seen', mode='before')
    @classmethod
    def ensure_utc_val(cls, v):
        if not v: return v
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
        else:
            dt = v
        if dt.tzinfo is None:
            return dt.replace(tzinfo=dt_timezone.utc)
        return dt.astimezone(dt_timezone.utc)
    project_id: Optional[str] = None
    channel: str


class LogGroupListResponse(BaseModel):
    groups: List[LogGroupResponse]
    total: int
    page: int
    size: int


def generate_log_hash(level: str, message: str, stack_trace: str = None) -> str:
    # We now group strictly by level and message as requested
    content = f"{level.lower()}:{message.strip()}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


ALLOWED_LEVELS = {'debug', 'info', 'warning', 'error', 'critical'}

def validate_log_level(level: str):
    if level.lower() not in ALLOWED_LEVELS:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid log level: '{level}'. Allowed values are: {', '.join(ALLOWED_LEVELS)}"
        )

import asyncio

# Ingestion Queue for scalability
ingestion_queue = asyncio.Queue()

async def log_worker():
    """Background worker to process log ingestion from the queue."""
    logger.info("Log ingestion worker started")
    while True:
        log_doc = None
        try:
            log_doc = await ingestion_queue.get()

            # 1. Automatic Channel Creation
            channel_name = log_doc.get('channel', 'default')
            project_id = log_doc.get('project_id')

            existing_channel = await db.channels.find_one({
                "name": channel_name,
                "project_id": project_id,
                "company_id": log_doc.get("company_id")
            })

            if not existing_channel:
                channel_id = str(uuid.uuid4())
                await db.channels.insert_one({
                    "id": channel_id,
                    "name": channel_name,
                    "description": f"Automatically created via ingestion from {log_doc.get('project_name')}",
                    "project_id": project_id,
                    "company_id": log_doc.get("company_id"),
                    "created_at": datetime.now(dt_timezone.utc).isoformat()
                })
                logger.info(f"Auto-created channel '{channel_name}' for project {project_id}")

            # 2. Index to Elasticsearch
            await log_store.index_log(log_doc)

            # 3. Broadcast to relevant WebSocket project subscribers
            await ws_manager.broadcast(project_id, {"type": "new_log", "log": log_doc})
        except Exception as e:
            logger.error(f"Error in log worker: {e}")
            await asyncio.sleep(1)
        finally:
            if log_doc is not None:
                ingestion_queue.task_done()


@router.post("/ingest", 
             response_model=LogIngestResponse,
             summary="Ingest Single Log",
             description="Ingest a single log entry. Authenticates via API Key (X-API-Key header).")
async def ingest_log(req: LogIngest, request: Request, background_tasks: BackgroundTasks, project=Depends(verify_api_key)):
    validate_log_level(req.level)
    log_id = str(uuid.uuid4())
    now = datetime.now(dt_timezone.utc)
    grouped_hash = generate_log_hash(req.level, req.message, req.stack_trace)

    device_info = req.device_info or {}
    if "ip" not in device_info:
        device_info["ip"] = request.client.host

    log_doc = {
        "id": log_id, "level": req.level.lower(), "message": req.message,
        "channel": req.channel,
        "environment": req.environment or project.get('environment', 'production'),
        "project_id": project['id'], "project_name": project['name'],
        "company_id": project.get('company_id'),
        "metadata": req.metadata, "stack_trace": req.stack_trace,
        "user_info": req.user_info, "device_info": device_info,
        "tags": req.tags, "timestamp": now, "grouped_hash": grouped_hash
    }
    # Evaluate alerts and fire emails asynchronously using BackgroundTasks
    email_sent = await _check_alerts(log_doc, background_tasks)
    log_doc["alert_email_sent"] = email_sent
    
    # Push to queue and return immediately for high scalability
    await ingestion_queue.put(log_doc)
    return {"id": log_id, "status": "queued", "emailSent": email_sent}


@router.post("/ingest/batch", 
             response_model=LogBatchResponse,
             summary="Ingest Batch of Logs",
             description="Ingest multiple log entries in a single request for higher throughput.")
async def ingest_batch(req: LogBatchIngest, request: Request, background_tasks: BackgroundTasks, project=Depends(verify_api_key)):
    client_ip = request.client.host
    results = []
    for log_req in req.logs:
        validate_log_level(log_req.level)
        log_id = str(uuid.uuid4())
        now = datetime.now(dt_timezone.utc)
        grouped_hash = generate_log_hash(log_req.level, log_req.message, log_req.stack_trace)

        device_info = log_req.device_info or {}
        if "ip" not in device_info:
            device_info["ip"] = client_ip

        log_doc = {
            "id": log_id, "level": log_req.level.lower(), "message": log_req.message,
            "channel": log_req.channel,
            "environment": log_req.environment or project.get('environment', 'production'),
            "project_id": project['id'], "project_name": project['name'],
            "company_id": project.get('company_id'),
            "metadata": log_req.metadata, "stack_trace": log_req.stack_trace,
            "user_info": log_req.user_info, "device_info": device_info,
            "tags": log_req.tags, "timestamp": now, "grouped_hash": grouped_hash
        }
        email_sent = await _check_alerts(log_doc, background_tasks)
        log_doc["alert_email_sent"] = email_sent
        await ingestion_queue.put(log_doc)
        results.append({"id": log_id, "status": "queued", "emailSent": email_sent})
    return {"count": len(results), "results": results}


@router.post("/ingest/agent",
             response_model=LogBatchResponse,
             summary="Ingest Logs from Agent",
             description="Ingest multiple log entries from a global Docker agent. Authenticates via Agent Key (Bearer token).")
async def ingest_agent(req: AgentLogBatch, request: Request, background_tasks: BackgroundTasks, agent=Depends(verify_agent_key)):
    results = []
    project_cache = {}
    agent_company_id = agent.get("company_id")

    # Batch optimization: Fetch company-scoped rules and SMTP config once
    try:
        alert_rules = await db.alert_rules.find({"enabled": True, "company_id": agent_company_id}, {"_id": 0}).to_list(100)
        smtp_config = await db.settings.find_one({"type": "smtp", "enabled": True, "company_id": agent_company_id}, {"_id": 0})
    except Exception as e:
        logger.error(f"Failed to fetch batch metadata: {e}")
        alert_rules = []
        smtp_config = None

    for log_req in req.logs:
        try:
            pid = log_req.project_id
            project = None
            
            if pid:
                if pid not in project_cache:
                    project_cache[pid] = await db.projects.find_one({"id": pid, "company_id": agent_company_id}, {"_id": 0})
                project = project_cache[pid]
            
            # Determine project info (use defaults for global docker logs)
            if project:
                p_id = project['id']
                p_name = project['name']
                env = log_req.environment or project.get('environment', 'production')
            else:
                p_id = None
                p_name = "Docker Logs"
                env = log_req.environment or "production"

            # Simple validation
            log_id = str(uuid.uuid4())
            now = datetime.now(dt_timezone.utc).isoformat()
            
            # Ensure metadata has source: docker-agent
            meta = log_req.metadata or {}
            meta["source"] = "docker-agent"
            
            # We assume messages are pre-processed by the agent
            grouped_hash = generate_log_hash(log_req.level, log_req.message, None)

            log_doc = {
                "id": log_id,
                "level": log_req.level.lower(),
                "message": log_req.message,
                "channel": log_req.channel,
                "environment": env,
                "project_id": p_id,
                "project_name": p_name,
                "company_id": agent_company_id,
                "metadata": meta,
                "stack_trace": None,
                "user_info": None,
                "device_info": {"ip": request.client.host if request.client else "unknown", "agent_id": agent.get('id')},
                "tags": ["docker"],
                "timestamp": log_req.timestamp or now,
                "grouped_hash": grouped_hash
            }
            
            email_sent = _check_alerts_batch_sync(log_doc, alert_rules, smtp_config, background_tasks)
            log_doc["alert_email_sent"] = email_sent
            await ingestion_queue.put(log_doc)
            results.append({"id": log_id, "status": "queued", "emailSent": email_sent})
        except Exception as e:
            logger.error(f"Failed to process individual log in batch: {e}")
            continue
        
    return {"count": len(results), "results": results}


@router.get("/docker",
            response_model=LogListResponse,
            summary="List Global Docker Logs")
async def list_docker_logs(
    page: int = Query(1, ge=1),
    size: int = Query(30, ge=1, le=100),
    level: Optional[str] = None,
    container_name: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(get_current_user)
):
    """
    Get all logs originating from Docker Agents across the entire platform.
    Requires Admin role.
    """
    # Admin role or view_docker_logs permission required
    if user.get('role') != 'admin' and 'view_docker_logs' not in user.get('permissions', []):
        raise HTTPException(status_code=403, detail="Not authorized to view global docker logs")

    filters = {"source": "docker-agent", "company_id": user.get("company_id")}
    if level: filters["level"] = level.lower()
    if container_name: filters["container_name"] = container_name
    if date_from: filters["date_from"] = date_from
    if date_to: filters["date_to"] = date_to
    
    return await log_store.search_logs(filters, page, size, search)


@router.get("/docker/containers",
            summary="List Monitored Containers")
async def list_monitored_containers(project_id: str = Query(None), user=Depends(get_current_user)):
    """
    Get a unique list of container names currently monitored by Docker Agents.
    If project_id is provided, returns containers only for that project.
    """
    company_id = user.get("company_id")
    # Permission check
    if project_id:
        # Check if user has access to THIS project within their company
        p_query = {"id": project_id, "company_id": company_id}
        if user.get('role') != 'admin':
            p_query["$or"] = [
                {"user_id": user['id']},
                {"id": {"$in": user.get('allowed_projects', [])}}
            ]
        project = await db.projects.find_one(p_query)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
    else:
        # Global view requires admin or specific permission
        if user.get('role') != 'admin' and 'view_docker_logs' not in user.get('permissions', []):
            raise HTTPException(status_code=403, detail="Not authorized for global Docker view")

    # Build queries — always scope by company_id
    es_query = {"bool": {"must": [
        {"term": {"metadata.source": "docker-agent"}},
        {"term": {"company_id": company_id}}
    ]}}
    mongo_query = {"metadata.source": "docker-agent", "company_id": company_id}

    if project_id:
        es_query["bool"]["must"].append({"term": {"project_id": project_id}})
        mongo_query["project_id"] = project_id

    # We use ES aggregation or MongoDB distinct/aggregate
    if log_store.es_available:
        try:
            body = {
                "query": es_query,
                "size": 0,
                "aggs": {
                    "containers": {
                        "terms": {"field": "metadata.container_name.keyword" if "." not in "metadata.container_name" else "metadata.container_name", "size": 1000}
                    }
                }
            }
            res = await log_store.es.search(index="logs-all", body=body)
            buckets = res['aggregations']['containers']['buckets']
            return {"containers": [b['key'] for b in buckets]}
        except Exception as e:
            logger.error(f"Failed to fetch containers from ES: {e}")
            
    # MongoDB Fallback
    containers = await db.logs.distinct("metadata.container_name", mongo_query)
    return {"containers": sorted([c for c in containers if c])}


@router.post("/gelf", 
             response_model=LogIngestResponse,
             summary="Ingest GELF via HTTP",
             description="Accepts GELF (Graylog Extended Log Format) payloads over HTTP.")
async def ingest_gelf_http(request: Request, background_tasks: BackgroundTasks, project=Depends(verify_api_key)):
    """GELF HTTP endpoint — accepts standard GELF JSON (short_message, full_message, etc)."""
    try:
        body = await request.body()
        payload_str = body.decode('utf-8')
        parsed_log = parse_gelf_payload(payload_str)
        
        if not parsed_log:
            raise HTTPException(status_code=400, detail="Invalid GELF payload")
        
        # If parse_gelf_payload returned an error dict instead of full log
        if "error" in parsed_log:
            raise HTTPException(status_code=400, detail=parsed_log["error"])

        log_id = str(uuid.uuid4())
        now = datetime.now(dt_timezone.utc)
        grouped_hash = generate_log_hash(parsed_log['level'], parsed_log['message'], parsed_log['stack_trace'])

        device_info = parsed_log.get('device_info', {})
        if device_info.get("ip") == "unknown":
            device_info["ip"] = request.client.host

        log_doc = {
            "id": log_id,
            "level": parsed_log['level'],
            "message": parsed_log['message'],
            "channel": parsed_log['channel'],
            "environment": parsed_log['environment'] or project.get('environment', 'production'),
            "project_id": project['id'],
            "project_name": project['name'],
            "company_id": project.get('company_id'),
            "metadata": parsed_log['metadata'],
            "stack_trace": parsed_log['stack_trace'],
            "user_info": None,
            "device_info": device_info,
            "tags": parsed_log['tags'],
            "timestamp": now,
            "grouped_hash": grouped_hash,
            "ingest_protocol": "gelf-http",
            "gelf_version": parsed_log.get("gelf_version"),
            "source_host": parsed_log.get("source_host"),
            "syslog_level": parsed_log.get("syslog_level")
        }

        email_sent = await _check_alerts(log_doc, background_tasks)
        log_doc["alert_email_sent"] = email_sent
        await ingestion_queue.put(log_doc)
        return {"id": log_id, "status": "queued", "emailSent": email_sent}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        logger.error(f"GELF HTTP ingestion failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/replay", 
             summary="Ingest Session Replay",
             description="Associate a session replay (RRWeb events) with an existing log entry.")
async def ingest_replay(req: ReplayIngest, project=Depends(verify_api_key)):
    # Check if the log exists and belongs to the project
    log = await log_store.get_log(req.log_id)
    if not log or log.get('project_id') != project['id']:
        raise HTTPException(status_code=404, detail="Log not found for this project")

    replay_doc = {
        "log_id": req.log_id,
        "project_id": project['id'],
        "events": req.events,
        "timestamp": datetime.now(dt_timezone.utc).isoformat()
    }
    
    await db.replays.update_one(
        {"log_id": req.log_id},
        {"$set": replay_doc},
        upsert=True
    )
    
    # Also update the log document to indicate it has a replay
    # This helps the frontend know when to show the replay button
    if log_store.es_available:
        try:
             # Find which index the log is in
            search_res = await log_store.es.search(index="logs-all", body={
                "query": {"term": {"id": req.log_id}},
                "_source": False,
                "size": 1
            })
            if search_res['hits']['hits']:
                target_index = search_res['hits']['hits'][0]['_index']
                await log_store.es.update(index=target_index, id=req.log_id, body={
                    "doc": {"has_replay": True}
                })
        except Exception as e:
            logger.error(f"Failed to update ES log with replay flag: {e}")
            
    await db.logs.update_one({"id": req.log_id, "project_id": project['id']}, {"$set": {"has_replay": True}})
    
    return {"status": "success", "log_id": req.log_id}


@router.get("", 
            response_model=LogListResponse,
            summary="Search Logs",
            description="Search and filter logs with pagination. Supports full-text search and faceted filtering.")
async def list_logs(
    project_id: str = Query(None, description="Filter by project ID"),
    level: str = Query(None, description="Filter by log level (info, error, etc)"),
    channel: str = Query(None, description="Filter by channel name"),
    environment: str = Query(None, description="Filter by environment (production, staging, etc)"),
    search: str = Query(None, description="Full-text search query"),
    date_from: str = Query(None, description="ISO timestamp start range"),
    date_to: str = Query(None, description="ISO timestamp end range"),
    tags: str = Query(None, description="Comma-separated list of tags"),
    grouped_hash: str = Query(None, description="Filter by error group hash"),
    page: int = Query(1, ge=1),
    size: int = Query(99, ge=1, le=200),
    user=Depends(get_current_user)
):
    company_id = user.get("company_id")
    filters = {}
    if project_id:
        p_query = {"id": project_id, "company_id": company_id}
        if user.get('role') != 'admin':
            p_query["$or"] = [
                {"user_id": user['id']},
                {"id": {"$in": user.get('allowed_projects', [])}}
            ]
        project = await db.projects.find_one(p_query)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        filters['project_id'] = project_id
    else:
        p_query = {"company_id": company_id}
        if user.get('role') != 'admin':
            p_query["$or"] = [
                {"user_id": user['id']},
                {"id": {"$in": user.get('allowed_projects', [])}}
            ]
        projects = await db.projects.find(p_query, {"_id": 0, "id": 1}).to_list(1000)
        project_ids = [p['id'] for p in projects]
        if not project_ids:
            return {"logs": [], "total": 0, "page": page, "size": size}
        filters['project_ids'] = project_ids

    if level:
        filters['level'] = level
    if channel:
        filters['channel'] = channel
    if environment:
        filters['environment'] = environment
    if date_from:
        filters['date_from'] = date_from
    if date_to:
        filters['date_to'] = date_to
    if tags:
        filters['tags'] = tags.split(",")
    if grouped_hash:
        filters['grouped_hash'] = grouped_hash
    filters['company_id'] = company_id

    return await log_store.search_logs(filters, page, size, search)


@router.get("/groups", 
            response_model=LogGroupListResponse,
            summary="List Log Groups",
            description="Retrieve logs grouped by their unique signature (hash). Useful for identifying recurring events and reducing noise.")
async def list_groups(
    project_id: str = Query(None, description="Filter by project ID"),
    level: str = Query(None),
    channel: str = Query(None),
    environment: str = Query(None),
    search: str = Query(None),
    tags: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user=Depends(get_current_user)
):
    company_id = user.get("company_id")
    filters = {}
    if project_id:
        p_query = {"id": project_id, "company_id": company_id}
        if user.get('role') != 'admin':
            p_query["$or"] = [
                {"user_id": user['id']},
                {"id": {"$in": user.get('allowed_projects', [])}}
            ]
        project = await db.projects.find_one(p_query)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        filters['project_id'] = project_id
    else:
        p_query = {"company_id": company_id}
        if user.get('role') != 'admin':
            p_query["$or"] = [
                {"user_id": user['id']},
                {"id": {"$in": user.get('allowed_projects', [])}}
            ]
        projects = await db.projects.find(p_query, {"_id": 0, "id": 1}).to_list(1000)
        project_ids = [p['id'] for p in projects]
        if not project_ids:
            return {"groups": [], "total": 0, "page": page, "size": size}
        filters['project_ids'] = project_ids

    if level:
        filters['level'] = level
    if channel:
        filters['channel'] = channel
    if environment:
        filters['environment'] = environment
    if tags:
        filters['tags'] = tags.split(",")
    if date_from:
        filters['date_from'] = date_from
    if date_to:
        filters['date_to'] = date_to
    filters['company_id'] = company_id

    return await log_store.search_groups(filters, page, size, search)


@router.get("/stats", 
            summary="Get Log Statistics",
            description="Retrieve aggregated statistics for the dashboard, including counts by level, project, and timeline.")
async def get_log_stats(project_id: str = Query(None), user=Depends(get_current_user)):
    company_id = user.get("company_id")
    p_query = {"company_id": company_id}
    if project_id:
        p_query["id"] = project_id

    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]

    if project_id:
        project = await db.projects.find_one(p_query)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

    # Need to pass list of projects to filter stats globally if no project_id
    project_ids = None
    if not project_id:
        projects = await db.projects.find(p_query, {"_id": 0, "id": 1}).to_list(1000)
        project_ids = [p['id'] for p in projects]
        if not project_ids:
            return {"total": 0, "by_level": {}, "by_project": {}, "timeline": []}
            
    return await log_store.get_stats(project_id=project_id, allowed_project_ids=project_ids, company_id=company_id)


@router.get("/{log_id}", 
            response_model=LogResponse,
            summary="Get Log Detail",
            description="Retrieve full details for a single log entry.")
async def get_log_detail(log_id: str, user=Depends(get_current_user)):
    log = await log_store.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
        
    project_id = log.get('project_id')
    
    # Global Docker Logs (no project_id or legacy ID) are visible to admins or users with view_docker_logs permission
    if not project_id or project_id == "docker-global":
        if user.get('role') != 'admin' and 'view_docker_logs' not in user.get('permissions', []):
             raise HTTPException(status_code=403, detail="Not authorized to view global logs")
        return log

    # Standard project-based access check, always scoped by company
    company_id = user.get("company_id")
    p_query = {"id": project_id, "company_id": company_id}
    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
    project = await db.projects.find_one(p_query)

    if not project:
        raise HTTPException(status_code=403, detail="Not authorized")
    return log


@router.get("/replay/{log_id}", 
            summary="Get Session Replay",
            description="Retrieve the associated RRWeb session replay events for a log entry.")
async def get_log_replay(log_id: str, user=Depends(get_current_user)):
    # Verify user has access to the log
    log = await log_store.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
        
    company_id = user.get("company_id")
    p_query = {"id": log['project_id'], "company_id": company_id}
    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
    project = await db.projects.find_one(p_query)

    if not project:
        raise HTTPException(status_code=403, detail="Not authorized")

    replay = await db.replays.find_one({"log_id": log_id, "project_id": project['id']}, {"_id": 0})
    if not replay:
        raise HTTPException(status_code=404, detail="Replay not found")
        
    return replay


@router.delete("/{log_id}", 
               summary="Delete Log",
               description="Permanently delete a log entry from the store.")
async def delete_log(log_id: str, user=Depends(get_current_user)):
    log = await log_store.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
        
    company_id = user.get("company_id")
    p_query = {"id": log['project_id'], "company_id": company_id}
    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
    project = await db.projects.find_one(p_query)

    if not project:
        raise HTTPException(status_code=403, detail="Not authorized")
    await log_store.delete_log(log_id)
    return {"message": "Log deleted"}


async def _check_alerts(log_doc, background_tasks: BackgroundTasks = None):
    try:
        company_id = log_doc.get("company_id")
        rules_query = {"enabled": True}
        smtp_query = {"type": "smtp", "enabled": True}
        if company_id:
            rules_query["company_id"] = company_id
            smtp_query["company_id"] = company_id
        rules = await db.alert_rules.find(rules_query, {"_id": 0}).to_list(100)
        smtp_config = await db.settings.find_one(smtp_query, {"_id": 0})
        return _check_alerts_batch_sync(log_doc, rules, smtp_config, background_tasks)
    except Exception as e:
        logger.error(f"Alert check failed: {e}")
        return False


def _check_alerts_batch_sync(log_doc, rules, smtp_config, background_tasks: BackgroundTasks = None):
    """Sync version of alert check to be used within loops after pre-fetching rules."""
    email_sent = False
    if not smtp_config or not rules:
        return False
        
    for rule in rules:
        if rule.get('level') and rule['level'] != log_doc['level']:
            continue
        if rule.get('project_id') and rule['project_id'] != log_doc['project_id']:
            continue
        if rule.get('channel') and rule['channel'] != log_doc['channel']:
            continue
        
        # Advanced Filtering
        # 1. Container Name
        if rule.get('container_name'):
            log_container = log_doc.get('metadata', {}).get('container_name')
            if rule['container_name'] != log_container:
                continue
        
        # 2. Message Pattern (Include)
        if rule.get('message_pattern'):
            try:
                import re
                if not re.search(rule['message_pattern'], log_doc.get('message', ''), re.IGNORECASE):
                    continue
            except Exception:
                # Fallback to simple substring if regex fails
                if rule['message_pattern'].lower() not in log_doc.get('message', '').lower():
                    continue

        # 3. Exclude Pattern
        if rule.get('exclude_pattern'):
            try:
                import re
                if re.search(rule['exclude_pattern'], log_doc.get('message', ''), re.IGNORECASE):
                    continue
            except Exception:
                # Fallback to simple substring if regex fails
                if rule['exclude_pattern'].lower() in log_doc.get('message', '').lower():
                    continue

        # 4. Metadata Match
        if rule.get('metadata_key'):
            actual_val = log_doc.get('metadata', {}).get(rule['metadata_key'])
            expected_val = rule.get('metadata_value', '')
            if str(actual_val) != str(expected_val):
                continue
        
        emails = rule.get('emails', [])
        if emails:
            # Reliably schedule the email without blocking ingestion
            if background_tasks:
                background_tasks.add_task(_send_alert_email, log_doc, rule, emails, smtp_config)
            else:
                asyncio.create_task(_send_alert_email(log_doc, rule, emails, smtp_config))
            email_sent = True
    return email_sent


async def _send_alert_email(log_doc, rule, emails, smtp_config):
    try:
        import aiosmtplib
        from email.message import EmailMessage
        from utils.email_utils import render_template, EMAIL_TRANSLATIONS, get_app_settings
        import datetime
        
        # Get company-specific app settings for dynamic app name
        app_settings = await get_app_settings(log_doc.get("company_id"))
        app_name = app_settings.get('app_name', 'LogForge')

        # SMTP specific language for emails
        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS.get(lang, EMAIL_TRANSLATIONS["en"])["alert"]
        
        level = log_doc['level'].upper()
        level_colors = {
            'INFO': '#3b82f6', 'WARNING': '#f59e0b', 'ERROR': '#ef4444', 
            'CRITICAL': '#7f1d1d', 'DEBUG': '#6b7280'
        }
        badge_color = level_colors.get(level, '#10b981')

        subject = f"[{app_name} {'Alerte' if lang == 'fr' else 'Alert'}] {level}: {log_doc.get('message', '')[:60]}..."
        import os
        from database import db as main_db
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        cta_link = f"{frontend_url}/explore?query=id:\"{log_doc['id']}\""
        
        context = {
            "log": log_doc,
            "rule_name": rule.get('name', 'N/A'),
            "badge_color": badge_color,
            "cta_link": cta_link,
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans}
        }

        html_body = await render_template("alert_notification.html", context, lang)
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_config.get('from_email', '')
        msg['To'] = ", ".join(emails)
        msg.set_content(f"Alert: {rule.get('name')}\nLevel: {level}\nMessage: {log_doc.get('message')}")
        msg.add_alternative(html_body, subtype='html')

        await aiosmtplib.send(msg,
            hostname=smtp_config['host'], port=smtp_config['port'],
            username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
            use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}")
