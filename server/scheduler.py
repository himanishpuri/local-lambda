import subprocess
import logging
import os
import threading
import time

IDLE_TIMEOUT = 30       # seconds a container may sit idle before eviction
MAX_PER_FN = 3          # warm containers per function (concurrency ceiling)
API = None              # shared RuntimeAPI instance

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Environment:
    """A single container execution environment."""
    def __init__(self, function_name, container_id, api):
        self.function_name = function_name
        self.container_id = container_id      # short 12-char id, used for API routing
        self.full_container_id = None         # 64-char id, used for `docker kill`
        self.api = api
        self.last_used = time.time()
        self.dead = False
        self.busy = False                     # currently serving an invoke

    def invoke(self, payload, timeout=15):
        self.last_used = time.time()
        try:
            return self.api.invoke(self.container_id, payload, timeout)
        except (TimeoutError, RuntimeError):
            # timeout / pickup failure -> the runtime API `docker kill`ed this
            # container; mark it dead so the pool prunes it. (HandlerError means
            # only user code threw; the container is still healthy, so it bubbles
            # up untouched and stays in the pool.)
            self.dead = True
            raise

    def kill(self, reason="Manual"):
        if not self.dead:
            kill_id = self.full_container_id or self.container_id
            logging.info(f"[RUNTIME] Killed container {self.container_id}: {reason}")
            subprocess.run(["docker", "kill", kill_id], check=False)
            self.dead = True


class Pool:
    """Per-function pool of warm containers.

    Concurrent invokes for the same function fan out across up to MAX_PER_FN
    containers. When all are busy, callers block until one is released.
    """
    def __init__(self):
        self.pools = {}                       # function_name -> list[Environment]
        self.cond = threading.Condition()     # guards pools + wakes waiters on release

    def acquire(self, function_name):
        with self.cond:
            while True:
                pool = self.pools.setdefault(function_name, [])
                # drop containers killed by the reaper or failed invokes
                pool[:] = [e for e in pool if not e.dead]

                for env in pool:
                    if not env.busy:
                        env.busy = True
                        return env

                if len(pool) < MAX_PER_FN:
                    # release the lock during the (slow) cold start so other
                    # functions aren't blocked on our `docker run`
                    self.cond.release()
                    try:
                        env = create_env(function_name)
                    finally:
                        self.cond.acquire()
                    env.busy = True
                    self.pools[function_name].append(env)
                    return env

                # at capacity — wait for a release, then retry
                self.cond.wait()

    def release(self, env):
        with self.cond:
            env.busy = False
            self.cond.notify()

    def reap_idle(self):
        with self.cond:
            for pool in self.pools.values():
                survivors = []
                for env in pool:
                    idle = time.time() - env.last_used
                    if not env.busy and not env.dead and idle > IDLE_TIMEOUT:
                        logging.info(f"[SCHEDULER] Evicting idle container after {idle:.1f}s")
                        env.kill("Idle eviction")
                    else:
                        survivors.append(env)
                pool[:] = survivors


POOL = Pool()

def get_env(function_name: str):
    global API
    if API is None:
        from runtime_api import RuntimeAPI  # lazy: keeps web stack out of pool tests
        API = RuntimeAPI()
    return POOL.acquire(function_name)

def release(env):
    POOL.release(env)

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
        # hostname inside container is only the first 12 chars, not the full 64
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

    env = Environment(function_name, short_id, API)   # short id for tracking/routing
    env.full_container_id = container_id              # full id for killing
    return env

# background reaper thread for idle eviction
def reap_idle():
    while True:
        time.sleep(5)
        POOL.reap_idle()

threading.Thread(target=reap_idle, daemon=True).start()
