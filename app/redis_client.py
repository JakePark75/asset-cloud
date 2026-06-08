"""
redis_client.py
Redis 연결 공통 모듈.

- get_redis() : redis.Redis 인스턴스 반환 (연결 실패 시 None)
- 호출부에서 None 체크 후 사용. Redis 장애가 기존 기능에 영향 없도록 한다.
"""

import redis

_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    global _client
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception:
            _client = None

    try:
        _client = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
        _client.ping()
        return _client
    except Exception as e:
        print(f"[redis_client] Redis 연결 실패: {e}")
        return None