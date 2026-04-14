from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional
import uuid

from database import db
from auth import get_current_user
import math

router = APIRouter()


class ChannelCreate(BaseModel):
    name: str
    description: str = ""
    project_id: str


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ChannelResponse(BaseModel):
    id: str
    name: str
    description: str
    project_id: str
    created_at: str


class ChannelListResponse(BaseModel):
    channels: list[ChannelResponse]
    total: int
    page: int
    size: int
    pages: int


@router.get("/", 
            response_model=ChannelListResponse,
            summary="List Channels",
            description="Retrieve a paginated list of channels. Optionally filtered by project ID.")
async def list_channels(project_id: str = Query(None), page: int = Query(1, ge=1), size: int = Query(99, ge=1, le=100), user=Depends(get_current_user)):
    company_id = user.get("company_id")
    query = {}

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
        query['project_id'] = project_id
    else:
        p_query = {"company_id": company_id}
        if user.get('role') != 'admin':
            p_query["$or"] = [
                {"user_id": user['id']},
                {"id": {"$in": user.get('allowed_projects', [])}}
            ]
        projects = await db.projects.find(p_query, {"_id": 0, "id": 1}).to_list(1000)
        project_ids = [p['id'] for p in projects]
        query['project_id'] = {"$in": project_ids}

    # Defense in depth: always scope channels by company_id
    query['company_id'] = company_id

    total = await db.channels.count_documents(query)
    skip = (page - 1) * size

    channels = await db.channels.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(size).to_list(length=size)
    return {
        "channels": channels,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if total > 0 else 1
    }


@router.post("/", 
             response_model=ChannelResponse,
             summary="Create Channel",
             description="Create a new channel within a specific project.")
async def create_channel(req: ChannelCreate, user=Depends(get_current_user)):
    company_id = user.get("company_id")
    p_query = {"id": req.project_id, "company_id": company_id}
    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
    project = await db.projects.find_one(p_query)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    channel_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": channel_id, "name": req.name,
        "description": req.description,
        "project_id": req.project_id,
        "company_id": company_id,
        "created_at": now
    }
    await db.channels.insert_one(doc)
    doc.pop('_id', None)
    return doc


@router.put("/{channel_id}", 
            response_model=ChannelResponse,
            summary="Update Channel",
            description="Modify the details of an existing channel.")
async def update_channel(channel_id: str, req: ChannelUpdate, user=Depends(get_current_user)):
    company_id = user.get("company_id")
    channel = await db.channels.find_one({"id": channel_id, "company_id": company_id}, {"_id": 0})
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    p_query = {"id": channel['project_id'], "company_id": company_id}
    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
    project = await db.projects.find_one(p_query)
    if not project:
        raise HTTPException(status_code=403, detail="Not authorized")
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if update_data:
        await db.channels.update_one({"id": channel_id, "company_id": company_id}, {"$set": update_data})
    updated = await db.channels.find_one({"id": channel_id, "company_id": company_id}, {"_id": 0})
    return updated


@router.delete("/{channel_id}",
               summary="Delete Channel",
               description="Permanently remove a channel.")
async def delete_channel(channel_id: str, user=Depends(get_current_user)):
    company_id = user.get("company_id")
    channel = await db.channels.find_one({"id": channel_id, "company_id": company_id}, {"_id": 0})
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    p_query = {"id": channel['project_id'], "company_id": company_id}
    if user.get('role') != 'admin':
        p_query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
    project = await db.projects.find_one(p_query)
    if not project:
        raise HTTPException(status_code=403, detail="Not authorized")
    await db.channels.delete_one({"id": channel_id, "company_id": company_id})
    return {"message": "Channel deleted"}
