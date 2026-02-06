import logging
from fastapi import FastAPI
from pydantic import BaseModel
from scheduler import get_env

app = FastAPI()

logging.basicConfig(level=logging.INFO)

class InvokeRequest(BaseModel):
    payload: dict = {}

@app.post("/invoke")
def invoke(request: InvokeRequest):
    logging.info("Invocation request received")
    env = get_env()
    result = env.invoke(request.payload)
    logging.info(f"Invocation result: {result}")
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)