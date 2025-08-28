# gui.py  —  Single-EXE runtime: GUI spawns itself with --watcher
import os
import sys
import yaml
import json
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Dict, Any, Optional, List

import numpy as np
from mss import mss
from PIL import Image, ImageTk
import customtkinter as ctk
from bundled_tesseract import use_bundled_tesseract

import license_client

# ────────────────────────────────────────────────────────────────────────────────
# Try to import watcher so PyInstaller bundles it. (We still launch a child proc)
# ────────────────────────────────────────────────────────────────────────────────
try:
    import watcher as _watcher_module
    _HAS_WATCHER_MODULE = True
except Exception:
    _HAS_WATCHER_MODULE = False

# ────────────────────────────────────────────────────────────────────────────────
# Config I/O
# ────────────────────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.getcwd(), "config.yaml")

DEFAULT_CFG = {
    "roi": {"x": 1014, "y": 477, "w": 530, "h": 833},
    "discord_webhook_url": "",
    # Triggers (first match wins)
    "triggers": [
        {"name": "Killed", "type": "regex", "match": "(?i)killed", "mention_mode": "@here",
         "mention_custom": "", "prefix": "", "suffix": ""},
        {"name": "Destroyed", "type": "regex", "match": "(?i)destroyed", "mention_mode": "@here",
         "mention_custom": "", "prefix": "", "suffix": ""},
    ],
    # Allowed mentions for webhook posts
    "discord_allowed_mentions": {
        "everyone": True,   # enables @here/@everyone
        "roles": True,      # enables <@&ROLE_ID>
        "users": False,     # enables <@USER_ID>
        "role_ids": [],
        "user_ids": [],
    },
    # Legacy (still supported by watcher as fallback)
    "keywords": ["destroyed", "killed", "tribe", "tribemember", "raid", "offline", "froze"],
    "regex_patterns": [
        "(?i)killed|destroyed|froze|raid(ed)?|offline",
        "(?i)tribe(member)?",
    ],
    # OCR / watcher
    "tesseract_cmd": "",
    "ocr_scale": 2.0,
    "psm_lines": 6,
    "reocr_psm": 6,
    "min_word_conf": 0,
    "tesseract_whitelist": "",
    "tighten_columns": True,
    "entry_bbox_pad_lr": 4,
    "entry_bbox_pad_v": 0,
    "entry_max_height_px": 360,
    "capture_interval_ms": 750,
    "send_only_newest": True,
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return DEFAULT_CFG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg = DEFAULT_CFG.copy()
        for k, v in data.items():
            cfg[k] = v
        if "roi" not in cfg or not cfg["roi"]:
            cfg["roi"] = DEFAULT_CFG["roi"].copy()
        if "discord_allowed_mentions" not in cfg:
            cfg["discord_allowed_mentions"] = DEFAULT_CFG["discord_allowed_mentions"].copy()
        if "triggers" not in cfg:
            cfg["triggers"] = DEFAULT_CFG["triggers"].copy()
        return cfg
    except Exception as e:
        messagebox.showerror("Config error", f"Failed to read config.yaml:\n{e}")
        return DEFAULT_CFG.copy()

def save_config(cfg: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    except Exception as e:
        messagebox.showerror("Config error", f"Failed to write config.yaml:\n{e}")

# ────────────────────────────────────────────────────────────────────────────────
# Helpers: app dir & watcher command (single-EXE)
# ────────────────────────────────────────────────────────────────────────────────
def _app_dir() -> str:
    """Folder of the running app (handles PyInstaller and script)."""
    if getattr(sys, "frozen", False):  # PyInstaller onefile
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _find_watcher_py() -> Optional[str]:
    """Dev fallback: watcher.py path next to gui.py or CWD."""
    here = os.path.dirname(os.path.abspath(__file__))
    p1 = os.path.join(here, "watcher.py")
    if os.path.exists(p1):
        return p1
    p2 = os.path.join(os.getcwd(), "watcher.py")
    if os.path.exists(p2):
        return p2
    return None

def _build_watcher_cmd() -> Optional[List[str]]:
    """
    Build the command to run the watcher.
    - Frozen (one EXE): run this same EXE with --watcher
    - Dev: run watcher.py with the current Python
    """
    if getattr(sys, "frozen", False):
        # We run the same EXE with --watcher (watcher code is bundled/importable)
        return [sys.executable, "--watcher"]

    # Dev fallback
    wp = _find_watcher_py()
    if wp:
        return [sys.executable, wp]
    return None

# ────────────────────────────────────────────────────────────────────────────────
# ROI Selector (fullscreen drag overlay)
# ────────────────────────────────────────────────────────────────────────────────
class RoiSelector(tk.Toplevel):
    def __init__(self, master, screenshot: Image.Image, on_set):
        super().__init__(master)
        self.title("Select ROI")
        self.attributes("-fullscreen", True)
        self.configure(bg="#0b0f14")
        self.on_set = on_set
        self.overrideredirect(True)

        self.img = screenshot
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        self.scale = min(sw / self.img.width, sh / self.img.height)
        disp_w = int(self.img.width * self.scale)
        disp_h = int(self.img.height * self.scale)

        self.canvas = tk.Canvas(self, width=sw, height=sh, bg="#0b0f14", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.offset_x = (sw - disp_w) // 2
        self.offset_y = (sh - disp_h) // 2

        self.disp_img = self.img.resize((disp_w, disp_h), Image.BICUBIC)
        self.tk_img = ImageTk.PhotoImage(self.disp_img)
        self.canvas.create_image(self.offset_x, self.offset_y, anchor="nw", image=self.tk_img)

        self.canvas.create_rectangle(
            self.offset_x, self.offset_y, self.offset_x + disp_w, self.offset_y + disp_h,
            fill="#000000", stipple="gray50", outline=""
        )

        self.rect_id = None
        self.start = None
        self.end = None

        self.canvas.create_text(
            sw // 2, int(self.offset_y * 0.6) + 28,
            text="Drag to select the ARK tribe log area • Enter to accept • Esc to cancel",
            fill="#9acbfd", font=("Segoe UI", 18, "bold")
        )

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", self._accept)
        self.canvas.bind("<Button-1>", self._on_down)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_up)

    def _on_down(self, event):
        self.start = (event.x, event.y); self.end = (event.x, event.y)
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#00ffc3", width=3
        )

    def _on_drag(self, event):
        self.end = (event.x, event.y)
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], self.end[0], self.end[1])

    def _on_up(self, event):
        self.end = (event.x, event.y)
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], self.end[0], self.end[1])

    def _accept(self, _event=None):
        if not (self.start and self.end):
            self.destroy(); return
        x0 = min(self.start[0], self.end[0]) - self.offset_x
        y0 = min(self.start[1], self.end[1]) - self.offset_y
        x1 = max(self.start[0], self.end[0]) - self.offset_x
        y1 = max(self.start[1], self.end[1]) - self.offset_y

        x0 = max(0, min(self.disp_img.width, x0)); x1 = max(0, min(self.disp_img.width, x1))
        y0 = max(0, min(self.disp_img.height, y0)); y1 = max(0, min(self.disp_img.height, y1))

        ox0 = int(x0 / self.scale); oy0 = int(y0 / self.scale)
        ox1 = int(x1 / self.scale); oy1 = int(y1 / self.scale)

        x = min(ox0, ox1); y = min(oy0, oy1)
        w = max(1, abs(ox1 - ox0)); h = max(1, abs(oy1 - oy0))
        self.on_set(x, y, w, h)
        self.destroy()

