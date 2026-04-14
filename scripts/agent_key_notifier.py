import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
import logging

# Add the parent directory to sys.path to allow importing from database, utils, etc.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db
from utils.email_utils import send_agent_key_expiration_alert

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def check_expiring_keys():
    """Iterate through agent keys and send alerts if they are about to expire."""
    logger.info("Starting agent key expiration check...")
    
    # 1. Find all active agent keys with an expiration date
    keys = await db.agent_keys.find({"status": "active", "expires_at": {"$ne": None}}).to_list(1000)

    if not keys:
        logger.info("No active agent keys with expiration found.")
        return

    now = datetime.now(timezone.utc)

    for key in keys:
        # Resolve company-scoped admin emails for this key
        company_id = key.get("company_id")
        admin_query = {"role": "admin", "is_active": {"$ne": False}}
        if company_id:
            admin_query["company_id"] = company_id
        admins = await db.users.find(admin_query, {"email": 1, "_id": 0}).to_list(100)
        admin_emails = [a["email"] for a in admins]
        if not admin_emails:
            logger.warning(f"No active admin emails found for company {company_id}. Skipping key {key['id']}.")
            continue
        expires_at_str = key.get("expires_at")
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
        except (ValueError, TypeError):
            logger.error(f"Invalid expiration date for key {key['id']}: {expires_at_str}")
            continue
            
        diff = expires_at - now
        minutes_left = diff.total_seconds() / 60
        
        # Thresholds: 1, 2, 3 days (1440, 2880, 4320 minutes). 
        # We use a 15-minute window to ensure we only send around the exact time, preventing retro-active spam.
        target_threshold = None
        tolerance = 15
        
        if 1440 - tolerance <= minutes_left <= 1440 + tolerance:
            target_threshold = 1
        elif 2880 - tolerance <= minutes_left <= 2880 + tolerance:
            target_threshold = 2
        elif 4320 - tolerance <= minutes_left <= 4320 + tolerance:
            target_threshold = 3
            
        if target_threshold:
            # Check if we already notified for this threshold (or a more urgent one)
            last_notified = key.get("last_notified_threshold")
            if last_notified is None or target_threshold < last_notified:
                # Send alert
                time_desc = f"{target_threshold} day(s)"
                logger.info(f"Sending alert for {key['description']} (threshold: {target_threshold} days, actual: {minutes_left:.2f} minutes left)")
                
                success = await send_agent_key_expiration_alert(
                    admin_emails=admin_emails,
                    key_description=key.get("description", "Agent Key"),
                    expires_at=expires_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    days_left=time_desc,
                    company_id=company_id
                )
                
                if success:
                    # Update key with last notified threshold (descending: 3 -> 2 -> 1)
                    await db.agent_keys.update_one(
                        {"id": key["id"]},
                        {"$set": {"last_notified_threshold": target_threshold}}
                    )
                else:
                    logger.error(f"Failed to send email alert for key {key['id']}")
        
        # Also mark as expired if passed
        if minutes_left <= 0 and key.get("status") != "expired":
            logger.info(f"Marking key {key['id']} as expired")
            await db.agent_keys.update_one(
                {"id": key["id"]},
                {"$set": {"status": "expired"}}
            )

async def check_expiring_keys_loop():
    while True:
        try:
            await check_expiring_keys()
        except Exception as e:
            logger.error(f"Error in expiration check loop: {e}")
            
        # Run every minute to ensure high precision for "jour pour jour, heure pour heure" alerts
        await asyncio.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        asyncio.run(check_expiring_keys())
    else:
        logger.info("Starting expiration check daemon (checking every minute)...")
        asyncio.run(check_expiring_keys_loop())
