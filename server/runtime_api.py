import json
import threading
import queue
import uuid
import uvicorn
import logging
import subprocess
import time
from fastapi import FastAPI, Request, Response


class HandlerError(Exception):
    """The handler raised — the container is still healthy and reusable."""


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
            # container long-polls here; block until its queue has an event
            queue_ = self.container_queues.setdefault(container_id, queue.Queue())
            event = queue_.get()
            if event["id"] in self.event_picked_events:
                self.event_picked_events[event["id"]].set()
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

        @self.app.post("/{rid}/error")
        async def error(rid: str, request: Request):
            body = await request.json()
            # marker so invoke() can distinguish a handler error from a result
            self.responses[rid] = {"__error__": body}
            if rid in self.response_events:
                self.response_events[rid].set()
            return {}

        threading.Thread(target=lambda: uvicorn.run(self.app, host="0.0.0.0", port=5001), daemon=True).start()

    def invoke(self, container_id, payload, total_timeout=15):
        rid = str(uuid.uuid4())
        queue_ = self.container_queues.setdefault(container_id, queue.Queue())

        # Phase 1 — enqueue the event and wait for the container to pick it up
        # (covers cold-start latency).
        picked_event = threading.Event()
        self.event_picked_events[rid] = picked_event
        queue_.put({"id": rid, "payload": payload})

        start_time = time.time()
        if not picked_event.wait(timeout=total_timeout):
            logging.error(f"[RUNTIME] Container {container_id} never picked up event {rid}")
            subprocess.run(["docker", "kill", container_id], check=False)
            self.event_picked_events.pop(rid, None)
            raise RuntimeError("Container failed to start or pick up event")

        remaining_timeout = total_timeout - (time.time() - start_time)
        if remaining_timeout <= 0:
            logging.warning(f"[RUNTIME] Cold start exhausted the timeout budget for {rid}")
            subprocess.run(["docker", "kill", container_id], check=False)
            self.event_picked_events.pop(rid, None)
            raise TimeoutError("Cold start timeout")

        # Phase 2 — wait for the handler result within the remaining budget.
        # The /response and /error routes .set() this event when they land.
        done = threading.Event()
        self.response_events[rid] = done
        # guard the race where the result landed before we registered `done`
        if rid in self.responses:
            done.set()
        if not done.wait(timeout=remaining_timeout):
            logging.info(f"[RUNTIME] Execution timeout — killing container {container_id}")
            subprocess.run(["docker", "kill", container_id], check=False)
            self.response_events.pop(rid, None)
            self.event_picked_events.pop(rid, None)
            raise TimeoutError(f"Function execution timed out after {remaining_timeout:.1f}s")

        self.response_events.pop(rid, None)
        self.event_picked_events.pop(rid, None)
        result = self.responses.pop(rid)
        if isinstance(result, dict) and "__error__" in result:
            err = result["__error__"]
            raise HandlerError(err.get("error", err) if isinstance(err, dict) else err)
        return result