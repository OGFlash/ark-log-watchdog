# watcher.py — sticky dedupe; full-entry capture; trigger-based mentions; license enforced
import os
import re
import time
from typing import List, Dict, Tuple, Optional

import cv2
import numpy as np
from mss import mss

from utils import load_config, load_keywords
from ocr import set_tesseract_cmd, ocr_lines, ocr_entry_fulltext
from line_detector import build_regexes, match_line
from discord_notifier import send_to_discord

import license_client

# ────────────────────────────────────────────────────────────────────────────────
# Canonical header key from text
# ────────────────────────────────────────────────────────────────────────────────

_CANON_RE = re.compile(r"(?i)day\s*(\d{1,6})\s*[,;]\s*(\d{1,2})[:;](\d{2})[:;](\d{2})")

def header_key_from_text(text: str) -> Optional[str]:
    if not text: return None
    m = _CANON_RE.search(text)
    if not m: return None
    day, hh, mm, ss = m.group(1, 2, 3, 4)
    try:
        return f"d{int(day)}-t{int(hh):02d}{int(mm):02d}{int(ss):02d}"
    except Exception:
        return None

def header_key_from_line(line_text: str) -> str:
    s = (line_text or "").lower()
    s = re.sub(r"[^a-z0-9:;]", "", s)[:64]
    return s or "nokey"

# ────────────────────────────────────────────────────────────────────────────────

def crop_roi(img_bgr: np.ndarray, roi: Dict) -> np.ndarray:
    H, W = img_bgr.shape[:2]
    x, y, w, h = [int(roi[k]) for k in ("x","y","w","h")]
    x = max(0, min(W - 1, x)); y = max(0, min(H - 1, y))
    w = max(1, min(W - x, w)); h = max(1, min(H - y, h))
    return img_bgr[y:y+h, x:x+w]

def compile_header_regex(cfg: Dict) -> re.Pattern:
    pat = cfg.get("entry_header_regex", r"(?i)\bday\s*\d{1,6}\s*,\s*\d{1,2}[:;]\d{2}[:;]\d{2}\s*[:;]?")
    try:
        return re.compile(pat)
    except re.error:
        return re.compile(r"(?i)\bday\b")

def parse_entries_from_lines(lines: List[Dict], img_h: int, img_w: int, cfg: Dict) -> List[Dict]:
    hdr_re = compile_header_regex(cfg)
    all_lines = sorted(lines, key=lambda z: (z["bbox"][1], z["bbox"][0]))
    hdr_idxs = [i for i, ln in enumerate(all_lines) if hdr_re.search(ln["text"] or "")]
    if not hdr_idxs: return []

    pad_lr = int(cfg.get("entry_bbox_pad_lr", 4))
    pad_v  = int(cfg.get("entry_bbox_pad_v", 0))
    cap_h  = int(cfg.get("entry_max_height_px", 360))

    entries: List[Dict] = []
    for h_i, idx in enumerate(hdr_idxs):
        hy = all_lines[idx]["bbox"][1]
        next_y = all_lines[hdr_idxs[h_i + 1]]["bbox"][1] if (h_i + 1) < len(hdr_idxs) else img_h
        y0 = max(0, hy - pad_v)
        y1 = min(img_h, min(next_y, hy + cap_h) + pad_v)
        x0, x1 = 0 + pad_lr, max(1, img_w - pad_lr)
        bbox = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        entries.append({
            "bbox": bbox,
            "header_text": all_lines[idx]["text"],
            "header_bbox": all_lines[idx]["bbox"],
        })
    return entries

# ────────────────────────────────────────────────────────────────────────────────
# Trigger selection
# ────────────────────────────────────────────────────────────────────────────────

