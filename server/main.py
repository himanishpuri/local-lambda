import logging
import json
from fastapi import FastAPI, Request
from pydantic import BaseModel
from scheduler import get_env

app = FastAPI()

logging.basicConfig(level=logging.INFO)

@app.post("/invoke/{function_name}")
async def invoke(function_name: str, request: Request):
    logging.info(f"Invocation request for function '{function_name}'")
    
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        logging.info("No payload provided, using default event.json")
        with open(f"functions/{function_name}/event.json") as f: # temporary default
            payload = json.load(f)

    env = get_env(function_name)
    result = env.invoke(payload)
    logging.info(f"Invocation result: {result}")
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)