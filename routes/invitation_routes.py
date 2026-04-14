"""
Invitation management routes — multi-tenant company invitations.
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from typing import List
import uuid
import secrets

from database import db
from auth import get_admin_user

router = APIRouter()


class InviteRequest(BaseModel):
    emails: List[str]


@router.get("/")
async def list_invitations(user=Depends(get_admin_user)):
    """List all invitations for the current admin's company."""
    company_id = user.get("company_id")
    if not company_id:
        return {"invitations": []}
    # Never expose the raw token
    cursor = db.invitations.find({"company_id": company_id}, {"_id": 0, "token": 0})
    invitations = await cursor.to_list(length=500)
    return {"invitations": invitations}


@router.post("/")
async def create_invitations(
    req: InviteRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_admin_user),
):
    """Send invitation emails to one or more email addresses."""
    company_id = user.get("company_id")
    if not company_id:
        raise HTTPException(
            status_code=400,
            detail="Your account is not linked to a company.",
        )

    from utils.email_utils import get_smtp_config
    smtp = await get_smtp_config(company_id)
    if not smtp or not smtp.get("enabled", False):
        raise HTTPException(
            status_code=422,
            detail="SMTP_NOT_CONFIGURED",
        )

    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=24)
    results = []

    for raw_email in req.emails:
        email = raw_email.strip().lower()
        if not email:
            continue

        # Already a member of this company?
        existing_user = await db.users.find_one(
            {"email": email, "company_id": company_id}
        )
        if existing_user:
            results.append({"email": email, "status": "already_member"})
            continue

        # Already has a pending invitation?
        existing_inv = await db.invitations.find_one(
            {"email": email, "company_id": company_id, "status": "pending"}
        )
        if existing_inv:
            results.append({"email": email, "status": "already_invited"})
            continue

        token = secrets.token_urlsafe(32)
        inv = {
            "id": str(uuid.uuid4()),
            "email": email,
            "company_id": company_id,
            "company_name": company["name"],
            "token": token,
            "status": "pending",
            "invited_by": user["id"],
            "invited_by_name": user.get("name", ""),
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        await db.invitations.insert_one(inv)

        from utils.email_utils import send_invitation_email
        background_tasks.add_task(
            send_invitation_email,
            email,
            company["name"],
            token,
            user.get("name", ""),
            company_id,
        )
        results.append({"email": email, "status": "invited"})

    return {"results": results}


@router.delete("/{invitation_id}")
async def delete_invitation(invitation_id: str, user=Depends(get_admin_user)):
    """Cancel and permanently delete an invitation."""
    company_id = user.get("company_id")
    inv = await db.invitations.find_one(
        {"id": invitation_id, "company_id": company_id}
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    await db.invitations.delete_one({"id": invitation_id, "company_id": company_id})
    return {"message": "Invitation deleted."}


@router.post("/{invitation_id}/resend")
async def resend_invitation(
    invitation_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(get_admin_user),
):
    """Generate a fresh token and resend the invitation email."""
    company_id = user.get("company_id")

    from utils.email_utils import get_smtp_config
    smtp = await get_smtp_config(company_id)
    if not smtp or not smtp.get("enabled", False):
        raise HTTPException(status_code=422, detail="SMTP_NOT_CONFIGURED")

    inv = await db.invitations.find_one(
        {"id": invitation_id, "company_id": company_id}
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if inv["status"] == "accepted":
        raise HTTPException(
            status_code=400, detail="This invitation has already been accepted."
        )

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=24)

    await db.invitations.update_one(
        {"id": invitation_id, "company_id": company_id},
        {
            "$set": {
                "token": token,
                "expires_at": expires_at.isoformat(),
                "status": "pending",
            }
        },
    )

    from utils.email_utils import send_invitation_email
    background_tasks.add_task(
        send_invitation_email,
        inv["email"],
        inv["company_name"],
        token,
        user.get("name", ""),
        company_id,
    )
    return {"message": "Invitation resent."}
