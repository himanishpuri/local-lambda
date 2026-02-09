import logging
import json
import os
import aiofiles
from fastapi import FastAPI, Request
from pydantic import BaseModel
from scheduler import get_env

app = FastAPI()

logging.basicConfig(level=logging.INFO)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

@app.post("/invoke/{function_name}")
async def invoke(function_name: str, request: Request):
    logging.info(f"Invocation request for function '{function_name}'")
    
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        logging.info("No payload provided, using default event.json")
        event_file = os.path.join(PROJECT_ROOT, f"functions/{function_name}/event.json")
        async with aiofiles.open(event_file, 'r') as f:
            content = await f.read()
            payload = json.loads(content)

    env = get_env(function_name)
    
    try:
        result = env.invoke(payload)
        logging.info(f"Invocation result: {result}")
        return result
    except Exception as e:
        logging.error(f"Invocation failed: {e}")
        return {"errorMessage": str(e), "errorType": "ExecutionError"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)