import json
import threading
import queue
import uuid
import uvicorn
import logging
import subprocess
import time
from fastapi import FastAPI, Request, Response

class RuntimeAPI:
    def __init__(self):
        self.app = FastAPI()
        self.container_queues = {}  # per-container event queues
        self.responses = {}
        self.response_events = {}
        self.event_picked_events = {}  # track when event is picked up
        self.addr = "host.docker.internal:5001"

        @self.app.get("/{container_id}/next")
        def next(container_id: str):
            logging.info(f"[API] /next called with container_id='{container_id}', length={len(container_id)}")
            logging.info(f"[API] Available queues: {list(self.container_queues.keys())}")
            if container_id not in self.container_queues:
                logging.warning(f"[API] Queue not found for '{container_id}', creating new one")
                self.container_queues[container_id] = queue.Queue()
            else:
                logging.info(f"[API] Found existing queue for '{container_id}'")
            logging.info(f"[API] Container {container_id[:12]} waiting on queue.get()...")
            event = self.container_queues[container_id].get()
            logging.info(f"[API] Container {container_id[:12]} got event {event['id']}, signaling...")

            if event["id"] in self.event_picked_events:
                self.event_picked_events[event["id"]].set()
                logging.info(f"[API] Signaled picked_event for {event['id']}")
            return Response(
                content=json.dumps(event["payload"]),
                headers={"Lambda-Runtime-Aws-Request-Id": event["id"]},
            )

        @self.app.post("/{rid}/response")
        async def response(rid: str, request: Request):
            body = await request.json()
            self.responses[rid] = body
            if rid in self.response_events:
                self.response_events[rid].set()
            return {}

        threading.Thread(target=lambda: uvicorn.run(self.app, host="0.0.0.0", port=5001), daemon=True).start()

    def invoke(self, container_id, payload, total_timeout=15):
        rid = str(uuid.uuid4())
        logging.info(f"[INVOKE] Starting invoke for container '{container_id}', length={len(container_id)}, request {rid}")
        
        # check container has a queue
        if container_id not in self.container_queues:
            self.container_queues[container_id] = queue.Queue()
            logging.info(f"[INVOKE] Created queue for container '{container_id}'")
        else:
            logging.info(f"[INVOKE] Using existing queue for container '{container_id}'")
        
        logging.info(f"[INVOKE] Current queues: {list(self.container_queues.keys())}")
        
        # track when event is picked up by container
        picked_event = threading.Event()
        self.event_picked_events[rid] = picked_event
        
        # put event in THIS container's queue
        logging.info(f"[INVOKE] Putting event {rid} in queue for container '{container_id}'")
        self.container_queues[container_id].put({"id": rid, "payload": payload})
        logging.info(f"[INVOKE] Event {rid} put in queue, waiting for pickup...")
        
        start_time = time.time()
        
        # wait for container to pick up the event (cold start phase)
        if not picked_event.wait(timeout=total_timeout):
            logging.error(f"[RUNTIME] Container {container_id} never picked up event")
            subprocess.run(["docker", "kill", container_id], check=False)
            self.event_picked_events.pop(rid, None)
            raise RuntimeError("Container failed to start or pick up event")
        
        logging.info(f"[INVOKE] Event {rid} picked up!")
        pickup_time = time.time() - start_time
        remaining_timeout = total_timeout - pickup_time
        
        if remaining_timeout <= 0:
            logging.warning(f"[RUNTIME] Cold start took {pickup_time:.1f}s, no time left for execution")
            subprocess.run(["docker", "kill", container_id], check=False)
            self.event_picked_events.pop(rid, None)
            raise TimeoutError("Cold start timeout")
        
        # now wait for execution to complete with remaining time
        response_event = threading.Event()
        self.response_events[rid] = response_event
        
        exec_start = time.time()
        while rid not in self.responses:
            if time.time() - exec_start > remaining_timeout:
                logging.info(f"[RUNTIME] Execution timeout - killing container {container_id}")
                subprocess.run(["docker", "kill", container_id], check=False)
                self.response_events.pop(rid, None)
                self.event_picked_events.pop(rid, None)
                raise TimeoutError(f"Function execution timed out after {remaining_timeout:.1f} seconds")
            time.sleep(0.01)  # to avoid busy waiting
        
        self.response_events.pop(rid, None)
        self.event_picked_events.pop(rid, None)
        return self.responses.pop(rid)