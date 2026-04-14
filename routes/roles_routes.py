from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List

from database import db
from auth import get_admin_user
from utils.email_utils import (
    send_project_access_email, 
    send_permission_change_email,
    send_role_change_email,
    send_status_change_email,
    send_password_reset_email
)
import math

router = APIRouter()

ALLOWED_PERMISSIONS = ["manage_smtp", "manage_alert_rules", "delete_projects", "view_docker_logs"]


class UserRoleUpdate(BaseModel):
    role: Optional[str] = None  # "admin" or "member"
    permissions: Optional[List[str]] = None
    allowed_projects: Optional[List[str]] = None
    is_active: Optional[bool] = None


class UserRoleResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    permissions: List[str]
    allowed_projects: List[str]
    is_active: bool = True
    created_at: Optional[str] = None
    auth_provider: Optional[str] = None


class UserListResponse(BaseModel):
    users: List[UserRoleResponse]
    total: int
    page: int
    size: int
    pages: int


@router.get("/users",
            response_model=UserListResponse,
            summary="List Users and Roles",
            description="Retrieve a paginated list of all users in the current company. Administrator access required.")
async def list_users(page: int = Query(1, ge=1), size: int = Query(99, ge=1, le=200), admin=Depends(get_admin_user)):
    """List all users in the admin's company. Admin only."""
    company_id = admin.get("company_id")
    query = {"company_id": company_id}
    total = await db.users.count_documents(query)
    skip = (page - 1) * size
    users = await db.users.find(query, {"_id": 0, "password": 0}).skip(skip).limit(size).to_list(length=size)
    # Normalize role/permissions for users that predate RBAC
    result = []
    for u in users:
        result.append({
            "id": u.get("id"),
            "name": u.get("name"),
            "email": u.get("email"),
            "role": u.get("role", "member"),
            "permissions": u.get("permissions", []),
            "allowed_projects": u.get("allowed_projects", []),
            "is_active": u.get("is_active", True),
            "created_at": u.get("created_at"),
            "auth_provider": u.get("auth_provider", "email"),
        })
    return {
        "users": result,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if total > 0 else 1
    }


@router.patch("/users/{user_id}", 
              response_model=UserRoleResponse,
              summary="Update User Role/Permissions",
              description="Update the role, permissions, or project access for a specific user. Sends notification emails to the user. Administrator access required.")
async def update_user_role(user_id: str, req: UserRoleUpdate, background_tasks: BackgroundTasks, admin=Depends(get_admin_user)):
    """Update a user's role and/or permissions. Admin only, company-scoped."""
    company_id = admin.get("company_id")
    target = await db.users.find_one({"id": user_id, "company_id": company_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = {}

    if req.role is not None:
        if req.role not in ["admin", "member"]:
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")
        if req.role != target.get("role"):
            background_tasks.add_task(
                send_role_change_email,
                target['email'],
                target.get('name', target['email']),
                req.role,
                performer_name=admin.get('name'),
                company_id=company_id
            )
        update_data["role"] = req.role

    if req.permissions is not None:
        invalid = [p for p in req.permissions if p not in ALLOWED_PERMISSIONS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid permissions: {invalid}")
        
        old_perms = set(target.get("permissions", []))
        new_perms_list = req.permissions
        new_perms_set = set(new_perms_list)
        
        added_perms = new_perms_set - old_perms
        removed_perms = old_perms - new_perms_set
        
        for perm_key in added_perms:
            background_tasks.add_task(
                send_permission_change_email,
                target['email'],
                target.get('name', target['email']),
                perm_key,
                "granted",
                performer_name=admin.get('name'),
                company_id=company_id
            )

        for perm_key in removed_perms:
            background_tasks.add_task(
                send_permission_change_email,
                target['email'],
                target.get('name', target['email']),
                perm_key,
                "revoked",
                performer_name=admin.get('name'),
                company_id=company_id
            )

        update_data["permissions"] = new_perms_list

    if req.allowed_projects is not None:
        old_projects = set(target.get("allowed_projects", []))
        new_projects_list = req.allowed_projects
        new_projects_set = set(new_projects_list)
        
        added = new_projects_set - old_projects
        removed = old_projects - new_projects_set
        
        # We'll send emails for each added/removed project
        for proj_id in added:
            project = await db.projects.find_one({"id": proj_id, "company_id": company_id})
            if project:
                background_tasks.add_task(
                    send_project_access_email,
                    target['email'],
                    target.get('name', target['email']),
                    project['name'],
                    "granted",
                    performer_name=admin.get('name'),
                    company_id=company_id
                )

        for proj_id in removed:
            project = await db.projects.find_one({"id": proj_id, "company_id": company_id})
            if project:
                background_tasks.add_task(
                    send_project_access_email,
                    target['email'],
                    target.get('name', target['email']),
                    project['name'],
                    "revoked",
                    performer_name=admin.get('name'),
                    company_id=company_id
                )

        update_data["allowed_projects"] = new_projects_list

    if req.is_active is not None:
        if req.is_active != target.get("is_active", True):
            background_tasks.add_task(
                send_status_change_email,
                target['email'],
                target.get('name', target['email']),
                req.is_active,
                performer_name=admin.get('name'),
                company_id=company_id
            )
        update_data["is_active"] = req.is_active

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    await db.users.update_one({"id": user_id, "company_id": company_id}, {"$set": update_data})

    updated = await db.users.find_one({"id": user_id, "company_id": company_id}, {"_id": 0, "password": 0})
    return {
        "id": updated.get("id"),
        "name": updated.get("name"),
        "email": updated.get("email"),
        "role": updated.get("role", "member"),
        "permissions": updated.get("permissions", []),
        "allowed_projects": updated.get("allowed_projects", []),
        "is_active": updated.get("is_active", True),
    }


@router.post("/users/{user_id}/reset-password",
             summary="Trigger Password Reset Email",
             description="Allows an administrator to manually trigger a password reset email for a user. Administrator access required.")
async def admin_trigger_reset(user_id: str, admin=Depends(get_admin_user)):
    """Trigger a password reset email for a user. Admin only, company-scoped."""
    company_id = admin.get("company_id")
    user = await db.users.find_one({"id": user_id, "company_id": company_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if SMTP is configured
    from utils.email_utils import get_smtp_config
    smtp = await get_smtp_config(company_id)
    if not smtp or not smtp.get('enabled', False):
        raise HTTPException(
            status_code=503, 
            detail="SMTP is not configured or enabled. Please configure SMTP in Settings > Platform Settings to send recovery emails."
        )

    # Generate token
    import secrets
    from datetime import datetime, timezone, timedelta
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "reset_token": token,
            "reset_token_expiry": expiry.isoformat()
        }}
    )
    
    # Send email
    success = await send_password_reset_email(user["email"], user["name"], token, company_id=user.get("company_id"))
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send reset email. Please check SMTP logs.")
        
    return {"message": f"Password reset email sent to {user['email']}"}


