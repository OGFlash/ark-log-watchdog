# bundled_tesseract.py
import os, sys

def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _candidates():
    base = _app_dir()
    # Installed path (Inno Setup target)
    yield os.path.join(base, "Tesseract-OCR", "tesseract.exe")
    # Dev path you mentioned
    yield os.path.join(base, "third_party", "Tesseract-OCR", "tesseract.exe")

def use_bundled_tesseract(cfg: dict | None = None) -> tuple[str | None, str | None]:
    """
    Detect a bundled tesseract.exe and its tessdata dir.
    - Sets pytesseract path when available
    - Sets env so child processes inherit
    - Writes cfg["tesseract_cmd"] (exe) and cfg["tessdata_dir"] (dir with *.traineddata)
    Returns (exe_path or None, tessdata_dir or None)
    """
    exe = None
    td = None
    for p in _candidates():
        if os.path.exists(p):
            exe = p
            # prefer ...\Tesseract-OCR\tessdata
            cand = os.path.join(os.path.dirname(p), "tessdata")
            td = cand if os.path.isdir(cand) else None
            break

    if exe:
        # Make it work for current process
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = exe
        except Exception:
            pass

        # Env for children (watcher)
        if td:
            # Many Windows builds expect TESSDATA_PREFIX to point DIRECTLY to folder with *.traineddata
            os.environ["TESSDATA_PREFIX"] = td
        # Put exe dir on PATH (helps native tesseract subprocess lookups)
        exe_dir = os.path.dirname(exe)
        os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")

        if cfg is not None:
            cfg["tesseract_cmd"] = exe
            if td:
                cfg["tessdata_dir"] = td
    return exe, td
