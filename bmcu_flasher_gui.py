#!/usr/bin/env python3
import os
import sys
import json
import threading
import queue
import time
import webbrowser
import locale
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bmcu_flasher

APP_NAME = "BMCU Flasher"
APP_VERSION = "1.2"

FW_URL = "https://github.com/jarczakpawel/BMCU-C-PJARCZAK"
APP_URL = "https://github.com/jarczakpawel/BMCU-Flasher"
WCH_CH341SER_URL = "https://www.wch-ic.com/downloads/ch341ser_exe.html"

BG = "#121417"
FG = "#e6e6e6"
ENTRY_BG = "#1b1f24"
ENTRY_FG = "#e6e6e6"
BORDER = "#2b313a"
TREE_BG = "#111318"
HDR_BG = "#1b1f24"
SEL_BG = "#2b313a"
PB_TROUGH = "#1b1f24"
PB_GREEN = "#25c25a"
LINK_FG = "#7fb3ff"
WARN_RED = "#ff4d4d"

ONLINE_FORCE_STD = "Standard (normal load force)"
ONLINE_FORCE_HF = "High force (stronger load/print pressure)"

ONLINE_SLOT_SOLO = "SOLO"
ONLINE_SLOT_A = "AMS_A"
ONLINE_SLOT_B = "AMS_B"
ONLINE_SLOT_C = "AMS_C"
ONLINE_SLOT_D = "AMS_D"

ONLINE_RETRACTS = [
    ("10cm", "0.10f"),
    ("20cm", "0.20f"),
    ("25cm", "0.25f"),
    ("30cm", "0.30f"),
    ("35cm", "0.35f"),
    ("40cm", "0.40f"),
    ("45cm", "0.45f"),
    ("50cm", "0.50f"),
    ("55cm", "0.55f"),
    ("60cm", "0.60f"),
    ("65cm", "0.65f"),
    ("70cm", "0.70f"),
    ("75cm", "0.75f"),
    ("80cm", "0.80f"),
    ("85cm", "0.85f"),
    ("90cm", "0.90f"),
]

# name, vid, pid, baud, fast_baud, no_fast
ADAPTERS = [
    ("CH340/CH341 (WCH)", 0x1A86, 0x7523, 115200, 1000000, False),
    ("CP2102/CP210x (Silabs)", 0x10C4, 0xEA60, 115200, 921600, False),
    ("FT232R (FTDI)", 0x0403, 0x6001, 115200, 1000000, False),
    ("PL2303 (Prolific)", 0x067B, 0x2303, 115200, 460800, False),
    ("CH9102 (WCH)", 0x1A86, 0x55D4, 115200, 1000000, False),
    ("CH343 (WCH)", 0x1A86, 0x55D3, 115200, 1000000, False),
    ("ALL (no VID/PID filter)", 0, 0, 115200, 1000000, False),
]

def _user_cfg_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "BMCUFlasher")
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
        return os.path.join(base, "BMCUFlasher")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "bmcu_flasher")

def _cfg_path():
    return os.path.join(_user_cfg_dir(), "config.json")

def _cache_dir():
    return os.path.join(_user_cfg_dir(), "cache")

def _cfg_load():
    p = _cfg_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}

def _cfg_save(d: dict):
    try:
        os.makedirs(_user_cfg_dir(), exist_ok=True)
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, sort_keys=True)
    except Exception:
        pass

def _open_url(url: str):
    try:
        webbrowser.open(url)
    except Exception:
        pass

def _i18n_dir():
    return os.path.join(SCRIPT_DIR, "i18n")

def _available_langs():
    out = []
    try:
        for fn in os.listdir(_i18n_dir()):
            if fn.lower().endswith(".json"):
                code = os.path.splitext(fn)[0].upper()
                if code:
                    out.append(code)
    except Exception:
        pass
    return sorted(set(out))

def _detect_os_lang():
    loc = None
    try:
        loc = locale.getlocale()[0]
    except Exception:
        loc = None
    if not loc:
        try:
            loc = locale.getdefaultlocale()[0]
        except Exception:
            loc = None
    if not loc:
        return "EN"
    loc = str(loc).lower()
    if loc.startswith("pl"):
        return "PL"
    return "EN"

