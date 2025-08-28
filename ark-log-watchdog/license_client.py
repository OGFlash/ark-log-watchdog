"""
Client-side licensing for ARK Watchdog.

- Canonical machine id = first 16 hex of SHA256( lower(hostname) + '|' + lower(mac) )
- Can validate an already-cached token (offline), or activate online to fetch one.
- Exposes the legacy helpers your GUI expects:
    - require_valid(allow_online=False, license_key=None, timeout=20) -> (ok: bool, msg: str)
    - activate(license_key: str, timeout=20) -> (ok: bool, msg: str)
    - get_cached_claims() -> dict | None
    - clear_cached_token() -> None

Replace PUBLIC_KEY_PEM with your server's PUBLIC key.
"""

from __future__ import annotations
import os, json, time, hashlib, platform, uuid
from typing import Optional, Tuple, Dict, Any

import requests
import jwt
from jwt import ExpiredSignatureError, InvalidSignatureError, InvalidAudienceError, DecodeError

# ----------------- CONFIG -----------------
API_BASE = "https://api.license-arkwatchdog.com"
APP_ID   = "ark-watchdog"

# ======= PASTE YOUR SERVER PUBLIC KEY HERE =======
# Paste your *public* RSA key (from public.pem) here:
PUBLIC_KEY_PEM = b""""""
# ================================================

# ----------------- MACHINE FP (canonical 16) -----------------
def machine_fingerprint() -> str:
    name = (platform.node() or "").strip().lower()
    mac  = f"{uuid.getnode():012x}".lower()
    h = hashlib.sha256(f"{name}|{mac}".encode("utf-8")).hexdigest()
    return h[:16]

# ----------------- TOKEN CACHE -----------------
def _cache_dir() -> str:
    # Windows-friendly local app data; fallback to home folder
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ArkWatchdog")
    os.makedirs(d, exist_ok=True)
    return d

def _token_path() -> str:
    return os.path.join(_cache_dir(), "license_token.json")

def save_cached_token(token: str) -> None:
    data = {"token": token, "saved_at": int(time.time())}
    tmp = _token_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _token_path())

def load_cached_token() -> Optional[str]:
    p = _token_path()
    if not os.path.exists(p):
        return None
    try:
        data = json.load(open(p, "r", encoding="utf-8"))
        return data.get("token")
    except Exception:
        return None

def clear_cached_token() -> None:
    try:
        os.remove(_token_path())
    except FileNotFoundError:
        pass

def get_cached_claims() -> Optional[Dict[str, Any]]:
    tok = load_cached_token()
    if not tok:
        return None
    try:
        # decode without verify just to show details
        return jwt.decode(tok, options={"verify_signature": False})
    except Exception:
        return None

# ----------------- CORE VERIFY / ACTIVATE -----------------
def _verify_token_strict(token: str) -> Dict[str, Any]:
    """Verify signature, audience, time; then enforce canonical machine match."""
    claims = jwt.decode(
        token,
        PUBLIC_KEY_PEM,
        algorithms=["RS256"],
        audience=APP_ID,
        options={"require": ["exp", "aud", "nbf", "iat"]},
    )
    local_m = machine_fingerprint()
    token_m = ((claims.get("machine") or "")).lower()[:16]
    if token_m != local_m:
        raise RuntimeError(f"server token mismatch: token.machine={token_m} local.machine={local_m}")
    if int(claims["exp"]) < int(time.time()):
        raise ExpiredSignatureError("expired")
    return claims

def activate_and_get_token(license_key: str, timeout: int = 20) -> str:
    m = machine_fingerprint()
    r = requests.post(
        f"{API_BASE}/api/activate",
        json={"key": license_key, "machine": m, "app": APP_ID},
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"activate failed {r.status_code}: {r.text}")
    return r.json()["token"]

# ----------------- LEGACY HELPERS (GUI expects these) -----------------
def require_valid(allow_online: bool = False,
                  license_key: Optional[str] = None,
                  timeout: int = 20) -> Tuple[bool, str]:
    """
    Check cached token first. If invalid and allow_online=True + license_key provided,
    try to activate and cache a fresh token.
    """
    # 1) try cached token
    tok = load_cached_token()
    if tok:
        try:
            claims = _verify_token_strict(tok)
            exp_human = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(claims["exp"])))
            return True, f"VALID — plan={claims.get('plan','?')} expires={exp_human}"
        except (ExpiredSignatureError,) as e:
            # fall through to online if allowed
            if not allow_online:
                return False, "Token expired. Re-activate required."
        except (InvalidSignatureError, InvalidAudienceError, DecodeError, RuntimeError) as e:
            if not allow_online:
                return False, f"Invalid token: {e}"

    # 2) go online if allowed
    if allow_online and license_key:
        try:
            tok = activate_and_get_token(license_key, timeout=timeout)
            save_cached_token(tok)
            _ = _verify_token_strict(tok)  # sanity
            return True, "Activated and valid."
        except Exception as e:
            return False, f"Activation failed: {e}"

    # 3) no cached valid token and didn't (or couldn't) go online
    return False, "No valid license token found."

def activate(license_key: str, timeout: int = 20) -> Tuple[bool, str]:
    """
    Online-only explicit activation. Saves token if success.
    """
    try:
        tok = activate_and_get_token(license_key, timeout=timeout)
        save_cached_token(tok)
        claims = _verify_token_strict(tok)
        exp_human = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(claims["exp"])))
        return True, f"Activated — plan={claims.get('plan','?')} expires={exp_human}"
    except Exception as e:
        return False, f"Activation failed: {e}"
