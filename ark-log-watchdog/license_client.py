# license_client.py
# Client for Ark Watchdog licensing.
# - Activates against /api/activate
# - Caches token & key in %APPDATA%\ArkWatchdog\license_cache.json (Windows)
#   or ~/.ark_watchdog/ArkWatchdog/license_cache.json (others)
# - Verifies RS256 JWT with public key, requires aud/app & machine match

import os
import json
import time
import uuid
import platform
import hashlib
from pathlib import Path
from typing import Tuple, Optional, Dict

import requests

try:
    import jwt  # PyJWT
except Exception:
    jwt = None  # we'll return a friendly error if missing

# ────────────────────────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────────────────────────

APP_NAME = "ark-watchdog"
API_BASE = os.environ.get("LW_API_BASE", "https://api.license-arkwatchdog.com").rstrip("/")

# Prefer public key from env (lets you update without touching the file)
_PUBLIC_ENV = os.environ.get("LW_PUBLIC_KEY_PEM")
if _PUBLIC_ENV and "\\n" in _PUBLIC_ENV and "-----BEGIN" in _PUBLIC_ENV:
    _PUBLIC_ENV = _PUBLIC_ENV.replace("\\n", "\n")

PUBLIC_KEY_PEM = _PUBLIC_ENV or """"""

TOKEN_LEEWAY = 60  # allow 60s of clock skew

# ────────────────────────────────────────────────────────────────────────────────
# Cache path helpers
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
# Machine ID (canonical 16-hex, same normalization as server)
# ────────────────────────────────────────────────────────────────────────────────

def _norm16_hex(s: str) -> str:
    s = (s or "").strip().lower()
    only = "".join(ch for ch in s if ch in "0123456789abcdef")
    if not only:
        return ""
    return only[:16].ljust(16, "0")  # ensure 16 if we got some hex at all

def _win_machine_guid() -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(guid)
    except Exception:
        return None

def machine_id() -> str:
    """
    Build a stable local ID → hash, then canonicalize to 16 hex.
    This stays consistent with earlier builds and the server's normalization.
    """
    src_parts = []

    if platform.system().lower().startswith("win"):
        guid = _win_machine_guid()
        if guid:
            src_parts.append(guid)

    # Use MAC as extra entropy if available
    try:
        mac = uuid.getnode()
        if mac and mac != uuid.getnode.__code__.co_consts[1]:  # best-effort non-random check
            src_parts.append(f"{mac:012x}")
    except Exception:
        pass

    # Fallbacks
    src_parts += [
        platform.node(),
        platform.system(),
        platform.release(),
        platform.machine(),
    ]

    src = "|".join(p for p in src_parts if p)
    digest = hashlib.sha256(src.encode("utf-8", "ignore")).hexdigest()
    return _norm16_hex(digest)

# ────────────────────────────────────────────────────────────────────────────────
# JWT helpers
# ────────────────────────────────────────────────────────────────────────────────

def _decode_token(token: str) -> Tuple[bool, str, Optional[Dict]]:
    if not jwt:
        return False, "pyjwt not installed", None
    try:
        # verify signature, audience, exp/iat
        claims = jwt.decode(
            token,
            PUBLIC_KEY_PEM,
            algorithms=["RS256"],
            audience=APP_NAME,
            options={"require": ["exp", "iat", "aud"]},
            leeway=TOKEN_LEEWAY,
        )
        return True, "ok", claims
    except Exception as e:
        return False, f"token decode failed: {e}", None

def _claims_valid_for_this_machine(claims: Dict) -> Tuple[bool, str]:
    if not claims:
        return False, "no claims"
    # server includes both 'aud' and 'app'
    if claims.get("aud") != APP_NAME:
        return False, f"aud mismatch: {claims.get('aud')}"
    if claims.get("app") and claims.get("app") != APP_NAME:
        return False, f"app mismatch: {claims.get('app')}"
    my_machine = machine_id()
    tok_machine = claims.get("machine") or _norm16_hex(claims.get("fp", ""))  # backward compat
    if not tok_machine:
        return False, "token missing machine"
    if tok_machine != my_machine:
        return False, "machine mismatch"
    return True, "ok"

# ────────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────────

def get_cached_claims() -> Optional[Dict]:
    """Return cached decoded claims if token present & decodes; else None."""
    cache = _read_cache()
    tok = cache.get("token")
    if not tok:
        return None
    ok, _, claims = _decode_token(tok)
    return claims if ok and claims else None

def activate(license_key: str, timeout: int = 15) -> Tuple[bool, str]:
    """Activate online with entered license key; cache token+key on success."""
    if not license_key:
        return False, "no license key entered"
    machine = machine_id()
    try:
        resp = requests.post(
            f"{API_BASE}/api/activate",
            json={"key": license_key, "app": APP_NAME, "machine": machine, "version": 1},
            timeout=timeout,
        )
    except Exception as e:
        return False, f"network error: {e}"

    if resp.status_code != 200:
        # try to surface structured detail
        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text}
        return False, f"{resp.status_code} at {resp.url} — {data}"

    data = {}
    try:
        data = resp.json()
    except Exception:
        pass

    token = data.get("token")
    if not token:
        return False, "server did not return token"

    ok, msg, claims = _decode_token(token)
    if not ok:
        return False, f"bad token: {msg}"

    ok2, msg2 = _claims_valid_for_this_machine(claims)
    if not ok2:
        return False, msg2

    cache = _read_cache()
    cache.update(
        {
            "token": token,
            "claims": claims,
            "license_key": license_key,
            "last_verified_unix": int(time.time()),
        }
    )
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
                return True, "cached token valid"
            # fall through → try online if allowed

    if not allow_online:
        return False, "no valid token cached (offline)"

    # Try activate with provided or cached key
    key = (license_key or cache.get("license_key") or "").strip()
    if not key:
        return False, "no license key available for online check"
    return activate(key, timeout=timeout)
