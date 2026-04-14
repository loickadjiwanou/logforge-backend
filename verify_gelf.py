import asyncio
import os
import sys
from database import db

async def verify():
    print("Checking database for GELF logs...")
    logs = await db.logs.find({"channel": {"$in": ["test-http", "test-udp"]}}).to_list(10)
    for l in logs:
        print(f"\nLog ID: {l.get('id')}")
        print(f"Message: {l.get('message')}")
        print(f"Channel: {l.get('channel')}")
        print(f"Ingest Protocol: {l.get('ingest_protocol')}")
        print(f"GELF Version: {l.get('gelf_version')}")
        print(f"Source Host: {l.get('source_host')}")
        print(f"Metadata: {l.get('metadata')}")

if __name__ == "__main__":
    asyncio.run(verify())
