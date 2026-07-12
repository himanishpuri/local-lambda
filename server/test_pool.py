"""Pool logic tests — no Docker required.

`create_env` is monkeypatched to hand back fake Environments so we exercise the
acquire/release/reap logic in isolation. Run: pytest test_pool.py -v
"""
import threading
import time
import itertools

import scheduler
from scheduler import Pool, Environment, MAX_PER_FN


def fake_env_factory():
    """Returns a create_env stand-in that mints numbered fake containers."""
    counter = itertools.count()

    def create_env(function_name):
        env = Environment(function_name, f"c{next(counter)}", api=None)
        env.full_container_id = env.container_id
        env.kill = lambda reason="Manual": setattr(env, "dead", True)  # no docker
        return env

    return create_env


def test_reuse_same_container(monkeypatch):
    monkeypatch.setattr(scheduler, "create_env", fake_env_factory())
    pool = Pool()

    e1 = pool.acquire("hello")
    pool.release(e1)
    e2 = pool.acquire("hello")

    assert e1 is e2, "released container should be reused"


def test_fan_out_up_to_max(monkeypatch):
    monkeypatch.setattr(scheduler, "create_env", fake_env_factory())
    pool = Pool()

    # acquire MAX without releasing -> MAX distinct containers
    envs = [pool.acquire("hello") for _ in range(MAX_PER_FN)]
    assert len({e.container_id for e in envs}) == MAX_PER_FN


def test_cap_blocks_until_release(monkeypatch):
    monkeypatch.setattr(scheduler, "create_env", fake_env_factory())
    pool = Pool()

    held = [pool.acquire("hello") for _ in range(MAX_PER_FN)]  # pool full

    got = {}

    def waiter():
        got["env"] = pool.acquire("hello")  # must block until a release

    t = threading.Thread(target=waiter)
    t.start()
    t.join(timeout=0.3)
    assert t.is_alive(), "acquire should block while pool is at capacity"

    pool.release(held[0])
    t.join(timeout=1.0)
    assert not t.is_alive(), "acquire should return after a release"
    assert got["env"] is held[0], "freed container should be handed to the waiter"


def test_dead_container_pruned_on_reacquire(monkeypatch):
    monkeypatch.setattr(scheduler, "create_env", fake_env_factory())
    pool = Pool()

    e1 = pool.acquire("hello")
    e1.dead = True          # simulate a timed-out container the runtime API killed
    pool.release(e1)
    e2 = pool.acquire("hello")

    assert e2 is not e1, "a dead container must not be handed back out"
    assert e1 not in pool.pools["hello"], "dead container should be pruned"


def test_reap_evicts_idle_not_busy(monkeypatch):
    monkeypatch.setattr(scheduler, "create_env", fake_env_factory())
    monkeypatch.setattr(scheduler, "IDLE_TIMEOUT", 0.05)
    pool = Pool()

    idle = pool.acquire("hello")
    busy = pool.acquire("hello")
    pool.release(idle)                 # idle is free; busy stays checked out
    time.sleep(0.1)                    # both exceed IDLE_TIMEOUT in wall time

    pool.reap_idle()

    survivors = pool.pools["hello"]
    assert idle.dead, "idle container should be evicted"
    assert idle not in survivors
    assert not busy.dead, "busy container must not be evicted"
    assert busy in survivors