def _load_lang(lang: str):
    p = os.path.join(_i18n_dir(), f"{lang.lower()}.json")
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise RuntimeError("bad i18n file")
    return d

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.minsize(940, 720)
        self.configure(bg=BG)

        self.q = queue.Queue()
        self.worker = None
        self.cfg = _cfg_load()

        self.langs = _available_langs() or ["EN"]
        cfg_lang = (self.cfg.get("lang") or "").strip().upper()
        if not cfg_lang:
            cfg_lang = _detect_os_lang()
        if cfg_lang not in self.langs:
            cfg_lang = "EN" if "EN" in self.langs else self.langs[0]

        self.var_lang = tk.StringVar(value=cfg_lang)
        self._tr = {}
        self._load_i18n()

        self.var_mode = tk.StringVar(value=self.cfg.get("mode", "usb") or "usb")
        if self.var_mode.get() not in ("usb", "ttl"):
            self.var_mode.set("usb")

        self.var_vid = tk.StringVar(value=self.cfg.get("vid", "0x1a86"))
        self.var_pid = tk.StringVar(value=self.cfg.get("pid", "0x7523"))

        self.var_adapter = tk.StringVar(value=self.cfg.get("adapter", ADAPTERS[0][0]))
        if self.var_adapter.get() not in [a[0] for a in ADAPTERS]:
            self.var_adapter.set(ADAPTERS[0][0])

        self.var_port = tk.StringVar(value=self.cfg.get("port", ""))
        self.var_port_disp = tk.StringVar(value="")

        self.var_fw = tk.StringVar(value=self.cfg.get("fw_path", ""))
        self.var_fw_source = tk.StringVar(value=self.cfg.get("fw_source", "local"))

        self._online_sel = self.cfg.get("online_sel", None)
        if not isinstance(self._online_sel, dict):
            self._online_sel = None
        if self.var_fw_source.get() == "online" and self._online_sel and self._online_sel.get("display"):
            self.var_fw.set(self._online_sel["display"])

        self.var_baud = tk.StringVar(value=str(self.cfg.get("baud", 115200)))
        self.var_fast_baud = tk.StringVar(value=str(self.cfg.get("fast_baud", 1000000)))
        self.var_no_fast = tk.BooleanVar(value=bool(self.cfg.get("no_fast", False)))
        self.var_verify = tk.BooleanVar(value=bool(self.cfg.get("verify", True)))

        self.var_remote_ver = tk.StringVar(value="")

        self._port_map = {}
        self._help_win = None
        self._ui_root = None

        self._topbar_spacer = None
        self._lang_box = None

        self._row_adapter_lbl = None
        self._row_adapter_cell = None

        self.cmb_lang = None
        self.cmb_adapter = None
        self.ent_vid = None
        self.ent_pid = None
        self.cmb_ports = None
        self.ent_fw = None
        self.btn_refresh = None
        self.btn_flash = None
        self.pbar = None
        self.tree = None
        self._menu = None

        # layout lock (fixes "overlap" glitches on mode switch / rebuild)
        self._layout_busy = False
        self._layout_seq = 0

        self._style()
        self._build_ui()

        self._apply_mode_layout(init=True)
        try:
            self.update_idletasks()
            w = max(980, int(self.winfo_reqwidth() or 0))
            h = max(780, int(self.winfo_reqheight() or 0))
            self.minsize(w, h)
            self.geometry(f"{w}x{h}")
        except Exception:
            pass
        self._refresh_ports()
        self._fetch_remote_version()

        self.after(50, self._drain_events)

    def T(self, key: str) -> str:
        v = self._tr.get(key)
        return v if isinstance(v, str) else key

    def _load_i18n(self):
        try:
            self._tr = _load_lang(self.var_lang.get())
        except Exception:
            self._tr = {}

    def _set_lang(self, lang: str):
        lang = (lang or "").strip().upper()
        if not lang or lang not in self.langs:
            return
        self.var_lang.set(lang)
        self.cfg["lang"] = lang
        _cfg_save(self.cfg)
        self._load_i18n()
        self._rebuild_ui()

    def _layout_begin(self) -> int:
        self._layout_seq += 1
        self._layout_busy = True
        return self._layout_seq

    def _layout_end(self, seq: int, do_refresh: bool = False):
        if seq != self._layout_seq:
            return

        try:
            self.update_idletasks()
        except Exception:
            pass

        if do_refresh:
            self._refresh_ports()
            try:
                self.update_idletasks()
            except Exception:
                pass

        self._layout_busy = False
        self.after(0, self._sync_topbar_center)

    def _rebuild_ui(self):
        seq = self._layout_begin()

        try:
            if self._help_win and self._help_win.winfo_exists():
                self._help_win.destroy()
        except Exception:
            pass
        self._help_win = None

        if self._ui_root and self._ui_root.winfo_exists():
            try:
                self._ui_root.destroy()
            except Exception:
                pass
        self._ui_root = None

        self._build_ui()
        self._apply_mode_layout(init=True)
        self._refresh_ports()
        if not (self.var_remote_ver.get() or "").strip():
            self._fetch_remote_version()

        self.after_idle(lambda: self._layout_end(seq, do_refresh=False))

    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        self.option_add("*TCombobox*Listbox.background", TREE_BG)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", SEL_BG)
        self.option_add("*TCombobox*Listbox.selectForeground", FG)

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)

        style.configure("TButton", padding=(10, 6), background=ENTRY_BG, foreground=FG)
        style.map("TButton",
                  background=[("active", SEL_BG), ("pressed", SEL_BG)],
                  foreground=[("active", FG), ("pressed", FG)])

        style.configure("TSeparator", background=BORDER)

        style.configure("TRadiobutton", background=BG, foreground=FG)
        style.map("TRadiobutton",
                  background=[("active", BG), ("pressed", BG), ("selected", BG)],
                  foreground=[("active", FG), ("pressed", FG), ("selected", FG)])

        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton",
                  background=[("active", BG), ("pressed", BG), ("selected", BG)],
                  foreground=[("active", FG), ("pressed", FG), ("selected", FG)])

        style.configure("TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=FG, arrowcolor=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", ENTRY_BG), ("active", ENTRY_BG)],
                  foreground=[("readonly", FG), ("active", FG), ("disabled", FG)],
                  background=[("readonly", ENTRY_BG), ("active", ENTRY_BG)],
                  arrowcolor=[("readonly", FG), ("active", FG), ("disabled", FG)])

        style.configure("Lang.TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=FG, arrowcolor=FG, padding=6)

        style.configure("Treeview", background=TREE_BG, fieldbackground=TREE_BG, foreground=FG, rowheight=22,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("Treeview.Heading", background=HDR_BG, foreground=FG, relief="flat")
        style.map("Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", FG)])
        style.map("Treeview.Heading",
                  background=[("active", HDR_BG), ("pressed", HDR_BG)],
                  foreground=[("active", FG), ("pressed", FG)])

        style.configure("Green.Horizontal.TProgressbar", troughcolor=PB_TROUGH, background=PB_GREEN,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)

    def _entry(self, parent, textvariable, width=None):
        e = tk.Entry(
            parent,
            textvariable=textvariable,
            bg=ENTRY_BG,
            fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
            bd=0,
        )
        if width is not None:
            e.config(width=width)
        return e

    def _link_label(self, parent, text: str, url: str):
        l = tk.Label(parent, text=text, bg=BG, fg=LINK_FG, cursor="hand2")
        l.bind("<Button-1>", lambda _e: _open_url(url))
        return l

    def _sync_topbar_center(self):
        if not self._topbar_spacer or not self._lang_box:
            return
        try:
            self.update_idletasks()
            w = int(self._lang_box.winfo_reqwidth() or 0)
            if w < 1:
                w = 1
            self._topbar_spacer.config(width=w)
        except Exception:
            pass

    def _get_adapter(self, name: str):
        for a in ADAPTERS:
            if a[0] == name:
                return a
        return ADAPTERS[0]

    def _apply_adapter_presets(self, name: str, save: bool = True):
        a = self._get_adapter(name)
        _, vid, pid, baud, fast_baud, no_fast = a

        self.var_adapter.set(a[0])

        if vid and pid:
            self.var_vid.set(f"0x{vid:04x}")
            self.var_pid.set(f"0x{pid:04x}")
        else:
            self.var_vid.set("0x0000")
            self.var_pid.set("0x0000")

        self.var_baud.set(str(int(baud)))
        self.var_fast_baud.set(str(int(fast_baud)))
        self.var_no_fast.set(bool(no_fast))

        if save:
            self.cfg["adapter"] = (self.var_adapter.get() or "").strip()
            self.cfg["vid"] = (self.var_vid.get() or "").strip()
            self.cfg["pid"] = (self.var_pid.get() or "").strip()
            self.cfg["baud"] = int(self.var_baud.get())
            self.cfg["fast_baud"] = int(self.var_fast_baud.get())
            self.cfg["no_fast"] = bool(self.var_no_fast.get())
            _cfg_save(self.cfg)

    def _force_ch340(self):
        self._apply_adapter_presets("CH340/CH341 (WCH)", save=True)

    def _build_ui(self):
        outer = tk.Frame(self, bg=BG)
        self._ui_root = outer
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.rowconfigure(1, weight=0)

        root = ttk.Frame(outer, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)

        footer = tk.Frame(outer, bg=BG)
        footer.grid(row=1, column=0, sticky="we", padx=12, pady=(0, 12))

        tk.Label(footer, text=f"{APP_NAME} v{APP_VERSION}", bg=BG, fg=FG).pack(side="left")
        tk.Label(footer, text=" - ", bg=BG, fg=FG).pack(side="left")
        self._link_label(footer, APP_URL, APP_URL).pack(side="left")
        tk.Label(footer, text=" - ", bg=BG, fg=FG).pack(side="left")
        tk.Label(footer, text=self.T("author_label"), bg=BG, fg=FG).pack(side="left")

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="we")

        topbar = ttk.Frame(header)
        topbar.pack(fill="x", pady=(0, 6))
        topbar.columnconfigure(0, weight=1)
        topbar.columnconfigure(1, weight=0)
        topbar.columnconfigure(2, weight=1)

        self._topbar_spacer = tk.Frame(topbar, bg=BG, width=1, height=1)
        self._topbar_spacer.grid(row=0, column=0, sticky="w")

        ttk.Label(topbar, text=APP_NAME, font=("TkDefaultFont", 16, "bold")).grid(row=0, column=1)

        self._lang_box = ttk.Frame(topbar)
        self._lang_box.grid(row=0, column=2, sticky="e")
        ttk.Label(self._lang_box, text=self.T("lang_label")).pack(side="left", padx=(0, 6))
        self.cmb_lang = ttk.Combobox(self._lang_box, textvariable=self.var_lang, state="readonly", width=8, values=self.langs, style="Lang.TCombobox")
        self.cmb_lang.pack(side="left")
        self.cmb_lang.bind("<<ComboboxSelected>>", lambda _e: self._set_lang(self.var_lang.get()))
        self._lang_box.bind("<Configure>", lambda _e: self.after(0, self._sync_topbar_center))
        self.after(0, self._sync_topbar_center)

        self._img = None
        self._img_small = None
        img_path = os.path.join(SCRIPT_DIR, "bmcu320.png")
        if os.path.isfile(img_path):
            try:
                self._img = tk.PhotoImage(file=img_path)
                self._img_small = self._img.zoom(13, 13).subsample(16, 16)
            except Exception:
                self._img = None
                self._img_small = None

        self._icon = None
        icon_path = os.path.join(SCRIPT_DIR, "icon.png")
        if os.path.isfile(icon_path):
            try:
                self._icon = tk.PhotoImage(file=icon_path)
                self.iconphoto(True, self._icon)
            except Exception:
                pass

        if self._img_small:
            ttk.Label(header, image=self._img_small).pack(anchor="center", pady=(0, 10))

        cfg = ttk.Frame(root)
        cfg.grid(row=1, column=0, sticky="we", pady=(0, 8))
        cfg.columnconfigure(0, weight=0, minsize=160)
        cfg.columnconfigure(1, weight=1)
        cfg.columnconfigure(2, weight=0)

        r = 0
        ttk.Label(cfg, text=self.T("mode_label")).grid(row=r, column=0, sticky="w", pady=(0, 10))
        mode_box = ttk.Frame(cfg)
        mode_box.grid(row=r, column=1, sticky="w", pady=(0, 10))
        ttk.Radiobutton(mode_box, text=self.T("mode_usb"), variable=self.var_mode, value="usb", command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(mode_box, text=self.T("mode_ttl"), variable=self.var_mode, value="ttl", command=self._on_mode_change).pack(side="left", padx=(18, 0))
        r += 1

        self._row_adapter_lbl = ttk.Label(cfg, text=self.T("adapter_label"))
        self._row_adapter_lbl.grid(row=r, column=0, sticky="w", pady=(0, 10))

        self._row_adapter_cell = ttk.Frame(cfg)
        self._row_adapter_cell.grid(row=r, column=1, sticky="w", pady=(0, 10))

        self.cmb_adapter = ttk.Combobox(self._row_adapter_cell, textvariable=self.var_adapter, state="readonly", width=22, values=[a[0] for a in ADAPTERS])
        self.cmb_adapter.pack(side="left")
        self.cmb_adapter.bind("<<ComboboxSelected>>", self._on_adapter_selected)

        ttk.Label(self._row_adapter_cell, text="  " + self.T("vid_label")).pack(side="left", padx=(10, 6))
        self.ent_vid = self._entry(self._row_adapter_cell, self.var_vid, width=12)
        self.ent_vid.pack(side="left")

        ttk.Label(self._row_adapter_cell, text="  " + self.T("pid_label")).pack(side="left", padx=(12, 6))
        self.ent_pid = self._entry(self._row_adapter_cell, self.var_pid, width=12)
        self.ent_pid.pack(side="left")
        r += 1

        ttk.Label(cfg, text=self.T("port_label")).grid(row=r, column=0, sticky="w", pady=(0, 10))
        self.cmb_ports = ttk.Combobox(cfg, textvariable=self.var_port_disp, state="readonly")
        self.cmb_ports.grid(row=r, column=1, sticky="we", pady=(0, 10), ipady=4)
        self.cmb_ports.bind("<<ComboboxSelected>>", self._on_port_selected)

        port_btns = ttk.Frame(cfg)
        port_btns.grid(row=r, column=2, sticky="e", pady=(0, 10))
        self.btn_refresh = ttk.Button(port_btns, text=self.T("refresh"), command=self._refresh_ports)
        self.btn_refresh.pack(side="left")
        r += 1

        ttk.Label(cfg, text=self.T("firmware_label")).grid(row=r, column=0, sticky="w", pady=(0, 10))
        self.ent_fw = self._entry(cfg, self.var_fw)
        self.ent_fw.grid(row=r, column=1, sticky="we", pady=(0, 10), padx=(0, 10), ipady=4)

        fw_btns = ttk.Frame(cfg)
        fw_btns.grid(row=r, column=2, sticky="e", pady=(0, 10))
        ttk.Button(fw_btns, text=self.T("browse"), command=self._browse_fw).pack(side="left", padx=(0, 8))
        ttk.Button(fw_btns, text=self.T("online"), command=self._online_fw).pack(side="left")
        r += 1

        mid = ttk.Frame(root)
        mid.grid(row=2, column=0, sticky="we")
        mid.columnconfigure(0, weight=1)

        link_row = ttk.Frame(mid)
        link_row.pack(fill="x", pady=(0, 8))
        link_row.columnconfigure(2, weight=1)

        ttk.Label(link_row, text=self.T("latest_fw_label")).grid(row=0, column=0, sticky="w")
        tk.Label(link_row, textvariable=self.var_remote_ver, bg=BG, fg=PB_GREEN, font=("TkDefaultFont", 11, "bold")).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._link_label(link_row, FW_URL, FW_URL).grid(row=0, column=3, sticky="e")

        warn_border = tk.Frame(mid, bg=WARN_RED)
        warn_border.pack(fill="x", pady=(0, 10))
        warn_border.columnconfigure(0, weight=1)

        warn = tk.Frame(warn_border, bg=BG)
        warn.grid(row=0, column=0, sticky="we", padx=1, pady=1)
        warn.columnconfigure(0, weight=1)

        tk.Label(warn, text=self.T("warn_title"), bg=BG, fg=WARN_RED, font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        tk.Label(warn, text=self.T("warn_text"), bg=BG, fg=FG, justify="left", wraplength=900).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

        ttk.Separator(root).grid(row=4, column=0, sticky="we", pady=10)

        opt = ttk.Frame(root)
        opt.grid(row=5, column=0, sticky="we")
        for c in range(10):
            opt.columnconfigure(c, weight=0)

        rr = 0
        ttk.Label(opt, text=self.T("baud_label")).grid(row=rr, column=0, sticky="w")
        self._entry(opt, self.var_baud, width=10).grid(row=rr, column=1, sticky="w", padx=(6, 12))
        ttk.Label(opt, text=self.T("fast_baud_label")).grid(row=rr, column=2, sticky="w")
        self._entry(opt, self.var_fast_baud, width=10).grid(row=rr, column=3, sticky="w", padx=(6, 12))
        ttk.Checkbutton(opt, text=self.T("no_fast"), variable=self.var_no_fast).grid(row=rr, column=4, sticky="w", padx=(0, 12))
        ttk.Checkbutton(opt, text=self.T("verify"), variable=self.var_verify).grid(row=rr, column=5, sticky="w")
        rr += 1

        act = ttk.Frame(root)
        act.grid(row=6, column=0, sticky="we", pady=(12, 8))
        act.columnconfigure(3, weight=1)

        left = ttk.Frame(act)
        left.grid(row=0, column=0, columnspan=3, sticky="w")

        self.btn_flash = ttk.Button(left, text=self.T("flash"), command=self._start)
        self.btn_flash.pack(side="left")
        ttk.Button(left, text=self.T("clear_log"), command=self._clear_log).pack(side="left", padx=10)
        ttk.Button(left, text=self.T("copy_log"), command=self._copy_all_log).pack(side="left")

        ttk.Button(act, text=self.T("help"), command=self._help).grid(row=0, column=4, sticky="e")

        self.pbar = ttk.Progressbar(root, mode="determinate", maximum=100, style="Green.Horizontal.TProgressbar")
        self.pbar.grid(row=7, column=0, sticky="we", pady=(0, 10))

        logf = ttk.Frame(root)
        logf.grid(row=8, column=0, sticky="nsew")
        root.rowconfigure(8, weight=1)

        cols = ("time", "level", "msg")
        self.tree = ttk.Treeview(logf, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("time", text=self.T("log_time"))
        self.tree.heading("level", text=self.T("log_level"))
        self.tree.heading("msg", text=self.T("log_message"))
        self.tree.column("time", width=90, anchor="w", stretch=False)
        self.tree.column("level", width=80, anchor="w", stretch=False)
        self.tree.column("msg", width=720, anchor="w", stretch=True)
        self.tree.tag_configure("action", foreground=PB_GREEN)

        vsb = ttk.Scrollbar(logf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)

        self._menu = tk.Menu(self, tearoff=0, bg=HDR_BG, fg=FG, activebackground=SEL_BG, activeforeground=FG)
        self._menu.add_command(label=self.T("menu_copy_selected"), command=self._copy_selected_log)
        self._menu.add_command(label=self.T("menu_copy_all"), command=self._copy_all_log)
        self.tree.bind("<Button-3>", self._on_tree_menu)
        self.tree.bind("<Control-c>", lambda _e: self._copy_selected_log())
        self.tree.bind("<Control-C>", lambda _e: self._copy_selected_log())
        self.bind_all("<Control-Shift-c>", lambda _e: self._copy_all_log())
        self.bind_all("<Control-Shift-C>", lambda _e: self._copy_all_log())

    def _set_row_visible(self, w, visible: bool):
        if not w:
            return
        if visible:
            try:
                w.grid()
            except Exception:
                pass
        else:
            try:
                w.grid_remove()
            except Exception:
                pass

    def _apply_mode_layout(self, init: bool = False):
        mode = self.var_mode.get()

        if mode == "usb":
            self._force_ch340()
            self._set_row_visible(self._row_adapter_lbl, False)
            self._set_row_visible(self._row_adapter_cell, False)
        else:
            self._set_row_visible(self._row_adapter_lbl, True)
            self._set_row_visible(self._row_adapter_cell, True)
            if not init:
                name = (self.var_adapter.get() or "").strip()
                self._apply_adapter_presets(name, save=True)

        self.cfg["mode"] = mode
        _cfg_save(self.cfg)
        self.after(0, self._sync_topbar_center)

    def _on_mode_change(self):
        if self._layout_busy:
            return
        seq = self._layout_begin()
        self._apply_mode_layout(init=False)
        self.after_idle(lambda: self._layout_end(seq, do_refresh=True))

    def _on_adapter_selected(self, _ev=None):
        name = (self.var_adapter.get() or "").strip()
        self._apply_adapter_presets(name, save=True)
        self._refresh_ports()

    def _fetch_remote_version(self):
        def worker():
            try:
                ver = bmcu_flasher.remote_get_version()
            except Exception:
                ver = ""
            self.q.put(("ver", ver))
        threading.Thread(target=worker, daemon=True).start()

    def _on_tree_menu(self, ev):
        try:
            self._menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self._menu.grab_release()

    def _copy_selected_log(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        v = self.tree.item(iid, "values")
        if not v or len(v) < 3:
            return
        s = f"{v[0]}\t{v[1]}\t{v[2]}\n"
        self.clipboard_clear()
        self.clipboard_append(s)

    def _copy_all_log(self):
        rows = []
        for iid in self.tree.get_children():
            v = self.tree.item(iid, "values")
            if v and len(v) >= 3:
                rows.append(f"{v[0]}\t{v[1]}\t{v[2]}")
        if not rows:
            return
        s = "Time\tLevel\tMessage\n" + "\n".join(rows) + "\n"
        self.clipboard_clear()
        self.clipboard_append(s)

    def _clear_log(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def _enqueue_log(self, level: str, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.q.put(("log", ts, level, msg))

    def _enqueue_progress(self, pct: int, done: int, total: int):
        self.q.put(("prog", int(pct)))

    def _drain_events(self):
        if self._layout_busy:
            self.after(50, self._drain_events)
            return

        try:
            while True:
                ev = self.q.get_nowait()
                if not ev:
                    continue
                if ev[0] == "log":
                    _, ts, level, msg = ev
                    tags = ("action",) if level == "ACTION" else ()
                    self.tree.insert("", "end", values=(ts, level, msg), tags=tags)
                    kids = self.tree.get_children()
                    if kids:
                        self.tree.see(kids[-1])
                elif ev[0] == "prog":
                    _, pct = ev
                    self.pbar["value"] = pct
                elif ev[0] == "done":
                    _, ok, msg = ev
                    self.btn_flash.config(state="normal")
                    if ok:
                        messagebox.showinfo(APP_NAME, self.T("ok"))
                    else:
                        messagebox.showerror(APP_NAME, msg or self.T("err_generic"))
                elif ev[0] == "ver":
                    _, ver = ev
                    self.var_remote_ver.set(ver or "")
        except queue.Empty:
            pass
        self.after(50, self._drain_events)

    def _on_port_selected(self, _ev=None):
        disp = self.var_port_disp.get()
        dev = self._port_map.get(disp, "")
        if dev:
            self.var_port.set(dev)
            self.cfg["port"] = dev
            _cfg_save(self.cfg)

    def _ports_ttl_filtered(self):
        name = (self.var_adapter.get() or "").strip()
        a = self._get_adapter(name)
        vid = int(a[1])
        pid = int(a[2])

        if vid == 0 and pid == 0:
            ps = bmcu_flasher.list_all_ports()
            out = []
            for p in ps:
                dev = (p.device or "")
                desc = (p.description or "")
                d = dev.lower()
                s = desc.lower()

                if p.vid is not None and p.pid is not None:
                    out.append(p)
                    continue
                if dev.upper().startswith("COM"):
                    out.append(p)
                    continue
                if "ttyusb" in d or "ttyacm" in d or d.startswith("/dev/cu.") or d.startswith("/dev/ttyusb") or d.startswith("/dev/ttyacm"):
                    out.append(p)
                    continue
                if "usb" in s or "serial" in s:
                    out.append(p)
                    continue

            if out:
                return out, False
            return ps, True

        try:
            vid_e = int(self.var_vid.get(), 0)
            pid_e = int(self.var_pid.get(), 0)
        except Exception:
            return [], True

        ps = bmcu_flasher.list_matching_ports(vid_e, pid_e)
        if ps:
            return ps, False
        return [], True

    def _refresh_ports(self):
        mode = self.var_mode.get()
        ports = []
        fallback = False

        if mode == "usb":
            try:
                vid = int(self.var_vid.get(), 0)
                pid = int(self.var_pid.get(), 0)
            except Exception:
                self._enqueue_log("ERROR", self.T("err_bad_vidpid"))
                return
            ports = bmcu_flasher.list_matching_ports(vid, pid)
        else:
            ports, fallback = self._ports_ttl_filtered()

        self._port_map.clear()
        items = []
        first_dev = ""
        first_disp = ""

        for p in ports:
            vid_s = f"0x{p.vid:04x}" if p.vid is not None else "----"
            pid_s = f"0x{p.pid:04x}" if p.pid is not None else "----"
            disp = f"{p.device} - {p.description} (vid={vid_s} pid={pid_s})"
            self._port_map[disp] = p.device
            items.append(disp)
            if not first_dev:
                first_dev = p.device
                first_disp = disp

        self.cmb_ports["values"] = items

        pref = self.cfg.get("port", "") or (self.var_port.get() or "").strip()
        chosen_disp = ""
        chosen_dev = ""

        if pref:
            for disp, dev in self._port_map.items():
                if dev == pref:
                    chosen_disp = disp
                    chosen_dev = dev
                    break

        if not chosen_dev and first_dev:
            chosen_dev = first_dev
            chosen_disp = first_disp

        self.var_port.set(chosen_dev)
        self.var_port_disp.set(chosen_disp)

        if fallback:
            self._enqueue_log("INFO", f"ports: {len(items)} (mode={mode}, fallback)")
        else:
            self._enqueue_log("INFO", f"ports: {len(items)} (mode={mode})")

        self.cfg["mode"] = mode
        self.cfg["vid"] = (self.var_vid.get() or "").strip()
        self.cfg["pid"] = (self.var_pid.get() or "").strip()
        self.cfg["adapter"] = (self.var_adapter.get() or "").strip()
        _cfg_save(self.cfg)

    def _browse_fw(self):
        initdir = self.cfg.get("fw_dir", "")
        if initdir and not os.path.isdir(initdir):
            initdir = ""
        p = filedialog.askopenfilename(
            title=self.T("dlg_select_bin_title"),
            initialdir=initdir or None,
            filetypes=[(self.T("dlg_bin_filter"), "*.bin"), (self.T("dlg_all_filter"), "*.*")],
        )
        if p:
            self.var_fw_source.set("local")
            self._online_sel = None
            self.var_fw.set(p)
            d = os.path.dirname(p)
            self.cfg["fw_dir"] = d
            self.cfg["fw_path"] = p
            self.cfg["fw_source"] = "local"
            self.cfg.pop("online_sel", None)
            _cfg_save(self.cfg)

    def _online_fw(self):
        if self.worker and self.worker.is_alive():
            return

        w = tk.Toplevel(self)
        w.title(self.T("online_title"))
        w.configure(bg=BG)
        w.minsize(780, 560)
        w.transient(self)
        w.grab_set()

        root = tk.Frame(w, bg=BG)
        root.pack(fill="both", expand=True, padx=16, pady=16)
        root.columnconfigure(0, weight=1)

        tk.Label(root, text=self.T("online_header"), bg=BG, fg=FG, font=("TkDefaultFont", 14, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 12))

        var_force = tk.StringVar(value=ONLINE_FORCE_STD)
        var_slot = tk.StringVar(value=ONLINE_SLOT_SOLO)
        var_retract = tk.StringVar(value="9.5cm")
        var_autoload = tk.BooleanVar(value=True)
        var_rgb = tk.BooleanVar(value=True)

        def step_box(parent, title):
            f = tk.Frame(parent, bg=BG, highlightthickness=1, highlightbackground=BORDER)
            tk.Label(f, text=title, bg=BG, fg=FG, font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 6))
            inner = tk.Frame(f, bg=BG)
            inner.pack(fill="x", padx=12, pady=(0, 12))
            return f, inner

        def _retract_values_for_slot(slot: str):
            if slot == ONLINE_SLOT_SOLO:
                xs = ["9.5cm"]
                xs += [cm for cm, _ in ONLINE_RETRACTS]
                return xs
            return [cm for cm, _ in ONLINE_RETRACTS]

        def _on_slot_change(_ev=None):
            vals = _retract_values_for_slot(var_slot.get())
            cmb_retract["values"] = vals
            cur = var_retract.get()
            if cur not in vals:
                var_retract.set(vals[0])

        def _pick():
            force = var_force.get()
            slot = var_slot.get()
            retract_disp = var_retract.get()
            autoload = bool(var_autoload.get())
            rgb = bool(var_rgb.get())

            if force == ONLINE_FORCE_HF:
                mode_dir = "high_force_load(P1S)"
            else:
                mode_dir = "standard(A1)"

            dm_dir = "AUTOLOAD" if autoload else "NO_AUTOLOAD"
            rgb_dir = "FILAMENT_RGB_ON" if rgb else "FILAMENT_RGB_OFF"

            retract_val = None
            if retract_disp == "9.5cm":
                retract_val = "0.095f"
            else:
                for cm_disp, val in ONLINE_RETRACTS:
                    if cm_disp == retract_disp:
                        retract_val = val
                        break
            if not retract_val:
                messagebox.showerror(APP_NAME, self.T("err_bad_retract"))
                return

            slot_dir = slot
            file_slot = slot

            if slot == ONLINE_SLOT_SOLO:
                if retract_val == "0.095f":
                    slot_dir = "SOLO"
                    file_name = "solo_0.095f.bin"
                    file_slot = "SOLO"
                    ret_cm = "9.5cm"
                else:
                    slot_dir = "AMS_A"
                    file_slot = "AMS_A"
                    file_name = f"ams_a_{retract_val}.bin"
                    ret_cm = retract_disp
            else:
                slot_dir = slot
                s = slot.split("_", 1)[1].lower()
                file_name = f"ams_{s}_{retract_val}.bin"
                ret_cm = retract_disp

            rel_path = f"{mode_dir}/{dm_dir}/{rgb_dir}/{slot_dir}/{file_name}"
            display = f"[ONLINE] {file_slot} RET={ret_cm} AUTOLOAD={'ON' if autoload else 'OFF'} RGB={'ON' if rgb else 'OFF'} ({mode_dir})"

            self.var_fw_source.set("online")
            self._online_sel = {"rel_path": rel_path, "display": display}
            self.var_fw.set(display)

            self.cfg["fw_source"] = "online"
            self.cfg["online_sel"] = self._online_sel
            _cfg_save(self.cfg)

            w.destroy()

        row = 1

        b1, i1 = step_box(root, self.T("online_step1_title"))
        b1.grid(row=row, column=0, sticky="we", pady=(0, 10))
        row += 1

        tk.Label(i1, text=self.T("online_force_label"), bg=BG, fg=FG).grid(row=0, column=0, sticky="w")
        cmb_force = ttk.Combobox(i1, textvariable=var_force, state="readonly", values=[ONLINE_FORCE_STD, ONLINE_FORCE_HF])
        cmb_force.grid(row=0, column=1, sticky="we", padx=(10, 0))
        i1.columnconfigure(1, weight=1)
        tk.Label(i1, text=self.T("online_force_hint"), bg=BG, fg=FG, justify="left", wraplength=720).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        b2, i2 = step_box(root, self.T("online_step2_title"))
        b2.grid(row=row, column=0, sticky="we", pady=(0, 10))
        row += 1

        tk.Label(i2, text=self.T("online_slot_label"), bg=BG, fg=FG).grid(row=0, column=0, sticky="w")
        cmb_slot = ttk.Combobox(i2, textvariable=var_slot, state="readonly", values=[ONLINE_SLOT_SOLO, ONLINE_SLOT_A, ONLINE_SLOT_B, ONLINE_SLOT_C, ONLINE_SLOT_D])
        cmb_slot.grid(row=0, column=1, sticky="we", padx=(10, 0))
        cmb_slot.bind("<<ComboboxSelected>>", _on_slot_change)

        tk.Label(i2, text=self.T("online_retract_label"), bg=BG, fg=FG).grid(row=1, column=0, sticky="w", pady=(8, 0))
        cmb_retract = ttk.Combobox(i2, textvariable=var_retract, state="readonly")
        cmb_retract.grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(8, 0))
        i2.columnconfigure(1, weight=1)

        _on_slot_change()
        tk.Label(i2, text=self.T("online_retract_hint"), bg=BG, fg=FG, justify="left", wraplength=720).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        b3, i3 = step_box(root, self.T("online_step3_title"))
        b3.grid(row=row, column=0, sticky="we", pady=(0, 10))
        row += 1

        opts = tk.Frame(i3, bg=BG)
        opts.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opts, text=self.T("online_autoload"), variable=var_autoload).pack(side="left")
        ttk.Checkbutton(opts, text=self.T("online_rgb"), variable=var_rgb).pack(side="left", padx=(12, 0))

        tk.Label(i3, text=self.T("online_opts_hint"), bg=BG, fg=FG, justify="left", wraplength=720).grid(row=1, column=0, sticky="w", pady=(8, 0))

        btns = tk.Frame(root, bg=BG)
        btns.grid(row=row, column=0, sticky="we", pady=(6, 0))
        btns.columnconfigure(0, weight=1)
        tk.Button(btns, text=self.T("cancel"), command=w.destroy, bg=ENTRY_BG, fg=FG, relief="flat",
                  highlightthickness=1, highlightbackground=BORDER, padx=18, pady=8).grid(row=0, column=1, sticky="e")
        tk.Button(btns, text=self.T("select"), command=_pick, bg=ENTRY_BG, fg=FG, relief="flat",
                  highlightthickness=1, highlightbackground=BORDER, padx=18, pady=8).grid(row=0, column=2, sticky="e", padx=(10, 0))

    def _help_link(self, parent, label: str, url: str):
        row = tk.Frame(parent, bg=BG)
        tk.Label(row, text=label, bg=BG, fg=FG).pack(side="left")
        l = tk.Label(row, text=url, bg=BG, fg=LINK_FG, cursor="hand2")
        l.pack(side="left", padx=6)
        l.bind("<Button-1>", lambda _e: _open_url(url))
        return row

    def _help(self):
        if self._help_win and self._help_win.winfo_exists():
            self._help_win.lift()
            return

        w = tk.Toplevel(self)
        self._help_win = w
        w.title(f"{self.T('help')} - {APP_NAME}")
        w.configure(bg=BG)
        w.minsize(860, 600)
        w.transient(self)
        w.grab_set()

        root = tk.Frame(w, bg=BG)
        root.pack(fill="both", expand=True, padx=16, pady=16)
        root.columnconfigure(0, weight=1, uniform="cols")
        root.columnconfigure(1, weight=1, uniform="cols")

        tk.Label(root, text=self.T("help"), bg=BG, fg=FG, font=("TkDefaultFont", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        warn = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=WARN_RED)
        warn.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12))
        tk.Label(warn, text=self.T("warn_title"), bg=BG, fg=WARN_RED, font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(warn, text=self.T("warn_text"), bg=BG, fg=FG, justify="left", wraplength=820).pack(anchor="w", padx=12, pady=(0, 10))

        left = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        right = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=2, column=1, sticky="nsew", padx=(10, 0))
        root.rowconfigure(2, weight=1)

        tk.Label(left, text=self.T("help_usb_title"), bg=BG, fg=FG, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(left, text=self.T("help_step1"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
        tk.Label(left, text=self.T("help_usb_s1"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)
        tk.Label(left, text=self.T("help_step2"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(left, text=self.T("help_usb_s2"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)
        tk.Label(left, text=self.T("help_step3"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(left, text=self.T("help_usb_s3"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        tk.Label(right, text=self.T("help_ttl_title"), bg=BG, fg=FG, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(right, text=self.T("help_step1"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
        tk.Label(right, text=self.T("help_ttl_s1"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        tk.Label(right, text=self.T("help_wiring"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        table = tk.Frame(right, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        table.pack(fill="x", padx=12, pady=(0, 10))

        def cell(r, c, text, bold=False):
            f = ("TkDefaultFont", 10, "bold") if bold else ("TkDefaultFont", 10)
            l = tk.Label(table, text=text, bg=BG, fg=FG, font=f, anchor="w", padx=8, pady=4)
            l.grid(row=r, column=c, sticky="we")
            return l

        table.columnconfigure(0, weight=1)
        table.columnconfigure(1, weight=1)
        cell(0, 0, self.T("help_tbl_bmcu"), bold=True)
        cell(0, 1, self.T("help_tbl_usbserial"), bold=True)
        cell(1, 0, "R");   cell(1, 1, "TXD")
        cell(2, 0, "T");   cell(2, 1, "RXD")
        cell(3, 0, "3V3"); cell(3, 1, "3V3")
        cell(4, 0, "GND"); cell(4, 1, "GND")

        tk.Label(right, text=self.T("help_bootloader"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(4, 2))
        tk.Label(right, text=self.T("help_boot_steps"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        tk.Label(right, text=self.T("help_step2"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(right, text=self.T("help_ttl_s2"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        tk.Label(right, text=self.T("help_step3"), bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(right, text=self.T("help_ttl_s3"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        drivers = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        drivers.grid(row=3, column=0, columnspan=2, sticky="we", pady=(14, 0))
        drivers.columnconfigure(0, weight=1)

        tk.Label(drivers, text=self.T("help_drivers_title"), bg=BG, fg=FG, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(drivers, text=self.T("help_drivers_ch340"), bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        l = tk.Label(drivers, text=WCH_CH341SER_URL, bg=BG, fg=LINK_FG, cursor="hand2")
        l.pack(anchor="w", padx=36, pady=(0, 10))
        l.bind("<Button-1>", lambda _e: _open_url(WCH_CH341SER_URL))

        bottom = tk.Frame(root, bg=BG)
        bottom.grid(row=4, column=0, columnspan=2, sticky="we", pady=(14, 0))
        bottom.columnconfigure(0, weight=1)

        links = tk.Frame(bottom, bg=BG)
        links.pack(anchor="w")
        self._help_link(links, self.T("help_link_fw"), FW_URL).pack(anchor="w", pady=2)
        self._help_link(links, self.T("help_link_app"), APP_URL).pack(anchor="w", pady=2)

        tk.Label(bottom, text=f"{APP_NAME} v{APP_VERSION} - {self.T('author_label')}", bg=BG, fg=FG).pack(anchor="w", pady=(10, 0))

        btns = tk.Frame(bottom, bg=BG)
        btns.pack(fill="x", pady=(12, 0))
        btns.columnconfigure(0, weight=1)
        tk.Button(
            btns,
            text=self.T("ok"),
            command=w.destroy,
            bg=ENTRY_BG,
            fg=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=18,
            pady=8,
        ).grid(row=0, column=1, sticky="e")

    def _start(self):
        if self.worker and self.worker.is_alive():
            return

        fw_source = (self.var_fw_source.get() or "").strip() or "local"

        if fw_source == "online":
            if not self._online_sel or not self._online_sel.get("rel_path"):
                messagebox.showerror(APP_NAME, self.T("err_select_online"))
                return
        else:
            fw = (self.var_fw.get() or "").strip()
            if not fw or not os.path.isfile(fw):
                messagebox.showerror(APP_NAME, self.T("err_select_bin"))
                return
            if not fw.lower().endswith(".bin"):
                messagebox.showerror(APP_NAME, self.T("err_bin_ext"))
                return

        port = (self.var_port.get() or "").strip()
        if not port:
            messagebox.showerror(APP_NAME, self.T("err_select_port"))
            return

        try:
            baud = int(self.var_baud.get())
            fast_baud = int(self.var_fast_baud.get())
        except Exception:
            messagebox.showerror(APP_NAME, self.T("err_bad_baud"))
            return

        self.cfg["mode"] = self.var_mode.get()
        self.cfg["port"] = port
        self.cfg["vid"] = (self.var_vid.get() or "").strip()
        self.cfg["pid"] = (self.var_pid.get() or "").strip()
        self.cfg["baud"] = baud
        self.cfg["fast_baud"] = fast_baud
        self.cfg["no_fast"] = bool(self.var_no_fast.get())
        self.cfg["verify"] = bool(self.var_verify.get())
        self.cfg["fw_source"] = fw_source
        self.cfg["adapter"] = (self.var_adapter.get() or "").strip()

        if fw_source == "local":
            fw = (self.var_fw.get() or "").strip()
            self.cfg["fw_path"] = fw
            self.cfg["fw_dir"] = os.path.dirname(fw)
            self.cfg.pop("online_sel", None)
        else:
            self.cfg["online_sel"] = self._online_sel or {}

        _cfg_save(self.cfg)

        self.btn_flash.config(state="disabled")
        self.pbar["value"] = 0
        self.worker = threading.Thread(target=self._run_flash, daemon=True)
        self.worker.start()

    def _run_flash(self):
        try:
            mode = self.var_mode.get()
            port = (self.var_port.get() or "").strip()
            fw_source = (self.var_fw_source.get() or "").strip() or "local"

            baud = int(self.var_baud.get())
            fast_baud = int(self.var_fast_baud.get())
            no_fast = bool(self.var_no_fast.get())
            verify = bool(self.var_verify.get())

            vid = int(self.var_vid.get(), 0)
            pid = int(self.var_pid.get(), 0)

            fw_path = ""
            online_cache_path = ""

            if fw_source == "online":
                sel = self._online_sel or {}
                rel = (sel.get("rel_path") or "").strip()
                if not rel:
                    raise RuntimeError("online selection missing rel_path")

                self._enqueue_log("INFO", f"online: selected {rel}")
                self._enqueue_progress(0, 0, 0)

                fw_path, ver = bmcu_flasher.remote_download_firmware(
                    rel_path=rel,
                    cache_dir=_cache_dir(),
                    manifest_url=bmcu_flasher.REMOTE_MANIFEST_URL,
                    firmware_base_url=bmcu_flasher.REMOTE_FIRMWARE_BASE,
                    version_url=bmcu_flasher.REMOTE_VERSION_URL,
                    timeout_s=40.0,
                    log_cb=self._enqueue_log,
                    progress_cb=lambda pct, _d, _t: self._enqueue_progress(pct, 0, 0),
                )
                online_cache_path = fw_path

                self._enqueue_log("INFO", f"online: using {ver} ({os.path.basename(fw_path)})")
                self._enqueue_progress(0, 0, 0)
            else:
                fw_path = (self.var_fw.get() or "").strip()

            self._enqueue_log("INFO", f"open port={port} mode={mode}")

            bmcu_flasher.flash_firmware(
                firmware_path=fw_path,
                mode=mode,
                port=port,
                vid=vid,
                pid=pid,
                flash_kb=64,
                baud=baud,
                fast_baud=fast_baud,
                no_fast=no_fast,
                verify=verify,
                verify_every=1,
                verify_last=True,
                seed_len=0x1E,
                seed_random=False,
                parity="N",
                trace=False,
                log_cb=self._enqueue_log,
                progress_cb=self._enqueue_progress,
            )

            if online_cache_path:
                try:
                    if os.path.isfile(online_cache_path):
                        os.remove(online_cache_path)
                        self._enqueue_log("INFO", f"online: cache removed ({os.path.basename(online_cache_path)})")
                except Exception as e:
                    self._enqueue_log("WARN", f"online: cache remove failed: {e}")

            self.q.put(("done", True, ""))

        except Exception as e:
            self._enqueue_log("ERROR", str(e))
            self.q.put(("done", False, str(e)))

if __name__ == "__main__":
    App().mainloop()
