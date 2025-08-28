# bundled_tesseract.py
import os, sys

def _app_dir():
    # Folder of the running EXE (PyInstaller) or this .py file
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _candidates():
    base = _app_dir()
    # When installed by Inno:
    yield os.path.join(base, "Tesseract-OCR", "tesseract.exe")
    # While developing from source (your new folder):
    yield os.path.join(base, "third_party", "Tesseract-OCR", "tesseract.exe")
    # (fallback legacy name, just in case)
    yield os.path.join(base, "tesseract", "tesseract.exe")

def use_bundled_tesseract(cfg: dict | None = None) -> str | None:
    """
    If a bundled Tesseract exists, wire env + pytesseract to use it.
    Also writes cfg['tesseract_cmd'] so child processes can read it.
    Returns the resolved tesseract.exe path, or None if not found.
    """
    for exe in _candidates():
        if os.path.exists(exe):
            tdir = os.path.dirname(exe)            # ...\Tesseract-OCR
            os.environ.setdefault("TESSDATA_PREFIX", tdir)  # contains tessdata\
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = exe
            except Exception:
                pass
            if cfg is not None:
                cfg["tesseract_cmd"] = exe
            return exe
    return None
