# license_client.py
# Drop-in client that caches the signed token under %APPDATA%\ArkWatchdog\license_cache.json
# and supports activate(), require_valid(), get_cached_claims().

import os
import json
import time
import platform
import hashlib
from pathlib import Path
from typing import Tuple, Optional, Dict

import requests

try:
    import jwt  # PyJWT
except Exception as e:
    jwt = None

# ────────────────────────────────────────────────────────────────────────────────
# CONFIG — set API_BASE and PUBLIC_PEM (keep your existing PUBLIC_PEM!)
# ────────────────────────────────────────────────────────────────────────────────

API_BASE = os.environ.get("LW_API_BASE", "https://api.license-arkwatchdog.com").rstrip("/")

# ======= PASTE YOUR SERVER PUBLIC KEY HERE =======
# Paste your *public* RSA key (from public.pem) here:
PUBLIC_KEY_PEM = b""""""
# ================================================

APP_NAME = "ark-watchdog"
TOKEN_LEEWAY = 60  # seconds of clock skew allowed

# ────────────────────────────────────────────────────────────────────────────────
# Cache file location: %APPDATA%\ArkWatchdog\license_cache.json (Windows)
#                      ~/.ark_watchdog/ArkWatchdog/license_cache.json (others)
# ────────────────────────────────────────────────────────────────────────────────

def _cache_path() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / ".ark_watchdog")
    cache_dir = Path(base) / "ArkWatchdog"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "license_cache.json"

def _read_cache() -> Dict:
    p = _cache_path()
    if p.exists():
        try:
            return json.load(open(p, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _write_cache(data: Dict) -> None:
    p = _cache_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ────────────────────────────────────────────────────────────────────────────────
# Fingerprint (Windows: MachineGuid; fallback to system tuple)
# ────────────────────────────────────────────────────────────────────────────────

def _win_machine_guid() -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(guid)
    except Exception:
        return None

def machine_fingerprint() -> str:
    guid = None
    if platform.system().lower().startswith("win"):
        guid = _win_machine_guid()
    node = platform.node()
    sysname = platform.system()
    release = platform.release()
    arch = platform.machine()
    src = "|".join([guid or "", node, sysname, release, arch])
    return hashlib.sha256(src.encode("utf-8", "ignore")).hexdigest()

# ────────────────────────────────────────────────────────────────────────────────
# JWT helpers
# ────────────────────────────────────────────────────────────────────────────────

def _decode_token(token: str) -> Tuple[bool, str, Optional[Dict]]:
    if not jwt:
        return False, "pyjwt not installed", None
    try:
        claims = jwt.decode(
            token,
            PUBLIC_PEM,
            algorithms=["RS256"],
            options={"require": ["exp", "iat"]},
            leeway=TOKEN_LEEWAY,
        )
        return True, "ok", claims
    except Exception as e:
        return False, f"token decode failed: {e}", None

def _claims_valid_for_this_machine(claims: Dict) -> Tuple[bool, str]:
    if not claims:
        return False, "no claims"
    if claims.get("app") != APP_NAME:
        return False, f"token for different app: {claims.get('app')}"
    # exp checked by jwt.decode; also check not in the past with leeway already applied
    fp = claims.get("fp")
    if not fp:
        return False, "token missing fingerprint"
    if fp != machine_fingerprint():
        return False, "fingerprint mismatch"
    active = claims.get("active", True)
    if not active:
        return False, "license inactive"
    return True, "ok"

# ────────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────────

def get_cached_claims() -> Optional[Dict]:
    """Return cached claims if token present and decodable (even if expired)."""
    cache = _read_cache()
    tok = cache.get("token")
    if not tok:
        return None
    ok, _, claims = _decode_token(tok)
    return claims if ok and claims else cache.get("claims")

def activate(license_key: str, timeout: int = 15) -> Tuple[bool, str]:
    """Activate online with entered license key; cache token+claims+key on success."""
    if not license_key:
        return False, "no license key entered"
    fp = machine_fingerprint()
    try:
        resp = requests.post(
            f"{API_BASE}/activate",
            json={"key": license_key, "fingerprint": fp, "app": APP_NAME, "version": 1},
            timeout=timeout,
        )
    except Exception as e:
        return False, f"network error: {e}"

    if resp.status_code != 200:
        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text}
        return False, f"server {resp.status_code}: {data}"

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    token = data.get("token")
    claims = data.get("claims") or {}

    if not token:
        return False, "server did not return token"

    ok, msg, dec = _decode_token(token)
    if not ok:
        return False, f"bad token: {msg}"
    ok2, msg2 = _claims_valid_for_this_machine(dec)
    if not ok2:
        return False, msg2

    cache = _read_cache()
    cache.update({
        "token": token,
        "claims": dec,         # store decoded claims (authoritative)
        "license_key": license_key,
        "last_verified_unix": int(time.time()),
    })
    _write_cache(cache)
    return True, "activation ok"

def require_valid(allow_online: bool = False, license_key: Optional[str] = None, timeout: int = 12) -> Tuple[bool, str]:
    """
    Validate cached token. If invalid/expired and allow_online=True, attempt online
    activation/refresh using provided license_key or cached license_key.
    """
    cache = _read_cache()
    tok = cache.get("token")
    if tok:
        ok, msg, claims = _decode_token(tok)
        if ok and claims:
            ok2, msg2 = _claims_valid_for_this_machine(claims)
            if ok2:
                # still valid
                return True, "cached token valid"
            # else fall through to possible online refresh

    if not allow_online:
        return False, "no valid token cached (offline)"

    # Try online with provided or cached key
    key = (license_key or cache.get("license_key") or "").strip()
    if not key:
        return False, "no license key available for online check"
    return activate(key, timeout=timeout)
