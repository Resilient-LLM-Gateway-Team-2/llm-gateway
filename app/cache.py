import os
import json
import logging
import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

def get_cached_response(prompt_key: str) -> dict | None:
    try:
        data = redis_client.get(prompt_key)
        if data:
            logger.info("Cache hit for prompt key: %s", prompt_key)
            return json.loads(data)
    except Exception as e:
        logger.error("Redis get error: %s", e)
    return None

def set_cached_response(prompt_key: str, response_data: dict, ttl: int = 3600):
    try:
        redis_client.setex(prompt_key, ttl, json.dumps(response_data))
        logger.info("Session data cached for prompt key: %s", prompt_key)
    except Exception as e:
        logger.error("Redis set error: %s", e)