def choose_trigger(text: str, cfg: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Return (trigger, reason). First matching trigger in cfg['triggers'] wins.
    Supports 'keyword' and 'regex'.
    """
    triggers = cfg.get("triggers") or []
    lower = (text or "").lower()
    for t in triggers:
        ttype = (t.get("type") or "keyword").lower()
        pat   = t.get("match") or ""
        if not pat: continue
        if ttype == "regex":
            try:
                if re.search(pat, text or "", flags=re.IGNORECASE):
                    return t, f"regex:{pat}"
            except re.error:
                continue
        else:
            if pat.lower() in lower:
                return t, f"kw:{pat}"
    # legacy fallback (keywords/regex_patterns)
    kws  = list(load_keywords(cfg))
    regs = build_regexes(cfg)
    ok, why = match_line(text or "", kws, regs)
    return (None, None) if not ok else ({"name":"Legacy", "mention_mode":"none", "prefix":"","suffix":""}, why)

def build_mention(trigger: Optional[Dict]) -> str:
    if not trigger: return ""
    mode = (trigger.get("mention_mode") or "none").lower()
    if mode in ("@here","@everyone"): return mode
    if mode == "custom": return (trigger.get("mention_custom","") or "").strip()
    return ""

def build_allowed_mentions(cfg: Dict) -> Dict:
    am = cfg.get("discord_allowed_mentions", {}) or {}
    payload = {"parse": []}
    if am.get("everyone"): payload["parse"].append("everyone")
    if am.get("roles"):    payload["parse"].append("roles")
    if am.get("users"):    payload["parse"].append("users")
    if am.get("role_ids"): payload["roles"] = am.get("role_ids")
    if am.get("user_ids"): payload["users"] = am.get("user_ids")
    return payload

# ────────────────────────────────────────────────────────────────────────────────

def main():
    # Enforce license (cannot bypass GUI)
    ok, msg = license_client.require_valid(allow_online=True)
    if not ok:
        raise SystemExit(f"[LICENSE] Not valid: {msg}")

    cfg = load_config()
    tess_path = (cfg.get("tesseract_cmd") or "").strip()
    if tess_path:
        set_tesseract_cmd(tess_path)

    roi = cfg.get("roi") or {}
    if not roi or roi.get("w", 0) < 5 or roi.get("h", 0) < 5:
        raise SystemExit("ROI not set. Run GUI → Select ROI (drag).")

    interval_ms      = int(cfg.get("capture_interval_ms", 750))
    send_only_newest = bool(cfg.get("send_only_newest", True))
    hdr_re           = compile_header_regex(cfg)
    allowed_mentions = build_allowed_mentions(cfg)
    webhook_url      = (cfg.get("discord_webhook_url") or "").strip()

    posted_header_keys: set[str] = set()

    print(f"[INFO] Watching ROI {roi} every {interval_ms} ms; triggers={len(cfg.get('triggers', []))}", flush=True)

    frame_id = 0
    with mss() as sct:
        monitor = sct.monitors[0]
        while True:
            t0 = time.time()
            screen = np.array(sct.grab(monitor))[:, :, :3]
            roi_img = crop_roi(screen, roi)

            lines, scaled_bgr = ocr_lines(roi_img, cfg)
            img_h, img_w = scaled_bgr.shape[:2]
            raw_preview = [ln["text"] for ln in lines[:5]]
            print(f"[DBG] frame {frame_id} | ocr_lines={len(lines)} | sample={raw_preview}", flush=True)

            entries = parse_entries_from_lines(lines, img_h, img_w, cfg)
            entries.sort(key=lambda e: e["header_bbox"][1])
            print(f"[DBG] headers_found={len(entries)} | top={[e['header_text'] for e in entries[:3]]}", flush=True)

            if send_only_newest and entries:
                entries = entries[:1]

            for ent in entries:
                text, conf2 = ocr_entry_fulltext(scaled_bgr, ent["bbox"], cfg)
                if not text:
                    continue
                if not hdr_re.search(text or ""):
                    hdr = ent["header_text"].strip()
                    if hdr and hdr.lower().startswith("day"):
                        text = f"{hdr} {text}"
                    else:
                        continue

                key = header_key_from_text(text) or header_key_from_line(ent["header_text"])
                if key in posted_header_keys:
                    print(f"[DBG] skip duplicate header key={key}", flush=True)
                    continue

                trig, why = choose_trigger(text, cfg)
                if not trig:
                    continue

                posted_header_keys.add(key)

                mention_text = build_mention(trig)
                prefix = (trig.get("prefix","") or "").strip()
                suffix = (trig.get("suffix","") or "").strip()

                parts = []
                if mention_text: parts.append(mention_text)
                if prefix: parts.append(prefix)
                parts.append("**ARK Watchdog match**")
                parts.append(f"- [{int(conf2)}%] {text} (match: {why.split(':',1)[-1] if why else 'trigger'})")
                if suffix: parts.append(suffix)
                content = "\n".join(parts)

                ok_enc, buf = cv2.imencode(".png", roi_img)
                png_bytes = buf.tobytes() if ok_enc else None
                send_to_discord(content, png_bytes, filename="ark_log_hit.png",
                                allowed_mentions=allowed_mentions, webhook_url=webhook_url)
                print("[OK] Posted to Discord.", flush=True)

                # local save (optional)
                try:
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    os.makedirs("captures", exist_ok=True)
                    cv2.imwrite(os.path.join("captures", f"hit-{ts}.png"), roi_img)
                except Exception:
                    pass

            frame_id += 1
            time.sleep(max(0, interval_ms / 1000.0 - (time.time() - t0)))

if __name__ == "__main__":
    main()
