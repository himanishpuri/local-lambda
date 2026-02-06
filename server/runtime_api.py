from fastapi import FastAPI, Request, Response
import json
import threading
import queue
import uuid
import uvicorn

class RuntimeAPI:
    def __init__(self):
        self.app = FastAPI()
        self.events = queue.Queue()
        self.responses = {}
        self.response_events = {}
        self.addr = "host.docker.internal:5001"

        @self.app.get("/next")
        def next():
            event = self.events.get()
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

        threading.Thread(target=lambda: uvicorn.run(self.app, host="0.0.0.0", port=5001)).start()

    def attach(self, cid):
        self.container_id = cid
        self.dead = False

    def invoke(self, payload):
        rid = str(uuid.uuid4())
        self.events.put({"id": rid, "payload": payload})
        
        event = threading.Event()
        self.response_events[rid] = event
        event.wait()

        self.response_events.pop(rid)
        return self.responses.pop(rid)