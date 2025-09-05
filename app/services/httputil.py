# app/services/httputil.py
import time, random, urllib.request, urllib.error, json
from typing import Dict, Optional
from ..obs.metrics import observe, inc

class CircuitOpenError(Exception):
    pass

_breakers: Dict[str, dict] = {}

def get_breaker(name: str) -> dict:
    b = _breakers.get(name)
    if not b:
        b = _breakers[name] = {"opened_at": 0.0, "failures": 0, "state": "closed"}
    return b

def breaker_is_open(name: str, *, recovery_timeout: float) -> bool:
    b = get_breaker(name)
    if b["state"] == "open":
        if (time.time() - b["opened_at"]) >= recovery_timeout:
            # half-open probe allowed
            b["state"] = "half"
            return False
        return True
    return False

def breaker_record_success(name: str):
    b = get_breaker(name)
    b["failures"] = 0
    b["state"] = "closed"

def breaker_record_failure(name: str, *, threshold: int, recovery_timeout: float):
    b = get_breaker(name)
    b["failures"] += 1
    if b["failures"] >= threshold:
        b["state"] = "open"
        b["opened_at"] = time.time()

def breaker_reset(name: str):
    b = get_breaker(name)
    b["failures"] = 0
    b["state"] = "closed"
    b["opened_at"] = 0.0

def _record_latency(url: str, t0: float):
    observe("vendor.http.latency_ms", (time.time()-t0)*1000.0, {"url": url.split("?",1)[0]})

def _record_error(url: str):
    observe("vendor.http.errors", 1, {"url": url.split("?",1)[0]})

def http_bytes(url: str, *, data: bytes, headers: Dict[str,str], method: str = "POST",
               timeout: float = 30.0, retries: int = 2, backoff_base: float = 0.2,
               breaker_key: Optional[str] = None, breaker_threshold: int = 3, breaker_cooldown: float = 10.0) -> bytes:
    """HTTP request with retry/backoff and optional circuit breaker. Returns raw bytes."""
    if breaker_key and breaker_is_open(breaker_key, recovery_timeout=breaker_cooldown):
        inc("vendor.cb.short_circuit", {"key": breaker_key})
        raise CircuitOpenError(f"circuit open for {breaker_key}")

    last_err = None
    attempts = 0
    while attempts <= retries:
        attempts += 1
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=data, method=method)
            for k,v in (headers or {}).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            _record_latency(url, t0)
            if breaker_key:
                breaker_record_success(breaker_key)
            return body
        except Exception as e:
            last_err = e
            _record_error(url)
            if breaker_key:
                breaker_record_failure(breaker_key, threshold=breaker_threshold, recovery_timeout=breaker_cooldown)
            if attempts > retries:
                break
            # backoff with jitter
            sleep_s = backoff_base * (2 ** (attempts-1)) * (0.5 + random.random())
            time.sleep(min(2.0, sleep_s))
    inc("vendor.http.fail", {"url": url.split("?",1)[0]})
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
