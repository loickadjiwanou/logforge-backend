import os
import jwt
import bcrypt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Header, Depends
from typing import List
from database import db

JWT_SECRET = os.environ.get('JWT_SECRET', 'logforge-secret-key-change-in-production')
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.get('is_active', True):
        raise HTTPException(status_code=403, detail="Account is deactivated")
    return user


def require_admin(user=Depends(lambda: None)):
    """Returns a dependency that ensures the current user is an admin."""
    async def _check(authorization: str = Header(None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authorization header")
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not user.get('is_active', True):
            raise HTTPException(status_code=403, detail="Account is deactivated")
        if user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Admin access required")
        return user
    return _check


def require_permission(permission: str):
    """Returns a dependency that ensures the user has the given permission (or is admin)."""
    async def _check(authorization: str = Header(None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authorization header")
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not user.get('is_active', True):
            raise HTTPException(status_code=403, detail="Account is deactivated")
        # Admins have all permissions
        if user.get('role') == 'admin':
            return user
        if permission not in user.get('permissions', []):
            raise HTTPException(status_code=403, detail=f"Permission '{permission}' required")
        return user
    return _check


async def get_admin_user(authorization: str = Header(None)):
    """Dependency: requires admin role."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.get('is_active', True):
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    project = await db.projects.find_one({"api_key": x_api_key}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return project


async def verify_agent_key(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    
    agent_key = authorization.split(" ")[1]
    if not agent_key.startswith("lfa_"):
        raise HTTPException(status_code=401, detail="Invalid agent key format")
        
    import hashlib
    key_hash = hashlib.sha256(agent_key.encode()).hexdigest()
    
    agent = await db.agent_keys.find_one({"key_hash": key_hash, "status": "active"}, {"_id": 0})
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or revoked agent key")
        
    # Check for expiration
    expires_at = agent.get("expires_at")
    if expires_at:
        from datetime import datetime, timezone
        if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
            # Optionally update status to "expired"
            await db.agent_keys.update_one({"id": agent["id"]}, {"$set": {"status": "expired"}})
            raise HTTPException(status_code=401, detail="Agent key expired")
        
    # Update last seen
    await db.agent_keys.update_one(
        {"key_hash": key_hash},
        {"$set": {"last_seen_at": datetime.now(timezone.utc).isoformat()}}
    )
    return agent
