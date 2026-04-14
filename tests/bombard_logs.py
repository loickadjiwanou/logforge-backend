# Use with caution: this script will send a lot of logs to the server
# python3 bombard_logs.py 100
# 100 is the number of logs to send
# params: x-api-key et URL

import requests
import argparse
import time
import random
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


def bombard(count):
    print(f"Sending {count} logs...")

    success = 0
    fail = 0
    total_bytes = 0

    start = time.time()

    for i in range(count):

        payload = BASE_PAYLOAD.copy()
        payload["level"] = random.choice(LEVELS)

        payload_json = json.dumps(payload)
        payload_size = len(payload_json.encode("utf-8"))
        total_bytes += payload_size

        try:
            r = requests.post(URL, headers=HEADERS, data=payload_json)

            if r.status_code in [200, 201]:
                success += 1
            else:
                fail += 1
                print(f"❌ Request {i+1} failed: {r.status_code}")

        except Exception as e:
            fail += 1
            print(f"⚠️ Request {i+1} error: {e}")

    duration = time.time() - start

    total_kb = total_bytes / 1024
    total_mb = total_bytes / (1024 * 1024)
    total_gb = total_bytes / (1024 * 1024 * 1024)

    print("\n----- RESULT -----")
    print(f"Total requests : {count}")
    print(f"Success        : {success}")
    print(f"Failed         : {fail}")
    print(f"Time           : {duration:.2f}s")
    print(f"Req/sec        : {count/duration:.2f}")
    print(f"Data sent      : {total_kb:.2f} KB ({total_mb:.2f} MB | {total_gb:.4f} GB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bombard LogForge ingest API")
    parser.add_argument("count", type=int, help="Number of logs to send")

    args = parser.parse_args()

    bombard(args.count)