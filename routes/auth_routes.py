from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
import uuid
import os
import requests as http_requests

from database import db
from auth import hash_password, verify_password, create_token, get_current_user

router = APIRouter()


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: str
    theme: str = "dark"
    role: str = "member"
    permissions: list = []
    allowed_projects: list = []
    dashboard_config: dict = {}
    is_active: bool = True


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserPreferencesUpdate(BaseModel):
    dashboard_config: dict


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/signup", 
             response_model=AuthResponse, 
             summary="User Signup", 
             description="Register a new user with an email and password. Returns an access token and user profile.")
async def signup(req: SignupRequest):
    existing = await db.users.find_one({"email": req.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    user_doc = {
        "id": user_id, "email": req.email,
        "password": hash_password(req.password),
        "name": req.name, "created_at": now,
        "theme": "dark", "auth_provider": "email",
        "role": "member", "permissions": [],
        "allowed_projects": [],
        "dashboard_config": {},
        "is_active": True
    }
    await db.users.insert_one(user_doc)
    token = create_token(user_id)
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user_id, email=req.email, name=req.name, created_at=now, theme="dark", role="member", permissions=[], allowed_projects=[], dashboard_config={}, is_active=True)
    )


@router.post("/login", 
             response_model=AuthResponse,
             summary="User Login",
             description="Authenticate with email and password. Returns a JWT access token.")
async def login(req: LoginRequest):
    user = await db.users.find_one({"email": req.email}, {"_id": 0})
    if not user or not verify_password(req.password, user['password']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not user.get('is_active', True):
        raise HTTPException(status_code=403, detail="Account is deactivated. Please contact an administrator.")

    token = create_token(user['id'])
    return AuthResponse(
        access_token=token,
        user=UserResponse(
            id=user['id'], email=user['email'], name=user['name'],
            created_at=user['created_at'], theme=user.get('theme', 'dark'),
            role=user.get('role', 'member'), permissions=user.get('permissions', []),
            allowed_projects=user.get('allowed_projects', []),
            dashboard_config=user.get('dashboard_config', {}),
            is_active=user.get('is_active', True)
        )
    )


@router.get("/me", 
            response_model=UserResponse,
            summary="Get Current User",
            description="Retrieve the profile of the currently authenticated user.")
async def get_me(user=Depends(get_current_user)):
    return UserResponse(
        id=user['id'], email=user['email'], name=user['name'],
        created_at=user['created_at'], theme=user.get('theme', 'dark'),
        role=user.get('role', 'member'), permissions=user.get('permissions', []),
        allowed_projects=user.get('allowed_projects', []),
        dashboard_config=user.get('dashboard_config', {}),
        is_active=user.get('is_active', True)
    )


@router.patch("/me/preferences", 
              response_model=UserResponse,
              summary="Update User Preferences",
              description="Update the current user dashboard configuration and other preferences.")
async def update_preferences(req: UserPreferencesUpdate, user=Depends(get_current_user)):
    await db.users.update_one({"id": user["id"]}, {"$set": {"dashboard_config": req.dashboard_config}})
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password": 0})
    return UserResponse(
        id=updated['id'], email=updated['email'], name=updated['name'],
        created_at=updated['created_at'], theme=updated.get('theme', 'dark'),
        role=updated.get('role', 'member'), permissions=updated.get('permissions', []),
        allowed_projects=updated.get('allowed_projects', []),
        dashboard_config=updated.get('dashboard_config', {}),
        is_active=updated.get('is_active', True)
    )


@router.get("/github",
            summary="GitHub OAuth Login URL",
            description="Generates the GitHub authorize URL if configured.")
async def github_oauth():
    client_id = os.environ.get('GITHUB_CLIENT_ID', '')
    if not client_id:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in your .env file.")
    redirect_uri = os.environ.get('GITHUB_REDIRECT_URI', '')
    return {"redirect_url": f"https://github.com/login/oauth/authorize?client_id={client_id}&redirect_uri={redirect_uri}&scope=user:email"}


@router.get("/github/callback",
            response_model=AuthResponse,
            summary="GitHub OAuth Callback",
            description="Exchanges the GitHub code for a JWT token and user profile.")
async def github_callback(code: str):
    client_id = os.environ.get('GITHUB_CLIENT_ID', '')
    client_secret = os.environ.get('GITHUB_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")

    token_resp = http_requests.post("https://github.com/login/oauth/access_token",
        data={"client_id": client_id, "client_secret": client_secret, "code": code},
        headers={"Accept": "application/json"})
    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to get GitHub access token")

    user_resp = http_requests.get("https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}"})
    gh_user = user_resp.json()

    email_resp = http_requests.get("https://api.github.com/user/emails",
        headers={"Authorization": f"Bearer {access_token}"})
    emails = email_resp.json()
    primary_email = next((e['email'] for e in emails if e.get('primary')), gh_user.get('email', ''))

    user = await db.users.find_one({"email": primary_email}, {"_id": 0})
    if not user:
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id": user_id, "email": primary_email, "password": "",
            "name": gh_user.get('name', gh_user.get('login', '')),
            "created_at": now,
            "auth_provider": "github",
            "theme": "dark",
            "role": "member",
            "permissions": [],
            "allowed_projects": [],
            "dashboard_config": {},
            "is_active": True
        }
    await db.users.insert_one(user)

    token = create_token(user['id'])
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user['id'], email=user['email'], name=user['name'],
                          created_at=user['created_at'], theme=user.get('theme', 'dark'),
                          role=user.get('role', 'member'), permissions=user.get('permissions', []),
                          allowed_projects=user.get('allowed_projects', []),
                          dashboard_config=user.get('dashboard_config', {}))
    )


