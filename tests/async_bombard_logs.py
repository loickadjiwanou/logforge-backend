# python3 async_bombard_logs.py 10000 200
# 10000 = total logs
# 200 = concurrence (logs envoyés en parallèle)

import aiohttp
import asyncio
import argparse
import random
import time
import json

API_KEY = "lv_8e543b4dc9aaf34fc1fa4a0cd625526d68371cb6d2e742dc"
URL = "http://localhost:8000/api/logs/ingest"

HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": API_KEY
}

LEVELS = ["info", "debug", "critical", "error", "warning"]

BASE_PAYLOAD = {
    "message": "Database connection timeout",
    "channel": "react native",
    "environment": "production",
    "metadata": {
        "host": "db-primary",
        "timeout_ms": 5000
    },
    "stack_trace": "Error: Connection timeout\n    at connect (db.js:42)",
    "tags": ["database", "timeout"]
}


def generate_payload():
    payload = BASE_PAYLOAD.copy()
    payload["level"] = random.choice(LEVELS)
    return payload


async def send_log(session):
    payload = generate_payload()
    payload_json = json.dumps(payload)

    try:
        async with session.post(URL, headers=HEADERS, data=payload_json) as r:
            return r.status, len(payload_json.encode())
    except:
        return 0, 0


async def worker(session, queue, stats):
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        status, size = await send_log(session)

        if status in [200, 201]:
            stats["success"] += 1
        else:
            stats["fail"] += 1

        stats["bytes"] += size
        queue.task_done()


async def bombard(total, concurrency):

    queue = asyncio.Queue()

    for _ in range(total):
        queue.put_nowait(1)

    stats = {
        "success": 0,
        "fail": 0,
        "bytes": 0
    }

    connector = aiohttp.TCPConnector(limit=concurrency)

    async with aiohttp.ClientSession(connector=connector) as session:

        tasks = [
            asyncio.create_task(worker(session, queue, stats))
            for _ in range(concurrency)
        ]

        start = time.time()

        await queue.join()

        for task in tasks:
            task.cancel()

        duration = time.time() - start

    total_kb = stats["bytes"] / 1024
    total_mb = stats["bytes"] / (1024 * 1024)
    total_gb = stats["bytes"] / (1024 * 1024 * 1024)

    print("\n----- RESULT -----")
    print(f"Total requests : {total}")
    print(f"Success        : {stats['success']}")
    print(f"Failed         : {stats['fail']}")
    print(f"Time           : {duration:.2f}s")
    print(f"Req/sec        : {total/duration:.2f}")
    print(f"Data sent      : {total_kb:.2f} KB ({total_mb:.2f} MB | {total_gb:.4f} GB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("total", type=int, help="total logs")
    parser.add_argument("concurrency", type=int, help="parallel workers")

    args = parser.parse_args()

    asyncio.run(bombard(args.total, args.concurrency))