# ────────────────────────────────────────────────────────────────────────────────
# Modern Dark UI + Triggers + License
# ────────────────────────────────────────────────────────────────────────────────
ACCENT = "#00ffc3"
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ARK Watchdog — Control Panel")
        self.geometry("1200x760")
        self.minsize(1050, 680)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.cfg = load_config()
        # Prefer a bundled Tesseract if present (sets cfg["tesseract_cmd"] automatically)
        use_bundled_tesseract(self.cfg)
        self.proc: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.stop_reader = threading.Event()

        self.triggers: List[Dict[str, Any]] = list(self.cfg.get("triggers", []))

        self._build_ui()
        self._populate_from_cfg()

        # Hotkey: F8 quick reselect ROI
        self.bind("<F8>", lambda e: self._select_roi())

    # UI layout
    def _build_ui(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, corner_radius=0); self.sidebar.pack(side="left", fill="y")
        self.sidebar.configure(fg_color="#0e141b")

        ctk.CTkLabel(self.sidebar, text="ARK Watchdog",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(16,4), padx=16, anchor="w")
        ctk.CTkLabel(self.sidebar, text="Screen log → Discord", text_color="#93a4b7").pack(pady=(0,16), padx=16, anchor="w")

        self.btn_save = ctk.CTkButton(self.sidebar, text="Save Config", command=self._on_save)
        self.btn_save.pack(padx=16, pady=6, fill="x")

        self.btn_test = ctk.CTkButton(self.sidebar, text="Test Discord", command=self._test_discord)
        self.btn_test.pack(padx=16, pady=6, fill="x")

        self.btn_roi = ctk.CTkButton(self.sidebar, text="Select ROI (drag)", fg_color=ACCENT, text_color="#001314",
                                     hover_color="#7fffe2", command=self._select_roi)
        self.btn_roi.pack(padx=16, pady=(6,18), fill="x")

        self.btn_start = ctk.CTkButton(self.sidebar, text="Start Watcher", command=self._toggle_watcher)
        self.btn_start.pack(padx=16, pady=6, fill="x")
        # store defaults so we can restore without passing None
        self._start_fg_default = self.btn_start.cget("fg_color")
        self._start_hover_default = self.btn_start.cget("hover_color")

        self.status = ctk.CTkLabel(self.sidebar, text="Ready.", text_color="#9acbfd")
        self.status.pack(padx=16, pady=(12,16), anchor="w")

        # Main container
        self.main = ctk.CTkFrame(self, corner_radius=0); self.main.pack(side="left", fill="both", expand=True)
        for i in range(2):
            self.main.grid_columnconfigure(i, weight=1)
        self.main.grid_rowconfigure(2, weight=1)

        # Card: Discord, License & OCR
        card1 = ctk.CTkFrame(self.main, corner_radius=14)
        card1.grid(row=0, column=0, padx=16, pady=16, sticky="nsew", columnspan=2)
        ctk.CTkLabel(card1, text="Discord, License & OCR", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14,8)
        )

        # Webhook
        ctk.CTkLabel(card1, text="Discord Webhook URL").grid(row=1, column=0, sticky="w", padx=14)
        self.webhook_var = tk.StringVar()
        self.webhook_ent = ctk.CTkEntry(card1, textvariable=self.webhook_var, width=800)
        self.webhook_ent.grid(row=2, column=0, sticky="we", padx=14, pady=(0,10))

        # Allowed mentions toggles
        am_row = ctk.CTkFrame(card1, fg_color="transparent")
        am_row.grid(row=3, column=0, sticky="w", padx=10, pady=(0,10))
        ctk.CTkLabel(am_row, text="Allowed mentions:").grid(row=0, column=0, padx=(4,10))
        self.allow_everyone_var = tk.BooleanVar(value=True)
        self.allow_roles_var = tk.BooleanVar(value=True)
        self.allow_users_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(am_row, text="@here/@everyone", variable=self.allow_everyone_var).grid(row=0, column=1, padx=6)
        ctk.CTkCheckBox(am_row, text="roles", variable=self.allow_roles_var).grid(row=0, column=2, padx=6)
        ctk.CTkCheckBox(am_row, text="users", variable=self.allow_users_var).grid(row=0, column=3, padx=6)

        # License section
        lic_frame = ctk.CTkFrame(card1, fg_color="transparent")
        lic_frame.grid(row=4, column=0, sticky="we", padx=10, pady=(6,6))
        lic_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(lic_frame, text="License Key").grid(row=0, column=0, padx=(4,6), sticky="w")
        self.lic_var = tk.StringVar()
        self.lic_entry = ctk.CTkEntry(lic_frame, textvariable=self.lic_var)
        self.lic_entry.grid(row=0, column=1, sticky="we", padx=(0,8))
        ctk.CTkButton(lic_frame, text="Activate", width=100, command=self._on_activate_license).grid(row=0, column=2, padx=4)
        ctk.CTkButton(lic_frame, text="Check", width=80, command=self._on_check_license).grid(row=0, column=3, padx=4)

        self.lic_status = ctk.CTkLabel(card1, text="License: unknown", text_color="#6f8296")
        self.lic_status.grid(row=5, column=0, sticky="w", padx=14, pady=(0,8))

        # Tesseract path
        ctk.CTkLabel(card1, text="Tesseract path (optional)").grid(row=6, column=0, sticky="w", padx=14)
        row = ctk.CTkFrame(card1, fg_color="transparent")
        row.grid(row=7, column=0, sticky="we", padx=10, pady=(0,10))
        row.grid_columnconfigure(0, weight=1)
        self.tess_var = tk.StringVar()
        self.tess_ent = ctk.CTkEntry(row, textvariable=self.tess_var)
        self.tess_ent.grid(row=0, column=0, sticky="we", padx=4)
        ctk.CTkButton(row, text="Browse…", width=110, command=self._browse_tesseract).grid(row=0, column=1, padx=6)

        # Interval & scale
        row2 = ctk.CTkFrame(card1, fg_color="transparent")
        row2.grid(row=8, column=0, sticky="we", padx=10, pady=(0,12))
        ctk.CTkLabel(row2, text="Interval (ms)").grid(row=0, column=0, padx=(4,6))
        self.interval_var = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=self.interval_var, width=90).grid(row=0, column=1)
        ctk.CTkLabel(row2, text="OCR scale").grid(row=0, column=2, padx=(18,6))
        self.scale_var = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=self.scale_var, width=90).grid(row=0, column=3)
        ctk.CTkLabel(row2, text="Send only newest").grid(row=0, column=4, padx=(18,6))
        self.only_newest_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(row2, variable=self.only_newest_var, text="").grid(row=0, column=5)

        # Card: ROI (left)
        card2 = ctk.CTkFrame(self.main, corner_radius=14)
        card2.grid(row=1, column=0, padx=16, pady=(0,16), sticky="nsew")
        ctk.CTkLabel(card2, text="ROI (screen region of ARK tribe log)",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14,8))

        grid = ctk.CTkFrame(card2, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="w", padx=12, pady=(0,8))
        self.roi_x = tk.StringVar(); self.roi_y = tk.StringVar()
        self.roi_w = tk.StringVar(); self.roi_h = tk.StringVar()
        def small_entry(var,w=90): return ctk.CTkEntry(grid, textvariable=var, width=w)
        ctk.CTkLabel(grid, text="x").grid(row=0, column=0, padx=(2,6)); small_entry(self.roi_x).grid(row=0, column=1)
        ctk.CTkLabel(grid, text="y").grid(row=0, column=2, padx=(16,6)); small_entry(self.roi_y).grid(row=0, column=3)
        ctk.CTkLabel(grid, text="w").grid(row=0, column=4, padx=(16,6)); small_entry(self.roi_w).grid(row=0, column=5)
        ctk.CTkLabel(grid, text="h").grid(row=0, column=6, padx=(16,6)); small_entry(self.roi_h).grid(row=0, column=7)

        ctk.CTkButton(card2, text="Select ROI (drag)", fg_color=ACCENT, text_color="#001314",
                      hover_color="#7fffe2", command=self._select_roi, width=180).grid(
            row=2, column=0, sticky="w", padx=14, pady=(6,12)
        )

        # Card: Triggers (right)
        card3 = ctk.CTkFrame(self.main, corner_radius=14)
        card3.grid(row=1, column=1, padx=(0,16), pady=(0,16), sticky="nsew")
        ctk.CTkLabel(card3, text="Triggers (first match wins)",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14,8)
        )

        body = ctk.CTkFrame(card3, fg_color="transparent"); body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0,12))
        body.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(body, corner_radius=10); left.grid(row=0, column=0, sticky="ns", padx=(0,10))
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="List").grid(row=0, column=0, pady=(8,4))
        self.trig_list = tk.Listbox(left, height=10, activestyle="dotbox",
                                    bg="#1a222c", fg="#e5edf5", highlightthickness=0,
                                    selectbackground="#2d3a49", selectforeground="#e5edf5",
                                    relief="flat", exportselection=False, width=24)
        self.trig_list.grid(row=1, column=0, sticky="nswe", padx=8, pady=(0,6))
        self.trig_list.bind("<<ListboxSelect>>", lambda e: self._load_trigger_into_fields())

        btns = ctk.CTkFrame(left, fg_color="transparent"); btns.grid(row=2, column=0, pady=(0,8))
        ctk.CTkButton(btns, text="Add", width=60, command=self._add_trigger).grid(row=0, column=0, padx=4)
        ctk.CTkButton(btns, text="Delete", width=70, command=self._del_trigger).grid(row=0, column=1, padx=4)
        ctk.CTkButton(btns, text="↑", width=34, command=lambda: self._move_trigger(-1)).grid(row=0, column=2, padx=2)
        ctk.CTkButton(btns, text="↓", width=34, command=lambda: self._move_trigger(1)).grid(row=0, column=3, padx=2)

        right = ctk.CTkFrame(body, corner_radius=10); right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(1, weight=1)

        self.f_name = tk.StringVar()
        self.f_match = tk.StringVar()
        self.f_type = tk.StringVar(value="keyword")
        self.f_mention_mode = tk.StringVar(value="none")
        self.f_mention_custom = tk.StringVar()
        self.f_prefix = tk.StringVar()
        self.f_suffix = tk.StringVar()

        def lbl(r, text): ctk.CTkLabel(right, text=text).grid(row=r, column=0, sticky="w", padx=10, pady=(10,0))
        def ent(r, var, w=260): ctk.CTkEntry(right, textvariable=var, width=w).grid(row=r, column=1, sticky="we", padx=(4,10), pady=(10,0))

        lbl(0, "Name");           ent(0, self.f_name)
        lbl(1, "Match (keyword or regex)"); ent(1, self.f_match)
        lbl(2, "Type")
        ctk.CTkOptionMenu(right, values=["keyword","regex"], variable=self.f_type, width=160)\
            .grid(row=2, column=1, sticky="w", padx=(4,10), pady=(10,0))
        lbl(3, "Mention")
        ctk.CTkOptionMenu(right, values=["none","@here","@everyone","custom"], variable=self.f_mention_mode, width=160,
                          command=lambda *_: self._toggle_custom_mention_field())\
            .grid(row=3, column=1, sticky="w", padx=(4,10), pady=(10,0))

        self.custom_row = ctk.CTkFrame(right, fg_color="transparent"); self.custom_row.grid(row=4, column=0, columnspan=2, sticky="we")
        ctk.CTkLabel(self.custom_row, text="Custom mention (e.g. <@&ROLE_ID>)").grid(row=0, column=0, sticky="w", padx=10, pady=(10,0))
        ctk.CTkEntry(self.custom_row, textvariable=self.f_mention_custom).grid(row=0, column=1, sticky="we", padx=(4,10), pady=(10,0))
        self._toggle_custom_mention_field()

        lbl(5, "Prefix text");    ent(5, self.f_prefix)
        lbl(6, "Suffix text");    ent(6, self.f_suffix)

        ctk.CTkButton(right, text="Apply changes to selected", command=self._apply_trigger_changes)\
            .grid(row=7, column=0, columnspan=2, padx=10, pady=12, sticky="e")

        # Card: Live logs (full width)
        card4 = ctk.CTkFrame(self.main, corner_radius=14)
        card4.grid(row=2, column=0, columnspan=2, padx=0, pady=(0,16), sticky="nsew")
        card4.grid_rowconfigure(1, weight=1)
        card4.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card4, text="Watcher Output", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(14,8)
        )
        self.log_box = ctk.CTkTextbox(card4); self.log_box.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0,12))
        self.log_box.configure(state="disabled")

        ctk.CTkLabel(self.main, text="tip: Triggers are checked top-to-bottom; first match wins.",
                     text_color="#6f8296").grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(0,12))

    # Populate
    def _populate_from_cfg(self):
        c = self.cfg
        self.webhook_var.set(c.get("discord_webhook_url",""))
        self.tess_var.set(c.get("tesseract_cmd",""))
        self.interval_var.set(str(c.get("capture_interval_ms",750)))
        self.scale_var.set(str(c.get("ocr_scale",2.0)))
        self.only_newest_var.set(bool(c.get("send_only_newest", True)))

        am = c.get("discord_allowed_mentions", {})
        self.allow_everyone_var.set(bool(am.get("everyone", True)))
        self.allow_roles_var.set(bool(am.get("roles", True)))
        self.allow_users_var.set(bool(am.get("users", False)))

        r = c.get("roi", {})
        self.roi_x.set(str(r.get("x",0))); self.roi_y.set(str(r.get("y",0)))
        self.roi_w.set(str(r.get("w",0))); self.roi_h.set(str(r.get("h",0)))

        self.triggers = list(c.get("triggers", []))
        self._refresh_trigger_list()

        # Prefill license key from cache if present
        try:
            cache_path = os.path.join(os.path.dirname(__file__), "license_cache.json")
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if cache.get("license_key"):
                    self.lic_var.set(cache["license_key"])
        except Exception:
            pass

        # Initial license status (gates Start button)
        self._on_check_license()

    # Triggers UI helpers
    def _refresh_trigger_list(self):
        self.trig_list.delete(0, "end")
        for t in self.triggers:
            self.trig_list.insert("end", t.get("name") or t.get("match") or "<unnamed>")
        if self.triggers:
            self.trig_list.selection_set(0)
            self._load_trigger_into_fields()

    def _load_trigger_into_fields(self):
        sel = self._selected_index()
        if sel is None: return
        t = self.triggers[sel]
        self.f_name.set(t.get("name",""))
        self.f_match.set(t.get("match",""))
        self.f_type.set((t.get("type") or "keyword").lower())
        self.f_mention_mode.set(t.get("mention_mode","none"))
        self.f_mention_custom.set(t.get("mention_custom",""))
        self.f_prefix.set(t.get("prefix",""))
        self.f_suffix.set(t.get("suffix",""))
        self._toggle_custom_mention_field()

    def _apply_trigger_changes(self):
        sel = self._selected_index()
        if sel is None: return
        self.triggers[sel] = {
            "name": self.f_name.get().strip() or "Untitled",
            "match": self.f_match.get().strip(),
            "type": (self.f_type.get() or "keyword").lower(),
            "mention_mode": self.f_mention_mode.get(),
            "mention_custom": self.f_mention_custom.get().strip(),
            "prefix": self.f_prefix.get(),
            "suffix": self.f_suffix.get(),
        }
        self._refresh_trigger_list()
        self.trig_list.selection_set(sel)

    def _add_trigger(self):
        self.triggers.append({
            "name": "New Trigger",
            "match": "",
            "type": "keyword",
            "mention_mode": "none",
            "mention_custom": "",
            "prefix": "",
            "suffix": "",
        })
        self._refresh_trigger_list()
        last = len(self.triggers) - 1
        self.trig_list.selection_clear(0, "end")
        self.trig_list.selection_set(last)
        self._load_trigger_into_fields()

    def _del_trigger(self):
        sel = self._selected_index()
        if sel is None: return
        del self.triggers[sel]
        self._refresh_trigger_list()

    def _move_trigger(self, delta: int):
        sel = self._selected_index()
        if sel is None: return
        nx = sel + delta
        if not (0 <= nx < len(self.triggers)): return
        self.triggers[sel], self.triggers[nx] = self.triggers[nx], self.triggers[sel]
        self._refresh_trigger_list()
        self.trig_list.selection_set(nx)

    def _toggle_custom_mention_field(self):
        mode = self.f_mention_mode.get()
        self.custom_row.grid_remove()
        if mode == "custom":
            self.custom_row.grid()

    def _selected_index(self) -> Optional[int]:
        sel = self.trig_list.curselection()
        if not sel: return None
        return int(sel[0])

    # License helpers
    def _set_license_status(self, ok: bool, msg: str):
        color = "#00ffc3" if ok else "#ff7a7a"
        self.lic_status.configure(text=f"License: {'VALID' if ok else 'INVALID'} — {msg}", text_color=color)
        self.btn_start.configure(state=("normal" if ok else "disabled"))

    def _on_check_license(self):
        ok, msg = license_client.require_valid(allow_online=False)
        if not ok:
            ok, msg = license_client.require_valid(allow_online=True)
        self._set_license_status(ok, msg)

    def _on_activate_license(self):
        key = self.lic_var.get().strip()
        ok, msg = license_client.activate_and_store(key) if hasattr(license_client, "activate_and_store") \
            else license_client.activate(key)
        self._set_license_status(ok, msg)
        if ok:
            messagebox.showinfo("License", "Activation successful.")
        else:
            messagebox.showerror("License", f"Activation failed: {msg}")

    # Actions
    def _browse_tesseract(self):
        path = filedialog.askopenfilename(title="Select tesseract.exe",
                                          filetypes=[("Executable","*.exe"),("All files","*.*")])
        if path: self.tess_var.set(path)

    def _on_save(self):
        self.cfg = self._collect_cfg_from_ui()
        save_config(self.cfg)
        self._set_status("Saved config.yaml")

    def _select_roi(self):
        with mss() as sct:
            img = np.array(sct.grab(sct.monitors[0]))[:, :, :3]
        img = Image.fromarray(img[:, :, ::-1])
        def on_set(x,y,w,h):
            self.roi_x.set(str(x)); self.roi_y.set(str(y))
            self.roi_w.set(str(w)); self.roi_h.set(str(h))
            self._set_status(f"ROI set → x:{x} y:{y} w:{w} h:{h}")
        RoiSelector(self, img, on_set)

    def _test_discord(self):
        cfg = self._collect_cfg_from_ui()
        url = cfg.get("discord_webhook_url","").strip()
        if not url:
            messagebox.showwarning("Discord","Webhook URL is empty.")
            return
        try:
            from discord_notifier import send_to_discord
            allowed = cfg.get("discord_allowed_mentions", {})
            payload = {"parse": []}
            if allowed.get("everyone"): payload["parse"].append("everyone")
            if allowed.get("roles"):    payload["parse"].append("roles")
            if allowed.get("users"):    payload["parse"].append("users")
            if allowed.get("role_ids"): payload["roles"] = allowed.get("role_ids")
            if allowed.get("user_ids"): payload["users"] = allowed.get("user_ids")
            send_to_discord("**ARK Watchdog** webhook test — it works!", None,
                            allowed_mentions=payload, webhook_url=url)
            messagebox.showinfo("Discord","Sent test message.")
        except Exception as e:
            messagebox.showerror("Discord", f"Failed to send: {e}")

    def _toggle_watcher(self):
        if self.proc is None:
            self._start_watcher()
        else:
            self._stop_watcher()

    def _start_watcher(self):
        # LICENSE GATE
        ok, msg = license_client.require_valid(allow_online=True)
        if not ok:
            self._set_license_status(False, msg)
            messagebox.showerror("License", f"Not valid: {msg}")
            return

        self.cfg = self._collect_cfg_from_ui()
        save_config(self.cfg)

        cmd = _build_watcher_cmd()
        if not cmd:
            messagebox.showerror(
                "Watcher",
                "Cannot find watcher to launch.\n\n"
                "In dev: keep watcher.py next to gui.py.\n"
                "For single EXE: build with PyInstaller and --hidden-import watcher."
            )
            return

        try:
            # UI: prepare
            self._clear_log()
            self._append_log(f"[GUI] launching: {' '.join(os.path.basename(x) for x in cmd)} …\n")
            self._set_status("Running watcher…")
            self.btn_start.configure(text="Stop Watcher", fg_color="#ff5a7a", hover_color="#ff7f97")
            self.btn_roi.configure(state="disabled")
            self.btn_save.configure(state="disabled")
            self.btn_test.configure(state="disabled")

            # Ensure clean reader state
            self.stop_reader.clear()

            # Force UTF-8 from child; never crash on weird bytes
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            # Ensure child sees bundled Tesseract (if present)
            # Ensure child sees the tessdata directory if we’re using a bundled exe
            if self.cfg.get("tesseract_cmd"):
                tdir = os.path.dirname(self.cfg["tesseract_cmd"])
                env.setdefault("TESSDATA_PREFIX", tdir)
                # Optional: put Tesseract folder at front of PATH so any tools that rely on PATH also work
                env["PATH"] = tdir + os.pathsep + env.get("PATH", "")

            self.proc = subprocess.Popen(
                cmd,
                cwd=_app_dir(),                 # launch from app folder
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,                      # line-buffered
                universal_newlines=True,        # text mode
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            # Start log reader thread
            self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self.reader_thread.start()

        except Exception as e:
            self._append_log(f"[GUI] failed to start watcher: {e}\n")
            self._set_status("Failed to start.")
            self._reset_controls()

    def _stop_watcher(self):
        if self.proc is None:
            return
        self._set_status("Stopping watcher…")
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass
        self.stop_reader.set()
        try:
            if self.reader_thread and self.reader_thread.is_alive():
                self.reader_thread.join(timeout=1.0)
        except Exception:
            pass
        self.proc = None
        self._set_status("Stopped.")
        self._reset_controls()

    def _reader_loop(self):
        p = self.proc
        if p is None or p.stdout is None:
            return
        try:
            for line in p.stdout:
                if self.stop_reader.is_set():
                    break
                self._append_log(line)
        finally:
            code = p.poll()
            self._append_log(f"\n[GUI] watcher exited with code {code}\n")
            self.after(0, self._stop_watcher)

    # UI helpers
    def _append_log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0","end")
        self.log_box.configure(state="disabled")

    def _set_status(self, s: str):
        self.status.configure(text=s)

    def _reset_controls(self):
        # restore button text/colors safely (avoid fg_color=None)
        kwargs = {"text": "Start Watcher"}
        if self._start_fg_default not in (None, "None"):
            kwargs["fg_color"] = self._start_fg_default
        if self._start_hover_default not in (None, "None"):
            kwargs["hover_color"] = self._start_hover_default
        self.btn_start.configure(**kwargs)
        self.btn_roi.configure(state="normal")
        self.btn_save.configure(state="normal")
        self.btn_test.configure(state="normal")

    def _collect_cfg_from_ui(self) -> Dict[str, Any]:
        cfg = load_config()
        cfg["discord_webhook_url"] = self.webhook_var.get().strip()
        cfg["tesseract_cmd"] = self.tess_var.get().strip()
        try: cfg["capture_interval_ms"] = int(float(self.interval_var.get()))
        except Exception: cfg["capture_interval_ms"] = DEFAULT_CFG["capture_interval_ms"]
        try: cfg["ocr_scale"] = float(self.scale_var.get())
        except Exception: cfg["ocr_scale"] = DEFAULT_CFG["ocr_scale"]
        cfg["send_only_newest"] = bool(self.only_newest_var.get())

        cfg["discord_allowed_mentions"] = {
            "everyone": bool(self.allow_everyone_var.get()),
            "roles": bool(self.allow_roles_var.get()),
            "users": bool(self.allow_users_var.get()),
            "role_ids": self.cfg.get("discord_allowed_mentions", {}).get("role_ids", []),
            "user_ids": self.cfg.get("discord_allowed_mentions", {}).get("user_ids", []),
        }
        try:
            cfg["roi"] = {
                "x": int(self.roi_x.get()), "y": int(self.roi_y.get()),
                "w": int(self.roi_w.get()), "h": int(self.roi_h.get()),
            }
        except Exception:
            messagebox.showerror("ROI error", "ROI values must be integers.")
        cfg["triggers"] = self.triggers
        return cfg

    def _on_close(self):
        if self.proc is not None:
            if not messagebox.askyesno("Quit", "Watcher is running. Stop and exit?"):
                return
            self._stop_watcher()
        self.destroy()

# ────────────────────────────────────────────────────────────────────────────────
# Watcher-mode entry (single-EXE): run watcher when invoked as: ArkWatchdog.exe --watcher
# ────────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__" and "--watcher" in sys.argv:
    # Run the watcher inside this process (child mode). Keep it as a separate
    # process from the GUI for clean log piping and stop/kill support.
    if _HAS_WATCHER_MODULE:
        # Ensure watcher sees a clean argv
        sys.argv = [sys.argv[0]]
        if hasattr(_watcher_module, "main"):
            _watcher_module.main()
        else:
            import runpy
            runpy.run_module("watcher", run_name="__main__")
    else:
        # Dev fallback: execute watcher.py directly if present
        import runpy
        wp = _find_watcher_py()
        if not wp:
            sys.stderr.write("watcher.py not found\n")
            sys.exit(2)
        runpy.run_path(wp, run_name="__main__")
    sys.exit(0)

# ────────────────────────────────────────────────────────────────────────────────
# GUI entry
# ────────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()
