import logging
import json
import os
import aiofiles
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from scheduler import get_env, release
from runtime_api import HandlerError

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

    # get_env may block (cold start / pool full) and invoke() blocks on the
    # container — run both off the event loop so requests overlap across the pool
    try:
        env = await run_in_threadpool(get_env, function_name)
    except FileNotFoundError as e:
        return {"errorMessage": str(e), "errorType": "FunctionNotFound"}

    try:
        result = await run_in_threadpool(env.invoke, payload)
        logging.info(f"Invocation result: {result}")
        return result
    except HandlerError as e:
        logging.error(f"Handler raised: {e}")
        return {"errorMessage": str(e), "errorType": "HandlerError"}
    except Exception as e:
        logging.error(f"Invocation failed: {e}")
        return {"errorMessage": str(e), "errorType": "ExecutionError"}
    finally:
        release(env)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)