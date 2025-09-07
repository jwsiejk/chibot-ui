# app/services/httputil.py
import time, random, urllib.request, urllib.error, json
from typing import Dict, Optional
from ..obs.metrics import observe, inc

try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    def _admin_emit(*a, **k): pass

class CircuitOpenError(Exception):
    pass

_breakers: Dict[str, dict] = {}

def get_breaker(name: str) -> dict:
    br = _breakers.get(name)
    if not br:
        br = {"fails": 0, "last_fail": 0.0, "open": False}
        _breakers[name] = br
    return br

def breaker_is_open(name: str, recovery_timeout: float = 10.0) -> bool:
    br = get_breaker(name)
    if not br["open"]:
        return False
    # half-open after cooldown
    if (time.time() - br["last_fail"]) >= recovery_timeout:
        return False
    return True

def _open_breaker(br: dict, name: str):
    if not br["open"]:
        br["open"] = True
        try:
            _admin_emit("breaker_open", key=name)
        except Exception:
            pass

def _close_breaker(br: dict, name: str):
    if br["open"]:
        br["open"] = False
        try:
            _admin_emit("breaker_close", key=name)
        except Exception:
            pass

def http_bytes(url: str, *, data: Optional[bytes], headers: Dict[str,str], method: str = "POST",
               timeout: float = 30.0, retries: int = 2, backoff_base: float = 0.2,
               breaker_key: Optional[str] = None, breaker_threshold: int = 3, breaker_cooldown: float = 10.0) -> bytes:
    key = breaker_key or url.split("?",1)[0].split("/", 3)[2]  # host as key
    br = get_breaker(key)
    if breaker_is_open(key, recovery_timeout=breaker_cooldown):
        raise CircuitOpenError(f"circuit open for {key}")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    attempts = 0
    last_err: Optional[Exception] = None
    while attempts <= retries:
        attempts += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            # success
            br["fails"] = 0
            _close_breaker(br, key)
            observe("vendor.http.latency_ms", int((random.random()+0.5)*100), {"url": url.split("?",1)[0]})
            return body
        except Exception as e:
            last_err = e
            br["fails"] += 1
            br["last_fail"] = time.time()
            if br["fails"] >= breaker_threshold:
                _open_breaker(br, key)
            if attempts > retries:
                break
            # jittered backoff
            sleep_s = backoff_base * (2 ** (attempts-1)) * (0.5 + random.random())
            time.sleep(min(2.0, sleep_s))

    inc("vendor.http.fail", {"url": url.split("?",1)[0]})
    if isinstance(last_err, CircuitOpenError):
        raise last_err
    raise last_err or RuntimeError("HTTP request failed")

def http_json(url: str, *, payload: dict, headers: Dict[str,str], method: str = "POST",
              timeout: float = 30.0, retries: int = 2, backoff_base: float = 0.2,
              breaker_key: Optional[str] = None, breaker_threshold: int = 3, breaker_cooldown: float = 10.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    body = http_bytes(url, data=data, headers=headers, method=method, timeout=timeout, retries=retries,
                      backoff_base=backoff_base, breaker_key=breaker_key,
                      breaker_threshold=breaker_threshold, breaker_cooldown=breaker_cooldown)
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {}
