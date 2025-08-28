# license_client.py
import os, json, time, uuid, hashlib, platform
from typing import Tuple, Optional, Dict, Any
import requests
import jwt  # PyJWT
from datetime import datetime, timezone

APP_ID = "ark-watchdog"

# POINT THIS TO YOUR LICENSE SERVER (no trailing slash)
API_BASE = "https://api.license-arkwatchdog.com"  # e.g. https://license.example.com

# Paste your *public* RSA key (from public.pem) here:
PUBLIC_KEY_PEM = b""""""

CACHE_PATH = os.path.join(os.path.dirname(__file__), "license_cache.json")


def _load_cache() -> Dict[str, Any]:
    if not os.path.exists(CACHE_PATH): return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(data: Dict[str, Any]) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _machine_fingerprint() -> str:
    # Stable-ish identifier (deterrent, not bulletproof)
    node = platform.node()
    sys = platform.system()
    rel = platform.release()
    proc = platform.processor()
    mac = uuid.getnode()
    raw = f"{node}|{sys}|{rel}|{proc}|{mac}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _verify_token(token: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    try:
        claims = jwt.decode(token, PUBLIC_KEY_PEM, algorithms=["RS256"], audience=APP_ID)
    except Exception as e:
        return False, f"invalid token: {e}", None
    fp = _machine_fingerprint()
    if claims.get("machine") != fp:
        return False, "token machine mismatch", None
    if claims.get("sub") != APP_ID:
        return False, "token subject mismatch", None
    # exp/nbf are validated by jwt.decode
    return True, "ok", claims

def check_local() -> Tuple[bool, str]:
    c = _load_cache()
    token = c.get("token")
    if not token:
        return False, "no token cached"
    ok, msg, _ = _verify_token(token)
    return ok, msg

def activate_and_store(license_key: str) -> Tuple[bool, str]:
    """
    Call your license server to activate a key for this machine.
    On success, caches {license_key, token} in license_cache.json.
    """
    if not license_key:
        return False, "empty license key"
    fp = _machine_fingerprint()
    url = f"{API_BASE}/api/activate"
    try:
        resp = requests.post(url, json={"key": license_key, "machine": fp, "app": APP_ID}, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token")
        if not token:
            return False, "server returned no token"
        ok, msg, claims = _verify_token(token)
        if not ok:
            return False, f"server token failed verify: {msg}"
        cache = {"license_key": license_key, "token": token, "activated_at": int(time.time())}
        _save_cache(cache)
        return True, f"activated; expires {datetime.fromtimestamp(claims['exp'], tz=timezone.utc).isoformat()}"
    except Exception as e:
        return False, f"activate failed: {e}"

def require_valid(allow_online: bool = True) -> Tuple[bool, str]:
    """
    Ensure there is a valid token. If local token is invalid/expired and
    allow_online=True, tries to refresh using the cached license_key.
    """
    ok, msg = check_local()
    if ok:
        return True, "valid (cached)"
    if not allow_online:
        return False, f"license not valid (cached): {msg}"
    cache = _load_cache()
    key = cache.get("license_key")
    if not key:
        return False, "no license key; please activate"
    return activate_and_store(key)
