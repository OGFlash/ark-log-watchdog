import os
import time
import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Deque, Tuple, Optional, Iterable
import yaml

@dataclass
class ROI:
    x: int
    y: int
    w: int
    h: int

class TTLSet:
    """A tiny TTL-based set for deduping events."""
    def __init__(self, ttl_seconds: int = 60, maxlen: int = 512):
        self.ttl = ttl_seconds
        self.maxlen = maxlen
        self._dq: Deque[Tuple[str, float]] = deque()  # (key, expires_at)

    def add(self, key: str):
        now = time.time()
        self._dq.append((key, now + self.ttl))
        while len(self._dq) > self.maxlen:
            self._dq.popleft()

    def __contains__(self, key: str) -> bool:
        now = time.time()
        # Drop expired
        while self._dq and self._dq[0][1] < now:
            self._dq.popleft()
        # Membership scan
        for k, exp in self._dq:
            if k == key and exp >= now:
                return True
        return False

def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path}. Copy from the repo and edit.")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data

def save_config(cfg: dict, path: str = "config.yaml"):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

def load_keywords(cfg: dict) -> Iterable[str]:
    kws = [k.strip() for k in cfg.get("keywords", []) if str(k).strip()]
    path = cfg.get("keywords_file", "")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and s not in kws:
                    kws.append(s)
    return kws

def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def make_event_key(text: str, bbox: Optional[Tuple[int,int,int,int]]=None, color: Optional[Tuple[int,int,int]]=None, text_only=True) -> str:
    if text_only or bbox is None:
        return sha1_text(text)
    return sha1_text(f"{text}|{bbox}|{color}")
