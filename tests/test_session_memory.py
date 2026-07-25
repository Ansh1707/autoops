import redis

from agent import session_memory


class FailingRedis:
    def rpush(self, *args, **kwargs):
        raise redis.RedisError("redis down")

    def expire(self, *args, **kwargs):
        raise redis.RedisError("redis down")

    def lrange(self, *args, **kwargs):
        raise redis.RedisError("redis down")

    def ping(self):
        raise redis.RedisError("redis down")


class InMemoryRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def rpush(self, key, value):
        self.values.setdefault(key, []).append(value)

    def expire(self, key, ttl):
        self.expirations[key] = ttl

    def lrange(self, key, start, end):
        values = self.values.get(key, [])
        if end == -1:
            return values[start:]
        return values[start:end + 1]

    def ping(self):
        return True


def test_session_memory_round_trips_steps(monkeypatch):
    fake_redis = InMemoryRedis()
    monkeypatch.setattr(session_memory, "redis_client", fake_redis)

    session_memory.save_step("job-1", {"event": "tool_call", "token": "secret"})
    steps = session_memory.load_steps("job-1")

    assert steps == [{"event": "tool_call", "token": "secret"}]
    assert session_memory.session_memory_status() == {"ok": True, "backend": "redis"}


def test_session_memory_degrades_without_raising(monkeypatch):
    monkeypatch.setattr(session_memory, "redis_client", FailingRedis())

    session_memory.save_step("job-1", {"event": "tool_call"})

    assert session_memory.load_steps("job-1") == []
    status = session_memory.session_memory_status()
    assert status["ok"] is False
    assert status["backend"] == "redis"
