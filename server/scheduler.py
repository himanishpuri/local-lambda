import subprocess
import uuid
import logging
import os
import threading
import time
from runtime_api import RuntimeAPI

ENV = None  # single warm environment
IDLE_TIMEOUT = 30 
API = None  # shared RuntimeAPI instance

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Environment:
    """Represents a single container execution environment"""
    def __init__(self, function_name, container_id, api):
        self.function_name = function_name
        self.container_id = container_id  
        self.full_container_id = None  # for docker kill
        self.api = api
        self.last_used = time.time()
        self.dead = False
    
    def invoke(self, payload, timeout=3):
        total_timeout = 15  # cold start + execution, but usually we do this separately
        self.last_used = time.time()
        return self.api.invoke(self.container_id, payload, total_timeout)
    
    def kill(self, reason="Manual"):
        if not self.dead:
            kill_id = self.full_container_id or self.container_id
            logging.info(f"[RUNTIME] Killed container {self.container_id}: {reason}")
            subprocess.run(["docker", "kill", kill_id], check=False)
            self.dead = True

def get_env(function_name: str):
    global ENV, API
    
    # initialize shared API 
    if API is None:
        API = RuntimeAPI()
    
    if ENV is None or ENV.dead or ENV.function_name != function_name:
        if ENV and not ENV.dead:
            ENV.kill()
        ENV = create_env(function_name)
    return ENV

def create_env(function_name: str):
    global API
    logging.info(f"Creating new environment for function '{function_name}'")
    
    function_path = os.path.join(PROJECT_ROOT, f"functions/{function_name}")
    if not os.path.isdir(function_path):
        raise FileNotFoundError(f"Function '{function_name}' not found")

    try:
        container_id = subprocess.check_output([
            "docker", "run", "-d",
            "--add-host=host.docker.internal:host-gateway",
            "-v", f"{function_path}:/function",
            "-e", f"RUNTIME_API={API.addr}",
            "-e", f"LAMBDA_TASK_ROOT=/function",
            "local-lambda-runtime"
        ]).decode().strip()
        # hostname inside container is only first 12 chars, otherwise its 64 bits
        short_id = container_id[:12]
        logging.info(f"Container {short_id} (full: {container_id}) created for function '{function_name}'")
        
        # wait for container
        for _ in range(10):
            status = subprocess.check_output([
                "docker", "inspect", "-f", "{{.State.Status}}", container_id
            ]).decode().strip()
            if status == "running":
                break
            time.sleep(0.1)
        else:
            logging.warning(f"Container {container_id} may not be ready")
            
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create container: {e}")
        raise

    # short ID for tracking
    env = Environment(function_name, short_id, API)
    env.full_container_id = container_id  # store full ID for killing
    return env

# background reaper thread for idle eviction
def reap_idle():
    global ENV
    while True:
        time.sleep(5)
        if ENV and not ENV.dead:
            idle = time.time() - ENV.last_used
            if idle > IDLE_TIMEOUT:
                logging.info(f"[SCHEDULER] Evicting idle container after {idle:.1f}s")
                ENV.kill("Idle eviction")
                ENV = None

threading.Thread(target=reap_idle, daemon=True).start()