@router.get("/gitlab")
async def gitlab_oauth():
    client_id = os.environ.get('GITLAB_CLIENT_ID', '')
    if not client_id:
        raise HTTPException(status_code=501, detail="GitLab OAuth not configured. Set GITLAB_CLIENT_ID and GITLAB_CLIENT_SECRET in your .env file.")
    redirect_uri = os.environ.get('GITLAB_REDIRECT_URI', '')
    return {"redirect_url": f"https://gitlab.com/oauth/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope=read_user"}


@router.get("/gitlab/callback")
async def gitlab_callback(code: str):
    client_id = os.environ.get('GITLAB_CLIENT_ID', '')
    client_secret = os.environ.get('GITLAB_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        raise HTTPException(status_code=501, detail="GitLab OAuth not configured")

    redirect_uri = os.environ.get('GITLAB_REDIRECT_URI', '')
    token_resp = http_requests.post("https://gitlab.com/oauth/token",
        data={"client_id": client_id, "client_secret": client_secret,
              "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to get GitLab access token")

    user_resp = http_requests.get("https://gitlab.com/api/v4/user",
        headers={"Authorization": f"Bearer {access_token}"})
    gl_user = user_resp.json()
    email = gl_user.get('email', '')

    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user:
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id": user_id, "email": email, "password": "",
            "name": gl_user.get('name', gl_user.get('username', '')),
            "created_at": now, "theme": "dark",
            "auth_provider": "gitlab", "gitlab_id": str(gl_user.get('id', '')),
            "role": "member", "permissions": [],
            "allowed_projects": [],
            "dashboard_config": {},
            "is_active": True
        }
        await db.users.insert_one(user)

    token = create_token(user['id'])
    return AuthResponse(
        access_token=token,
        user=UserResponse(id=user['id'], email=user['email'], name=user['name'],
                          created_at=user['created_at'], theme=user.get('theme', 'dark'),
                          role=user.get('role', 'member'), permissions=user.get('permissions', []),
                          allowed_projects=user.get('allowed_projects', []),
                          dashboard_config=user.get('dashboard_config', {}))
    )


@router.post("/forgot-password",
             summary="Request Password Reset",
             description="Sends a password reset email if the user exists and SMTP is configured.")
async def forgot_password(req: ForgotPasswordRequest):
    user = await db.users.find_one({"email": req.email})
    if not user:
        # For security, don't reveal if user exists
        return {"message": "If your email is registered, you will receive a reset link shortly."}

    # Check if SMTP is configured
    from utils.email_utils import get_smtp_config, send_password_reset_email
    smtp = await get_smtp_config()
    if not smtp or not smtp.get('enabled', False):
        raise HTTPException(status_code=503, detail="Password recovery is not configured on this server. Please contact an administrator.")

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
    success = await send_password_reset_email(user["email"], user["name"], token)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send reset email. Please try again later.")
        
    return {"message": "If your email is registered, you will receive a reset link shortly."}


@router.post("/reset-password",
             summary="Reset Password",
             description="Updates the user password using a valid reset token.")
async def reset_password(req: ResetPasswordRequest):
    user = await db.users.find_one({"reset_token": req.token})
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    # Check expiry
    from datetime import datetime, timezone
    expiry_str = user.get("reset_token_expiry")
    if not expiry_str:
        raise HTTPException(status_code=400, detail="Invalid token")
        
    expiry = datetime.fromisoformat(expiry_str)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expiry:
        raise HTTPException(status_code=400, detail="Reset token has expired")
    
    # Update password and clear token
    from auth import hash_password
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {"password": hash_password(req.new_password)},
            "$unset": {"reset_token": "", "reset_token_expiry": ""}
        }
    )
    
    return {"message": "Password updated successfully"}
