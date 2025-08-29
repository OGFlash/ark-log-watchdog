# ocr.py — Tesseract backend with robust TSV parsing and line grouping
import os
import sys
import cv2
import numpy as np
from typing import Dict, List, Tuple, Optional
import pytesseract
from pytesseract import Output

# ────────────────────────────────────────────────────────────────────────────────
# Tesseract runtime setup (works for dev and one-file EXE)
# ────────────────────────────────────────────────────────────────────────────────

_TESS_INIT = False
_TESS_PATH = None
_TESSDATA_DIR = None

def _norm(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    # strip any accidental surrounding quotes and trailing slashes
    p = p.strip().strip('"').strip("'")
    return os.path.normpath(p)

def _portable_root() -> str:
    # Where we install the portable Tesseract via Inno:
    # {userappdata}\ArkWatchdog\Tesseract-OCR
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "ArkWatchdog", "Tesseract-OCR")

def _guess_tesseract_from_cfg(cfg: Dict) -> Tuple[Optional[str], Optional[str]]:
    # 1) explicit path from config.yaml
    exe = _norm(cfg.get("tesseract_cmd") or "")
    if exe and os.path.isfile(exe):
        td = _norm(os.path.join(os.path.dirname(exe), "tessdata"))
        if os.path.isdir(td):
            return exe, td

    # 2) portable next to user data (what installer adds)
    root = _portable_root()
    exe = os.path.join(root, "tesseract.exe")
    td = os.path.join(root, "tessdata")
    if os.path.isfile(exe) and os.path.isdir(td):
        return exe, td

    # 3) system default install (common)
    sys_root = r"C:\Program Files\Tesseract-OCR"
    exe = os.path.join(sys_root, "tesseract.exe")
    td  = os.path.join(sys_root, "tessdata")
    if os.path.isfile(exe) and os.path.isdir(td):
        return exe, td

    # 4) last resort: leave None; pytesseract will try PATH
    return None, None

def set_tesseract_runtime(tesseract_cmd: Optional[str], tessdata_dir: Optional[str]) -> None:
    """
    Configure pytesseract and environment. No quotes in env vars!
    """
    global _TESS_INIT, _TESS_PATH, _TESSDATA_DIR
    tesseract_cmd = _norm(tesseract_cmd)
    tessdata_dir  = _norm(tessdata_dir)

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        _TESS_PATH = tesseract_cmd

    if tessdata_dir:
        # Tesseract expects TESSDATA_PREFIX to point directly at the folder
        # that contains *.traineddata (i.e., the "tessdata" directory).
        os.environ["TESSDATA_PREFIX"] = tessdata_dir
        _TESSDATA_DIR = tessdata_dir

    _TESS_INIT = True

def set_tesseract_cmd(path: str) -> None:
    """
    Back-compat shim for older watcher code.
    """
    set_tesseract_runtime(path, None)

def ensure_tesseract_ready(cfg: Dict) -> None:
    """
    Idempotent: resolve & set tesseract.exe and TESSDATA_PREFIX once.
    """
    global _TESS_INIT
    if _TESS_INIT:
        return
    exe, td = _guess_tesseract_from_cfg(cfg)
    set_tesseract_runtime(exe, td)

# ────────────────────────────────────────────────────────────────────────────────
# OCR helpers
# ────────────────────────────────────────────────────────────────────────────────

def _scale_for_ocr(bgr: np.ndarray, cfg: Dict) -> np.ndarray:
    scale = float(cfg.get("ocr_scale", 2.0))
    if scale and scale != 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return bgr

