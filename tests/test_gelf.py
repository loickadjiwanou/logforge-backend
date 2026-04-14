import asyncio
import sys
import os
import json
import socket
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import db

async def run_tests():
    project = await db.projects.find_one({})
    if not project:
        print("No project found.")
        key = "TEST_KEY"
    else:
        key = project["api_key"]
        print(f"Found API key: {key}")

    # HTTP GELF — uses the same payload format as /api/logs/ingest
    payload = {
        "level": "error",
        "message": "Testing GELF HTTP Ingest",
        "channel": "test-http",
        "environment": "development",
        "metadata": {"source": "test_gelf.py"},
        "tags": ["test", "gelf"]
    }
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/logs/gelf", 
        data=json.dumps(payload).encode(), 
        headers={"X-API-Key": key, "Content-Type": "application/json"}, 
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req)
        print("HTTP Sent", resp.status)
    except Exception as e:
        print("HTTP Exception", e)

    # UDP GELF — keeps the standard GELF format with _api_key for shipper compatibility
    upayload = {
        "version": "1.1",
        "host": "test-host",
        "short_message": "Testing UDP Gelf",
        "level": "error",
        "_api_key": key,
        "_channel": "test-udp",
        "_environment": "development"
    }
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps(upayload).encode(), ("127.0.0.1", 12201))
    print("UDP Sent")
    
    # Wait a bit for ingestion
    await asyncio.sleep(2)
    
    # Verify in DB
    logs = await db.logs.find({"channel": {"$in": ["test-http", "test-udp"]}}).to_list(10)
    print(f"Found {len(logs)} logs in DB for our test channels")
    for l in logs:
        print(f" > {l['channel']}: {l['message']}")

if __name__ == "__main__":
    asyncio.run(run_tests())
