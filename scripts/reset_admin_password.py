import asyncio
import os
import sys
import uuid
import secrets
from pathlib import Path

# Add backend directory to sys.path to import from database and auth
ROOT_DIR = Path(__file__).parent.parent
sys.path.append(str(ROOT_DIR))

from database import db
from auth import hash_password

async def reset_password(email, new_password):
    user = await db.users.find_one({"email": email, "role": "admin"})
    if not user:
        # Check if any user with this email exists regardless of role, for flexibility
        user = await db.users.find_one({"email": email})
        if not user:
            print(f"Error: Admin user with email '{email}' not found.")
            return False
        print(f"Warning: User '{email}' found but does not have the 'admin' role. Resetting anyway.")

    hashed = hash_password(new_password)
    result = await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password": hashed, "is_active": True}}
    )

    if result.modified_count > 0:
        print(f"Success: Password for {email} has been reset and account activated.")
        return True
    else:
        print(f"Error: Failed to update password for {email}.")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("LogForge Emergency Admin Password Reset")
        print("Usage: python reset_admin_password.py <admin_email> <new_password>")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    asyncio.run(reset_password(email, password))
