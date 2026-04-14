from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional
import uuid
import secrets

from database import db
from auth import get_current_user, get_admin_user, require_permission
import math

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    environment: str = "production"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    environment: str
    api_key: str
    user_id: str
    created_at: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int
    page: int
    size: int
    pages: int


@router.get("/", 
            response_model=ProjectListResponse,
            summary="List Projects",
            description="Retrieve a paginated list of projects accessible by the current user.")
async def list_projects(page: int = Query(1, ge=1), size: int = Query(99, ge=1, le=100), user=Depends(get_current_user)):
    query = {}
    if user.get('role') != 'admin':
        # Members only see basic projects they explicitly own or are granted access to
        query = {"$or": [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]}
    
    total = await db.projects.count_documents(query)
    skip = (page - 1) * size

    projects = await db.projects.find(
        query, {"_id": 0}
    ).sort("created_at", -1).skip(skip).limit(size).to_list(length=size)
    
    return {
        "projects": projects,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if total > 0 else 1
    }


@router.post("/", 
             response_model=ProjectResponse,
             summary="Create Project",
             description="Create a new project. Requires administrator privileges.")
async def create_project(req: ProjectCreate, admin=Depends(get_admin_user)):
    project_id = str(uuid.uuid4())
    api_key = f"lv_{secrets.token_hex(24)}"
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "id": project_id,
        "name": req.name,
        "description": req.description,
        "environment": req.environment,
        "api_key": api_key,
        "user_id": admin['id'],
        "created_at": now
    }
    await db.projects.insert_one(project)
    project.pop('_id', None)
    return project


@router.get("/{project_id}", 
            response_model=ProjectResponse,
            summary="Get Project Details",
            description="Retrieve detailed information about a specific project by its unique ID.")
async def get_project(project_id: str, user=Depends(get_current_user)):
    query = {"id": project_id}
    if user.get('role') != 'admin':
        query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
        
    project = await db.projects.find_one(query, {"_id": 0})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}", 
            response_model=ProjectResponse,
            summary="Update Project",
            description="Modify the details of an existing project.")
async def update_project(project_id: str, req: ProjectUpdate, user=Depends(get_current_user)):
    query = {"id": project_id}
    if user.get('role') != 'admin':
        query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
        
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
        
    result = await db.projects.update_one(query, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return project


@router.delete("/{project_id}",
               summary="Delete Project",
               description="Permanently remove a project and all its associated channels. Requires 'delete_projects' permission.")
async def delete_project(project_id: str, user=Depends(require_permission("delete_projects"))):
    result = await db.projects.delete_one({"id": project_id, "user_id": user['id']})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.channels.delete_many({"project_id": project_id})
    return {"message": "Project deleted"}


@router.post("/{project_id}/regenerate-key",
             summary="Regenerate API Key",
             description="Revoke the current API key and generate a new one for the specified project.")
async def regenerate_api_key(project_id: str, user=Depends(get_current_user)):
    query = {"id": project_id}
    if user.get('role') != 'admin':
        query["$or"] = [
            {"user_id": user['id']},
            {"id": {"$in": user.get('allowed_projects', [])}}
        ]
        
    new_key = f"lv_{secrets.token_hex(24)}"
    result = await db.projects.update_one(
        query, {"$set": {"api_key": new_key}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"api_key": new_key}
