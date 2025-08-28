"""
Client-side licensing for ARK Watchdog.

- Computes a canonical machine fingerprint: 16-char lowercase hex of SHA256(hostname|mac)
- Activates against your server to receive a JWT
- Verifies JWT signature (RS256), audience, exp
- Compares canonical machine id from token vs local

Replace PUBLIC_KEY_PEM with your server's PUBLIC key (PEM).
"""

import time
import platform, uuid, hashlib
import requests
import jwt
from jwt import ExpiredSignatureError, InvalidAudienceError, InvalidSignatureError

API_BASE = "https://api.license-arkwatchdog.com"
APP_ID   = "ark-watchdog"

# ======= PASTE YOUR SERVER PUBLIC KEY HERE =======
# Paste your *public* RSA key (from public.pem) here:
PUBLIC_KEY_PEM = b""""""
# ================================================

def machine_fingerprint() -> str:
    """Canonical machine id: 16-char lowercase hex."""
    name = (platform.node() or "").strip().lower()
    mac  = f"{uuid.getnode():012x}".lower()
    h = hashlib.sha256(f"{name}|{mac}".encode("utf-8")).hexdigest()
    return h[:16]

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

def validate_token(token: str) -> dict:
    """
    Verify signature/audience/exp and ensure the token's machine matches the local canonical id.
    Raises a clear RuntimeError on machine mismatch.
    """
    # Verify signature + audience + time claims
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

# Optional: tiny CLI for quick testing
if __name__ == "__main__":
    import sys, json
    if len(sys.argv) == 2 and sys.argv[1] == "--fingerprint":
        print(json.dumps({"fingerprint": machine_fingerprint()}, indent=2))
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--activate":
        key = sys.argv[2]
        tok = activate_and_get_token(key)
        print("token:", tok[:32]+"...")  # preview
        print("claims:", jwt.decode(tok, options={"verify_signature": False}))
        sys.exit(0)
    print("Usage:")
    print("  python license_client.py --fingerprint")
    print("  python license_client.py --activate <LICENSE_KEY>")
