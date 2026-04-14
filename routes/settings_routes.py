from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional, List
import uuid
import base64
import secrets
import hashlib
from datetime import datetime, timezone

from database import db
from auth import get_current_user, get_admin_user, require_permission

router = APIRouter()

ALLOWED_PERMISSIONS = ["manage_smtp", "manage_alert_rules", "delete_projects"]


class AgentKeyResponse(BaseModel):
    id: str
    description: str
    key_prefix: str  # Only show the prefix for existing keys
    created_at: str
    expires_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    status: str


class AgentKeyCreate(BaseModel):
    description: str = "Docker Agent"
    expires_at: Optional[str] = None


class SMTPConfigUpdate(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    language: str = "en"
    enabled: bool = False


class AlertRuleCreate(BaseModel):
    name: str
    level: str
    project_id: Optional[str] = None
    channel: Optional[str] = None
    emails: List[str] = []
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    level: Optional[str] = None
    project_id: Optional[str] = None
    channel: Optional[str] = None
    emails: Optional[List[str]] = None
    enabled: Optional[bool] = None


class ThemeUpdate(BaseModel):
    theme: str


class AppSettingsUpdate(BaseModel):
    app_name: Optional[str] = None
    primary_color: Optional[str] = None
    logo_url: Optional[str] = None
    language: Optional[str] = None
    notify_role_change: Optional[bool] = True
    notify_project_access: Optional[bool] = True
    notify_permission_change: Optional[bool] = True
    notify_status_change: Optional[bool] = True
    material_mode: Optional[bool] = False


class AppSettingsResponse(BaseModel):
    app_name: str = "LogForge"
    primary_color: str = "#10b981"
    logo_url: Optional[str] = None
    language: str
    notify_role_change: bool = True
    notify_project_access: bool = True
    notify_permission_change: bool = True
    notify_status_change: bool = True
    material_mode: bool = False


class SMTPConfigResponse(BaseModel):
    host: str
    port: int
    username: str
    from_email: str
    language: str
    enabled: bool


class SettingsResponse(BaseModel):
    theme: str
    smtp_config: SMTPConfigResponse


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    level: str
    project_id: Optional[str] = None
    channel: Optional[str] = None
    emails: List[str]
    enabled: bool
    user_id: str


class AlertRuleListResponse(BaseModel):
    rules: List[AlertRuleResponse]
    total: int
    page: int
    size: int
    pages: int


@router.get("/", 
            response_model=SettingsResponse,
            summary="Get Global Settings",
            description="Retrieve the current user theme and the global SMTP configuration.")
async def get_settings(user=Depends(get_current_user)):
    # SMTP is now global for the instance
    smtp = await db.settings.find_one({"type": "smtp"}, {"_id": 0})
    if not smtp:
        smtp = {"type": "smtp", "host": "", "port": 587, "username": "", "password": "", "from_email": "", "language": "en", "enabled": False}
    return {"theme": user.get('theme', 'dark'), "smtp_config": smtp}


@router.put("/theme", 
            summary="Update User Theme",
            description="Update the theme preference (light/dark) for the currently authenticated user.")
async def update_theme(req: ThemeUpdate, user=Depends(get_current_user)):
    print(f"DEBUG: Updating theme for user {user['id']} to {req.theme}")
    await db.users.update_one({"id": user['id']}, {"$set": {"theme": req.theme}})
    return {"theme": req.theme}


# --- Global App Settings (admin only) ---

@router.get("/app", 
            response_model=AppSettingsResponse,
            summary="Get App Branding",
            description="Retrieve global application branding settings like name, primary color, and logo.")
async def get_app_settings():
    """Get global app settings. Accessible to all authenticated users (to load logo/color)."""
    settings = await db.settings.find_one({"type": "app_settings"}, {"_id": 0})
    if not settings:
        settings = {
            "type": "app_settings",
            "app_name": "LogForge",
            "primary_color": "#10b981",
            "logo_url": None,
            "language": "en",
            "notify_role_change": True,
            "notify_project_access": True,
            "notify_permission_change": True,
            "notify_status_change": True,
            "material_mode": False
        }
    return settings


@router.put("/app", 
            response_model=AppSettingsResponse,
            summary="Update App Branding",
            description="Update global application branding. Administrator access required.")
async def update_app_settings(req: AppSettingsUpdate, user=Depends(get_admin_user)):
    """Update global app settings. Admin only."""
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    update_data['type'] = 'app_settings'
    await db.settings.update_one(
        {"type": "app_settings"},
        {"$set": update_data},
        upsert=True
    )
    settings = await db.settings.find_one({"type": "app_settings"}, {"_id": 0})
    return settings


@router.post("/app/logo", 
             summary="Upload App Logo",
             description="Upload a new application logo (stored as base64). Max size 2MB. Administrator access required.")
async def upload_app_logo(file: UploadFile = File(...), user=Depends(get_admin_user)):
    """Upload app logo as base64. Admin only."""
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:  # 2MB limit
        raise HTTPException(status_code=400, detail="Logo must be less than 2MB")
    
    mime_type = file.content_type or "image/png"
    if mime_type not in ["image/png", "image/jpeg", "image/svg+xml", "image/gif", "image/webp"]:
        raise HTTPException(status_code=400, detail="Invalid image type. Accepted: PNG, JPEG, SVG, GIF, WebP")
    
    b64 = base64.b64encode(content).decode()
    logo_url = f"data:{mime_type};base64,{b64}"
    
    await db.settings.update_one(
        {"type": "app_settings"},
        {"$set": {"type": "app_settings", "logo_url": logo_url}},
        upsert=True
    )
    return {"logo_url": logo_url}


# --- SMTP (permission: manage_smtp OR admin) ---

@router.put("/smtp", 
            response_model=SMTPConfigResponse,
            summary="Update SMTP Config",
            description="Update the global SMTP server configuration for alert emails. Requires 'manage_smtp' permission.")
async def update_smtp(req: SMTPConfigUpdate, user=Depends(require_permission("manage_smtp"))):
    doc = req.model_dump()
    doc['type'] = 'smtp'
    # SMTP settings are global, no user_id scope
    await db.settings.update_one(
        {"type": "smtp"},
        {"$set": doc}, upsert=True
    )
    return doc


@router.get("/alert-rules", 
            response_model=AlertRuleListResponse,
            summary="List Alert Rules",
            description="Retrieve a paginated list of configured alert rules.")
async def list_alert_rules(page: int = Query(1, ge=1), size: int = Query(99, ge=1, le=100), user=Depends(get_current_user)):
    query = {}
    if user.get('role') != 'admin':
        query['user_id'] = user['id']
        
    total = await db.alert_rules.count_documents(query)
    skip = (page - 1) * size
    rules = await db.alert_rules.find(query, {"_id": 0}).skip(skip).limit(size).to_list(length=size)
    import math
    return {
        "rules": rules,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if total > 0 else 1
    }


@router.post("/alert-rules", 
             response_model=AlertRuleResponse,
             summary="Create Alert Rule",
             description="Create a new alert rule to trigger emails on specific log levels or projects. Requires 'manage_alert_rules' permission.")
async def create_alert_rule(req: AlertRuleCreate, user=Depends(require_permission("manage_alert_rules"))):
    rule_id = str(uuid.uuid4())
    doc = req.model_dump()
    doc['id'] = rule_id
    doc['user_id'] = user['id']
    await db.alert_rules.insert_one(doc)
    doc.pop('_id', None)
    return doc


@router.put("/alert-rules/{rule_id}", 
            response_model=AlertRuleResponse,
            summary="Update Alert Rule",
            description="Modify an existing alert rule. Requires 'manage_alert_rules' permission.")
async def update_alert_rule(rule_id: str, req: AlertRuleUpdate, user=Depends(require_permission("manage_alert_rules"))):
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    query = {"id": rule_id}
    if user.get('role') != 'admin':
        query["user_id"] = user['id']
        
    result = await db.alert_rules.update_one(query, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert rule not found or you don't have permission")
    rule = await db.alert_rules.find_one({"id": rule_id}, {"_id": 0})
    return rule


@router.delete("/alert-rules/{rule_id}", 
               summary="Delete Alert Rule",
               description="Permanently remove an alert rule. Requires 'manage_alert_rules' permission.")
async def delete_alert_rule(rule_id: str, user=Depends(require_permission("manage_alert_rules"))):
    query = {"id": rule_id}
    if user.get('role') != 'admin':
        query["user_id"] = user['id']
        
    result = await db.alert_rules.delete_one(query)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Alert rule not found or you don't have permission")
    return {"message": "Alert rule deleted"}


@router.post("/smtp/test", 
             summary="Test SMTP Config",
             description="Send a test email using the provided SMTP configuration and save it if successful. Requires 'manage_smtp' permission.")
async def test_smtp(req: SMTPConfigUpdate, user=Depends(require_permission("manage_smtp"))):
    if not req.host or not req.from_email:
        raise HTTPException(status_code=400, detail="Missing host or from_email in configuration")
    try:
        import aiosmtplib
        from email.message import EmailMessage
        from utils.email_utils import render_template, EMAIL_TRANSLATIONS
        
        # Test Email Template
        msg = EmailMessage()
        lang = req.language
        trans = EMAIL_TRANSLATIONS.get(lang, EMAIL_TRANSLATIONS["en"])["smtp_test"]
        
        subject = trans["subject"]
        
        context = {
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans}
        }
        
        html_body = await render_template("smtp_test.html", context, lang)
        msg['Subject'] = subject
        msg['From'] = req.from_email
        msg['To'] = user['email']
        msg.set_content(trans["body_text"])
        msg.add_alternative(html_body, subtype='html')
        
        await aiosmtplib.send(msg,
            hostname=req.host, port=req.port,
            username=req.username, password=req.password,
            use_tls=req.port == 465, start_tls=req.port == 587)
            
        # Automatically save config on successful test (globally)
        doc = req.model_dump()
        doc['type'] = 'smtp'
        await db.settings.update_one(
            {"type": "smtp"},
            {"$set": doc}, upsert=True
        )
            
        return {"message": "Test email sent successfully and configuration saved"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SMTP test failed: {str(e)}")


# --- Docker Agent Keys (admin only) ---

@router.get("/agent-keys",
            response_model=List[AgentKeyResponse],
            summary="List Agent Keys",
            description="Retrieve a list of all global Docker Agent API keys. Administrator access required.")
async def list_agent_keys(user=Depends(get_admin_user)):
    keys = await db.agent_keys.find({}, {"_id": 0, "key_hash": 0}).to_list(100)
    return keys


@router.post("/agent-keys",
             summary="Generate Agent Key",
             description="Create a new global Docker Agent API key. The key is shown only once and stored as a hash. Administrator access required.")
async def generate_agent_key(req: AgentKeyCreate, user=Depends(get_admin_user)):
    key_id = str(uuid.uuid4())
    raw_key = f"lfa_{secrets.token_hex(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": key_id,
        "description": req.description,
        "key_hash": key_hash,
        "key_prefix": raw_key[:10] + "...",
        "created_at": now,
        "expires_at": req.expires_at,
        "last_seen_at": None,
        "status": "active"
    }
    await db.agent_keys.insert_one(doc)
    
    return {
        "id": key_id,
        "raw_key": raw_key,
        "description": req.description,
        "created_at": now,
        "expires_at": req.expires_at
    }


@router.delete("/agent-keys/{key_id}",
               summary="Revoke Agent Key",
               description="Permanently revoke a global Docker Agent API key. Administrator access required.")
async def revoke_agent_key(key_id: str, user=Depends(get_admin_user)):
    result = await db.agent_keys.delete_one({"id": key_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Agent key not found")
    return {"message": "Agent key revoked"}
