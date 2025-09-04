import time
from typing import Tuple, Dict

# key -> (window_start, count)
_bucket: Dict[str, Tuple[float,int]] = {}

def check_rate(key: str, limit: int, per_seconds: float):
    now = time.time()
    win, cnt = _bucket.get(key, (now, 0))
    if now - win > per_seconds:
        win, cnt = now, 0
    cnt += 1
    _bucket[key] = (win, cnt)
    if cnt > limit:
        retry_after = max(0, int((win + per_seconds - now)*1000))
        return False, retry_after
    return True, 0
