import subprocess
import uuid
import logging
import os
from runtime_api import RuntimeAPI

ENV = None  # single warm environment

# Get the project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_env(function_name: str):
    global ENV
    if ENV is None or ENV.dead or ENV.function_name != function_name:
        if ENV and not ENV.dead:
            ENV.kill()
        ENV = create_env(function_name)
    return ENV

def create_env(function_name: str):
    api = RuntimeAPI()
    api.function_name = function_name
    logging.info(f"Creating new environment for function '{function_name}'")
    
    function_path = os.path.join(PROJECT_ROOT, f"functions/{function_name}")
    if not os.path.isdir(function_path):
        raise FileNotFoundError(f"Function '{function_name}' not found")

    try:
        container_id = subprocess.check_output([
            "docker", "run", "-d",
            "--add-host=host.docker.internal:host-gateway",
            "-v", f"{function_path}:/function",
            "-e", f"RUNTIME_API={api.addr}",
            "-e", f"LAMBDA_TASK_ROOT=/function",
            "local-lambda-runtime"
        ]).decode().strip()
        logging.info(f"Container {container_id} created for function '{function_name}'")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create container: {e}")
        raise

    api.attach(container_id)
    return api