def _preprocess_gray(bgr: np.ndarray) -> np.ndarray:
    """Light enhancement to help Tesseract on UI text."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return gray

def _tess_config(cfg: Dict, psm: int = 6) -> str:
    # Note: NO quotes anywhere here.
    ocr_cfg = f"--psm {psm} --oem 1 -c preserve_interword_spaces=1"
    wl = (cfg.get("tesseract_whitelist") or "").strip()
    if wl:
        wl_safe = wl.replace('"', "")
        ocr_cfg += f' -c tessedit_char_whitelist={wl_safe}'
    # If we’ve set TESSDATA_PREFIX, Tesseract will find language files. No need to pass -l here.
    return ocr_cfg

def _lines_from_tsv(tsv: Dict, min_word_conf: int) -> List[Dict]:
    """Group TSV words into text lines using page/block/par/line indices."""
    n = len(tsv["text"])
    words = []
    for i in range(n):
        txt = (tsv["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = int(float(tsv["conf"][i]))
        except Exception:
            conf = -1
        if conf < min_word_conf:
            continue
        x, y, w, h = tsv["left"][i], tsv["top"][i], tsv["width"][i], tsv["height"][i]
        words.append({
            "page": tsv["page_num"][i],
            "block": tsv["block_num"][i],
            "par": tsv["par_num"][i],
            "line": tsv["line_num"][i],
            "word": tsv["word_num"][i],
            "text": txt,
            "conf": conf,
            "bbox": (int(x), int(y), int(w), int(h)),
        })
    if not words:
        return []

    # Group by (page, block, par, line)
    groups = {}
    for w in words:
        key = (w["page"], w["block"], w["par"], w["line"])
        groups.setdefault(key, []).append(w)

    lines: List[Dict] = []
    for key, arr in groups.items():
        arr.sort(key=lambda z: z["word"])
        text = " ".join(a["text"] for a in arr)
        confs = [a["conf"] for a in arr if a["conf"] >= 0]
        conf = float(np.median(confs)) if confs else 0.0
        xs = [a["bbox"][0] for a in arr]
        ys = [a["bbox"][1] for a in arr]
        xe = [a["bbox"][0] + a["bbox"][2] for a in arr]
        ye = [a["bbox"][1] + a["bbox"][3] for a in arr]
        x0, y0, x1, y1 = min(xs), min(ys), max(xe), max(ye)
        lines.append({
            "text": text,
            "conf": conf,
            "bbox": (int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0))),
        })

    lines.sort(key=lambda z: (z["bbox"][1], z["bbox"][0]))
    return lines

def ocr_lines(bgr_roi: np.ndarray, cfg: Dict):
    """
    Return OCR lines as: [{"text","conf","bbox"(x,y,w,h)}, ...] and the scaled BGR image used.
    Uses PSM 6 (block of text) and TSV to build reliable per-line boxes.
    """
    ensure_tesseract_ready(cfg)  # <-- make sure runtime is configured
    min_word_conf = int(cfg.get("min_word_conf", 0))
    img = _scale_for_ocr(bgr_roi, cfg)
    gray = _preprocess_gray(img)
    tcfg = _tess_config(cfg, psm=int(cfg.get("psm_lines", 6)))
    data = pytesseract.image_to_data(gray, config=tcfg, output_type=Output.DICT)
    lines = _lines_from_tsv(data, min_word_conf=min_word_conf)
    return lines, img

def crop_to_text_columns(bgr_scaled: np.ndarray, bbox: Tuple[int,int,int,int], cfg: Dict) -> Tuple[int,int,int,int]:
    """
    Optional: tighten horizontally to columns that actually contain ink, to avoid UI noise.
    """
    x, y, w, h = bbox
    roi = bgr_scaled[max(0, y):y + h, max(0, x):x + w]
    if roi.size == 0:
        return bbox
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
    blur = cv2.GaussianBlur(gray, (3,3), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 1))
    mask = cv2.dilate(mask, ker, iterations=1)
    cols = (mask > 0).any(axis=0)
    xs = np.where(cols)[0]
    if xs.size == 0:
        return bbox
    x0_local = int(xs.min()); x1_local = int(xs.max() + 1)
    pad_lr = int(cfg.get("entry_bbox_pad_lr", 4))
    x0_local = max(0, x0_local - pad_lr)
    x1_local = min(mask.shape[1], x1_local + pad_lr)
    x0_new = x + x0_local
    w_new = max(1, (x1_local - x0_local))
    return (x0_new, y, w_new, h)

def ocr_entry_fulltext(bgr_scaled: np.ndarray, bbox: Tuple[int,int,int,int], cfg: Dict) -> Tuple[str, float]:
    """
    Re-OCR a vertical slice (optionally column-tightened) and return concatenated text + median conf.
    We keep *all* words — no tail trimming — to show the full entry.
    """
    ensure_tesseract_ready(cfg)  # <-- ensure configured here too
    tight = crop_to_text_columns(bgr_scaled, bbox, cfg) if bool(cfg.get("tighten_columns", True)) else bbox
    x, y, w, h = tight
    roi = bgr_scaled[max(0, y):y + h, max(0, x):x + w]
    if roi.size == 0:
        return "", 0.0
    gray = _preprocess_gray(roi)
    tcfg = _tess_config(cfg, psm=int(cfg.get("reocr_psm", 6)))  # block of text
    data = pytesseract.image_to_data(gray, config=tcfg, output_type=Output.DICT)
    parts = _lines_from_tsv(data, min_word_conf=int(cfg.get("min_word_conf", 0)))
    if not parts:
        return "", 0.0
    parts.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    text = " ".join([p["text"] for p in parts]).strip()
    median_conf = float(np.median([p["conf"] for p in parts]))
    return text, median_conf
