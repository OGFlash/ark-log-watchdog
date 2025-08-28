# discord_notifier.py
import os
import json
from typing import Optional, Dict, Any

import requests


def send_to_discord(
    content: str,
    image_bytes: Optional[bytes] = None,
    filename: str = "image.png",
    allowed_mentions: Optional[Dict[str, Any]] = None,
    webhook_url: Optional[str] = None,
) -> None:
    """
    Post to a Discord webhook.

    - content: message text (may include @here/@everyone/<@&ROLEID>/<@USERID>)
    - image_bytes: optional PNG bytes to attach
    - filename: name for the uploaded image file
    - allowed_mentions: Discord 'allowed_mentions' payload; e.g.
        {"parse":["everyone","roles"],"roles":["123"],"users":["456"]}
    - webhook_url: override URL (else uses DISCORD_WEBHOOK_URL env)

    Raises requests.HTTPError on non-2xx.
    """
    url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        raise RuntimeError("Discord webhook URL not set (config or DISCORD_WEBHOOK_URL).")

    payload: Dict[str, Any] = {"content": content}
    if allowed_mentions:
        payload["allowed_mentions"] = allowed_mentions

    if image_bytes:
        # multipart/form-data: one file part + one JSON payload part
        files = {"file": (filename, image_bytes, "image/png")}
        data = {"payload_json": json.dumps(payload)}
        resp = requests.post(url, data=data, files=files, timeout=15)
    else:
        # simple application/json
        resp = requests.post(url, json=payload, timeout=15)

    resp.raise_for_status()
