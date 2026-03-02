#!/usr/bin/env python3
import os
import sys
import json
import threading
import queue
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bmcu_flasher

APP_NAME = "BMCU Flasher"
APP_VERSION = "1.0"

FW_URL = "https://github.com/jarczakpawel/BMCU-C-PJARCZAK"
APP_URL = "https://github.com/jarczakpawel/BMCU-Flasher"

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

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.minsize(940, 720)
        self.configure(bg=BG)

        self.q = queue.Queue()
        self.worker = None

        self.cfg = _cfg_load()

        self.var_mode = tk.StringVar(value=self.cfg.get("mode", "usb"))
        self.var_vid = tk.StringVar(value=self.cfg.get("vid", "0x1a86"))
        self.var_pid = tk.StringVar(value=self.cfg.get("pid", "0x7523"))

        self.var_port = tk.StringVar(value=self.cfg.get("port", ""))
        self.var_port_disp = tk.StringVar(value="")

        self.var_fw = tk.StringVar(value=self.cfg.get("fw_path", ""))
        self.var_baud = tk.StringVar(value=str(self.cfg.get("baud", 115200)))
        self.var_fast_baud = tk.StringVar(value=str(self.cfg.get("fast_baud", 1000000)))
        self.var_no_fast = tk.BooleanVar(value=bool(self.cfg.get("no_fast", False)))
        self.var_verify = tk.BooleanVar(value=bool(self.cfg.get("verify", True)))

        self._port_map = {}
        self._help_win = None

        self._style()
        self._build_ui()
        self._refresh_ports()

        self.after(50, self._drain_events)

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
        style.configure("TButton", padding=6)
        style.configure("TSeparator", background=BORDER)

        style.configure("TRadiobutton", background=BG, foreground=FG)
        style.map("TRadiobutton",
                  background=[("active", BG), ("pressed", BG), ("selected", BG)],
                  foreground=[("active", FG), ("pressed", FG), ("selected", FG)])

        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton",
                  background=[("active", BG), ("pressed", BG), ("selected", BG)],
                  foreground=[("active", FG), ("pressed", FG), ("selected", FG)])

        style.configure("TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG, foreground=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", ENTRY_BG), ("active", ENTRY_BG)],
                  foreground=[("readonly", FG), ("active", FG)],
                  background=[("readonly", ENTRY_BG), ("active", ENTRY_BG)],
                  selectbackground=[("readonly", ENTRY_BG)],
                  selectforeground=[("readonly", FG)])

        style.configure("Treeview", background=TREE_BG, fieldbackground=TREE_BG, foreground=FG, rowheight=22, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("Treeview.Heading", background=HDR_BG, foreground=FG, relief="flat")
        style.map("Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", FG)])
        style.map("Treeview.Heading",
                  background=[("active", HDR_BG), ("pressed", HDR_BG)],
                  foreground=[("active", FG), ("pressed", FG)])

        style.configure("Green.Horizontal.TProgressbar", troughcolor=PB_TROUGH, background=PB_GREEN, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)

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

    def _hlabel(self, parent, text, bold=False):
        f = ("TkDefaultFont", 11, "bold") if bold else ("TkDefaultFont", 11)
        return tk.Label(parent, text=text, bg=BG, fg=FG, font=f)

    def _help_link(self, parent, label: str, url: str):
        row = tk.Frame(parent, bg=BG)
        tk.Label(row, text=label, bg=BG, fg=FG).pack(side="left")
        tk.Label(row, text=url, bg=BG, fg=LINK_FG, cursor="hand2").pack(side="left", padx=6)
        row.winfo_children()[-1].bind("<Button-1>", lambda _e: _open_url(url))
        return row

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="we")

        self._img = None
        img_path = os.path.join(SCRIPT_DIR, "bmcu320.png")
        if os.path.isfile(img_path):
            try:
                self._img = tk.PhotoImage(file=img_path)
            except Exception:
                self._img = None

        self._icon = None
        icon_path = os.path.join(SCRIPT_DIR, "icon.png")
        if os.path.isfile(icon_path):
            try:
                self._icon = tk.PhotoImage(file=icon_path)
                self.iconphoto(True, self._icon)
            except Exception:
                pass

        if self._img:
            ttk.Label(header, image=self._img).pack(anchor="center", pady=(0, 6))
        ttk.Label(header, text=APP_NAME, font=("TkDefaultFont", 16, "bold")).pack(anchor="center", pady=(0, 10))

        cfg = ttk.Frame(root)
        cfg.grid(row=1, column=0, sticky="we", pady=(0, 8))
        for c in range(7):
            cfg.columnconfigure(c, weight=1 if c in (1, 3) else 0)

        r = 0
        ttk.Label(cfg, text="Mode:").grid(row=r, column=0, sticky="w")
        ttk.Radiobutton(cfg, text="USB (AutoDI)", variable=self.var_mode, value="usb", command=self._refresh_ports).grid(row=r, column=1, sticky="w")
        ttk.Radiobutton(cfg, text="TTL (manual BOOT+RESET)", variable=self.var_mode, value="ttl", command=self._refresh_ports).grid(row=r, column=2, sticky="w")
        r += 1

        ttk.Label(cfg, text="VID:").grid(row=r, column=0, sticky="w")
        self._entry(cfg, self.var_vid, width=12).grid(row=r, column=1, sticky="w")
        ttk.Label(cfg, text="PID:").grid(row=r, column=2, sticky="w")
        self._entry(cfg, self.var_pid, width=12).grid(row=r, column=3, sticky="w")
        ttk.Button(cfg, text="Refresh", command=self._refresh_ports).grid(row=r, column=4, padx=8, sticky="w")
        r += 1

        ttk.Label(cfg, text="Port:").grid(row=r, column=0, sticky="w")
        self.cmb_ports = ttk.Combobox(cfg, textvariable=self.var_port_disp, width=72, state="readonly")
        self.cmb_ports.grid(row=r, column=1, columnspan=4, sticky="we")
        self.cmb_ports.bind("<<ComboboxSelected>>", self._on_port_selected)
        r += 1

        ttk.Label(cfg, text="Firmware (.bin):").grid(row=r, column=0, sticky="w")
        self._entry(cfg, self.var_fw).grid(row=r, column=1, columnspan=3, sticky="we")
        ttk.Button(cfg, text="Browse", command=self._browse_fw).grid(row=r, column=4, padx=8, sticky="w")
        r += 1

        link_row = ttk.Frame(root)
        link_row.grid(row=2, column=0, sticky="we", pady=(0, 10))
        link_row.columnconfigure(0, weight=1)
        ttk.Label(link_row, text="Latest BMCU firmware:").grid(row=0, column=0, sticky="w")
        self._link_label(link_row, FW_URL, FW_URL).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Separator(root).grid(row=3, column=0, sticky="we", pady=10)

        opt = ttk.Frame(root)
        opt.grid(row=4, column=0, sticky="we")
        for c in range(10):
            opt.columnconfigure(c, weight=0)

        r = 0
        ttk.Label(opt, text="Baud:").grid(row=r, column=0, sticky="w")
        self._entry(opt, self.var_baud, width=10).grid(row=r, column=1, sticky="w", padx=(6, 12))
        ttk.Label(opt, text="Fast baud:").grid(row=r, column=2, sticky="w")
        self._entry(opt, self.var_fast_baud, width=10).grid(row=r, column=3, sticky="w", padx=(6, 12))
        ttk.Checkbutton(opt, text="No fast", variable=self.var_no_fast).grid(row=r, column=4, sticky="w", padx=(0, 12))
        ttk.Checkbutton(opt, text="Verify", variable=self.var_verify).grid(row=r, column=5, sticky="w")
        r += 1

        act = ttk.Frame(root)
        act.grid(row=5, column=0, sticky="we", pady=(10, 8))
        act.columnconfigure(0, weight=0)
        act.columnconfigure(1, weight=0)
        act.columnconfigure(2, weight=0)
        act.columnconfigure(3, weight=1)
        act.columnconfigure(4, weight=0)

        left = ttk.Frame(act)
        left.grid(row=0, column=0, columnspan=3, sticky="w")

        self.btn_flash = ttk.Button(left, text="Flash", command=self._start)
        self.btn_flash.pack(side="left")
        ttk.Button(left, text="Clear log", command=self._clear_log).pack(side="left", padx=8)
        ttk.Button(left, text="Copy log", command=self._copy_all_log).pack(side="left")

        ttk.Button(act, text="Help", command=self._help).grid(row=0, column=4, sticky="e")

        self.pbar = ttk.Progressbar(root, mode="determinate", maximum=100, style="Green.Horizontal.TProgressbar")
        self.pbar.grid(row=6, column=0, sticky="we", pady=(0, 10))

        logf = ttk.Frame(root)
        logf.grid(row=7, column=0, sticky="nsew")
        root.rowconfigure(7, weight=1)

        cols = ("time", "level", "msg")
        self.tree = ttk.Treeview(logf, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("time", text="Time")
        self.tree.heading("level", text="Level")
        self.tree.heading("msg", text="Message")
        self.tree.column("time", width=90, anchor="w", stretch=False)
        self.tree.column("level", width=80, anchor="w", stretch=False)
        self.tree.column("msg", width=720, anchor="w", stretch=True)

        vsb = ttk.Scrollbar(logf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)

        footer = ttk.Frame(root)
        footer.grid(row=8, column=0, sticky="we", pady=(10, 0))
        ttk.Label(footer, text=f"{APP_NAME} v{APP_VERSION}").pack(side="left")
        ttk.Label(footer, text=" - ").pack(side="left")
        self._link_label(footer, APP_URL, APP_URL).pack(side="left")

        self._menu = tk.Menu(self, tearoff=0, bg=HDR_BG, fg=FG, activebackground=SEL_BG, activeforeground=FG)
        self._menu.add_command(label="Copy selected", command=self._copy_selected_log)
        self._menu.add_command(label="Copy all", command=self._copy_all_log)
        self.tree.bind("<Button-3>", self._on_tree_menu)
        self.tree.bind("<Control-c>", lambda _e: self._copy_selected_log())
        self.tree.bind("<Control-C>", lambda _e: self._copy_selected_log())
        self.bind_all("<Control-Shift-c>", lambda _e: self._copy_all_log())
        self.bind_all("<Control-Shift-C>", lambda _e: self._copy_all_log())

    def _help(self):
        if self._help_win and self._help_win.winfo_exists():
            self._help_win.lift()
            return

        w = tk.Toplevel(self)
        self._help_win = w
        w.title(f"Help - {APP_NAME}")
        w.configure(bg=BG)
        w.minsize(820, 520)
        w.transient(self)
        w.grab_set()

        root = tk.Frame(w, bg=BG)
        root.pack(fill="both", expand=True, padx=16, pady=16)
        root.columnconfigure(0, weight=1, uniform="cols")
        root.columnconfigure(1, weight=1, uniform="cols")

        tk.Label(root, text="Help", bg=BG, fg=FG, font=("TkDefaultFont", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        left = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        right = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        root.rowconfigure(1, weight=1)

        # LEFT: USB
        tk.Label(left, text="USB mode", bg=BG, fg=FG, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(left, text="Choose USB mode if:", bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(6, 2))
        tk.Label(left, text="- You have a USB connector\n- You do NOT see buttons \"B\" and \"R\"", bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        tk.Label(left, text="Steps:", bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        tk.Label(left, text="- Plug BMCU via USB\n- Select port\n- Select firmware .bin\n- Click Flash", bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        # RIGHT: TTL
        tk.Label(right, text="TTL mode", bg=BG, fg=FG, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(right, text="Choose TTL mode if:", bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(6, 2))
        tk.Label(right, text="- You see pin header on the board\n- OR you have USB + buttons \"B\" and \"R\"", bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)

        tk.Label(right, text="You need USB-Serial adapter (CH340 recommended).", bg=BG, fg=FG, justify="left").pack(anchor="w", padx=12, pady=(10, 6))

        tk.Label(right, text="Wiring:", bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(6, 4))

        table = tk.Frame(right, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        table.pack(fill="x", padx=12, pady=(0, 10))

        def cell(r, c, text, bold=False):
            f = ("TkDefaultFont", 10, "bold") if bold else ("TkDefaultFont", 10)
            l = tk.Label(table, text=text, bg=BG, fg=FG, font=f, anchor="w", padx=8, pady=4)
            l.grid(row=r, column=c, sticky="we")
            return l

        table.columnconfigure(0, weight=1)
        table.columnconfigure(1, weight=1)

        cell(0, 0, "BMCU", bold=True)
        cell(0, 1, "USB-Serial (CH340)", bold=True)
        cell(1, 0, "R")
        cell(1, 1, "TXD")
        cell(2, 0, "T")
        cell(2, 1, "RXD")
        cell(3, 0, "3V3")
        cell(3, 1, "3V3")
        cell(4, 0, "GND")
        cell(4, 1, "GND")

        tk.Label(right, text="Enter bootloader:", bg=BG, fg=FG, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(4, 2))
        tk.Label(right, text="1) Hold BOOT\n2) Tap RESET\n3) Release BOOT", bg=BG, fg=FG, justify="left").pack(anchor="w", padx=24)
        
        drivers = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        drivers.grid(row=2, column=0, columnspan=2, sticky="we", pady=(14, 0))
        drivers.columnconfigure(0, weight=1)

        tk.Label(drivers, text="Drivers (CH340)", bg=BG, fg=FG, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(
            drivers,
            text="- CH340 drivers are in \"drivers\" folder (Windows/macOS/Linux if needed)",
            bg=BG,
            fg=FG,
            justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        # Links + footer
        bottom = tk.Frame(root, bg=BG)
        bottom.grid(row=3, column=0, columnspan=2, sticky="we", pady=(14, 0))
        bottom.columnconfigure(0, weight=1)

        links = tk.Frame(bottom, bg=BG)
        links.pack(anchor="w")

        self._help_link(links, "BMCU firmware:", FW_URL).pack(anchor="w", pady=2)
        self._help_link(links, "BMCU Flasher:", APP_URL).pack(anchor="w", pady=2)

        tk.Label(bottom, text=f"{APP_NAME} v{APP_VERSION}", bg=BG, fg=FG).pack(anchor="w", pady=(10, 0))

        btns = tk.Frame(bottom, bg=BG)
        btns.pack(fill="x", pady=(12, 0))
        btns.columnconfigure(0, weight=1)
        tk.Button(btns, text="OK", command=w.destroy, bg=ENTRY_BG, fg=FG, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=18, pady=8).grid(row=0, column=1, sticky="e")

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

    def _browse_fw(self):
        initdir = self.cfg.get("fw_dir", "")
        if initdir and not os.path.isdir(initdir):
            initdir = ""
        p = filedialog.askopenfilename(
            title="Select firmware .bin",
            initialdir=initdir or None,
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
        )
        if p:
            self.var_fw.set(p)
            d = os.path.dirname(p)
            self.cfg["fw_dir"] = d
            self.cfg["fw_path"] = p
            _cfg_save(self.cfg)

    def _clear_log(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def _enqueue_log(self, level: str, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.q.put(("log", ts, level, msg))

    def _enqueue_progress(self, pct: int, done: int, total: int):
        self.q.put(("prog", int(pct)))

    def _drain_events(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if not ev:
                    continue
                if ev[0] == "log":
                    _, ts, level, msg = ev
                    self.tree.insert("", "end", values=(ts, level, msg))
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
                        messagebox.showinfo(APP_NAME, "OK")
                    else:
                        messagebox.showerror(APP_NAME, msg or "ERROR")
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

    def _refresh_ports(self):
        mode = self.var_mode.get()
        if mode == "usb":
            try:
                vid = int(self.var_vid.get(), 0)
                pid = int(self.var_pid.get(), 0)
            except Exception:
                self._enqueue_log("ERROR", "Bad VID/PID")
                return
            ports = bmcu_flasher.list_matching_ports(vid, pid)
            fallback = False
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

        pref = self.cfg.get("port", "") or self.var_port.get().strip()
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

        if mode == "ttl" and fallback:
            self._enqueue_log("INFO", f"ports: {len(items)} (mode=ttl, fallback=all)")
        else:
            self._enqueue_log("INFO", f"ports: {len(items)} (mode={mode})")

        self.cfg["mode"] = mode
        self.cfg["vid"] = self.var_vid.get().strip()
        self.cfg["pid"] = self.var_pid.get().strip()
        _cfg_save(self.cfg)

    def _start(self):
        if self.worker and self.worker.is_alive():
            return

        fw = self.var_fw.get().strip()
        if not fw or not os.path.isfile(fw):
            messagebox.showerror(APP_NAME, "Select firmware .bin")
            return
        if not fw.lower().endswith(".bin"):
            messagebox.showerror(APP_NAME, "Firmware must be .bin")
            return

        port = self.var_port.get().strip()
        if not port:
            messagebox.showerror(APP_NAME, "Select port")
            return

        try:
            baud = int(self.var_baud.get())
            fast_baud = int(self.var_fast_baud.get())
        except Exception:
            messagebox.showerror(APP_NAME, "Bad baud values")
            return

        self.cfg["fw_path"] = fw
        self.cfg["fw_dir"] = os.path.dirname(fw)
        self.cfg["mode"] = self.var_mode.get()
        self.cfg["port"] = port
        self.cfg["vid"] = self.var_vid.get().strip()
        self.cfg["pid"] = self.var_pid.get().strip()
        self.cfg["baud"] = baud
        self.cfg["fast_baud"] = fast_baud
        self.cfg["no_fast"] = bool(self.var_no_fast.get())
        self.cfg["verify"] = bool(self.var_verify.get())
        _cfg_save(self.cfg)

        self.btn_flash.config(state="disabled")
        self.pbar["value"] = 0
        self.worker = threading.Thread(target=self._run_flash, daemon=True)
        self.worker.start()

    def _run_flash(self):
        try:
            mode = self.var_mode.get()
            port = self.var_port.get().strip()
            fw = self.var_fw.get().strip()

            baud = int(self.var_baud.get())
            fast_baud = int(self.var_fast_baud.get())
            no_fast = bool(self.var_no_fast.get())
            verify = bool(self.var_verify.get())

            vid = int(self.var_vid.get(), 0)
            pid = int(self.var_pid.get(), 0)

            self._enqueue_log("INFO", f"open port={port} mode={mode}")

            bmcu_flasher.flash_firmware(
                firmware_path=fw,
                mode=mode,
                port=port,
                vid=vid,
                pid=pid,
                flash_kb=128,
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

            self.q.put(("done", True, ""))
        except Exception as e:
            self._enqueue_log("ERROR", str(e))
            self.q.put(("done", False, str(e)))

if __name__ == "__main__":
    App().mainloop()
