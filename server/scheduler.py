import subprocess
import uuid
import logging
from runtime_api import RuntimeAPI

ENV = None  # single warm environment

def get_env():
    global ENV
    if ENV is None or ENV.dead:
        ENV = create_env()
    return ENV

def create_env():
    api = RuntimeAPI()
    logging.info("Creating new environment")
    try:
        container_id = subprocess.check_output([
            "docker", "run", "-d",
            "--add-host=host.docker.internal:host-gateway",
            "-e", f"RUNTIME_API={api.addr}",
            "local-lambda-runtime"
        ]).decode().strip()
        logging.info(f"Container {container_id} created")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create container: {e}")
        raise

    api.attach(container_id)
    return api