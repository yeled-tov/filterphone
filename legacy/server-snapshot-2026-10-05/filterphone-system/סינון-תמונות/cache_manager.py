"""
מנהל cache — Redis
שומר תוצאות בדיקה לפי hash של התמונה ולפי רמת סינון!
"""
import redis
import hashlib
import json
from config import REDIS_HOST, REDIS_PORT, CACHE_TTL_DAYS

_redis = None

# ---- מתג כיבוי קאש לטסטים! ----
USE_CACHE = False
# ---------------------------------

def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    return _redis

def image_hash(image_bytes):
    return hashlib.sha256(image_bytes).hexdigest()

def get_cached_result(img_bytes, level):
    if not USE_CACHE:
        return None
    try:
        h = image_hash(img_bytes)
        r = get_redis()
        # החלוקה החדשה: קאש נפרד לכל רמה!
        data = r.get(f"fp:img:v5:level_{level}:{h}")
        if data:
            return json.loads(data)
    except:
        pass
    return None

def set_cached_result(img_bytes, result, level):
    if not USE_CACHE:
        return
    try:
        h = image_hash(img_bytes)
        r = get_redis()
        # החלוקה החדשה: קאש נפרד לכל רמה!
        r.setex(f"fp:img:v5:level_{level}:{h}", CACHE_TTL_DAYS * 86400, json.dumps(result))
    except:
        pass

def get_filter_level(client_ip):
    try:
        r = get_redis()
        level = r.get(f"filterphone:vpn_ip:{client_ip}:filter_level")
        return int(level) if level else 3
    except:
        return 3

def increment_stat(key):
    try:
        r = get_redis()
        r.incr(f"filterphone:stats:{key}")
    except:
        pass
