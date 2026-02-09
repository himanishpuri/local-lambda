import requests
import os
import sys
import logging
import socket

LAMBDA_TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", "/function")
sys.path.insert(0, LAMBDA_TASK_ROOT)

import handler

API = os.environ["RUNTIME_API"]
CONTAINER_ID = socket.gethostname()
logging.basicConfig(level=logging.INFO)

while True:
    logging.info("Waiting for next event")
    try:
        r = requests.get(f"http://{API}/{CONTAINER_ID}/next")
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to get next event: {e}")
        continue
    
    event = r.json()
    request_id = r.headers["Lambda-Runtime-Aws-Request-Id"]
    logging.info(f"Received event for request {request_id}")

    try:
        result = handler.handler(event)
        logging.info(f"Handler result: {result}")
        requests.post(
            f"http://{API}/{request_id}/response",
            json=result
        )
    except Exception as e:
        logging.error(f"Handler failed: {e}")
        requests.post(
            f"http://{API}/{request_id}/error",
            json={"error": str(e)}
        )