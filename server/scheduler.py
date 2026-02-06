import subprocess
import uuid
import logging
import os
from runtime_api import RuntimeAPI

ENV = None  # single warm environment

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
    
    function_path = os.path.abspath(f"functions/{function_name}")
    if not os.path.isdir(function_path):
        raise FileNotFoundError(f"Function '{function_name}' not found")

    try:
        container_id = subprocess.check_output([
            "docker", "run", "-d",
            "--add-host=host.docker.internal:host-gateway",
            "-v", f"{function_path}:/app",
            "-e", f"RUNTIME_API={api.addr}",
            "local-lambda-runtime"
        ]).decode().strip()
        logging.info(f"Container {container_id} created for function '{function_name}'")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create container: {e}")
        raise

    api.attach(container_id)
    return api