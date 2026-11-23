"""
Vortexs Clips - interfaz
Saca las rondas en las que haces 3 kills o más de tus partidas de competitivo
y las recorta del VOD de Twitch en un solo vídeo.
"""
import json
import os
import subprocess
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import core
import twitch
import updater
from version import VERSION

APP_NAME = "Vortexs Clips"
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "VortexsClips")
CFG_FILE = os.path.join(CFG_DIR, "config.json")

# Colores del canal
BG = "#0d0b10"
PANEL = "#17141d"
FIELD = "#221e2a"
TEXT = "#f2eef8"
MUTED = "#9a91a8"
YELLOW = "#ffd21f"
PURPLE = "#c7a6ff"

REGIONS = ["eu", "na", "ap", "kr", "latam", "br"]
ENCODERS = {"CPU (x264, compatible siempre)": "x264",
            "NVIDIA (NVENC, rápido)": "nvenc",
            "Intel (QuickSync)": "qsv",
            "AMD (AMF)": "amf"}


def load_cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("1180x820")
        self.minsize(1000, 700)
        if os.name == "nt":
            try:
                self.state("zoomed")   # abrir maximizada
            except tk.TclError:
                pass
        self.configure(bg=BG)
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.isfile(ico):
            try:
                self.iconbitmap(ico)
            except Exception:
                pass

        self.cfg = load_cfg()
        self.matches: dict[str, core.Match] = {}
        self.checked: set[str] = set()
        self.calib: dict = self.cfg.get("calib", {})
        self.vod_info = None
        self.clips: list[core.Clip] = []
        self.next_start = 0
        self.stop_flag = False
        self.busy = False

        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(1500, lambda: self.check_update(silent=True))

        if not core.find_tool("ffmpeg"):
            self.after(400, lambda: messagebox.showwarning(
                APP_NAME, "No encuentro ffmpeg.\n\nPon la carpeta de ffmpeg junto al programa "
                          "(ffmpeg\\bin\\ffmpeg.exe) o instálalo con:\n\nwinget install Gyan.FFmpeg"))

    # ------------------------------------------------------------ estilo
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD,
                    bordercolor=PANEL, font=("Segoe UI", 10))
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=PANEL, foreground=TEXT)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=BG, foreground=YELLOW, font=("Segoe UI Black", 20))
        s.configure("Sub.TLabel", background=BG, foreground=PURPLE, font=("Segoe UI", 10))
        s.configure("H.TLabel", background=PANEL, foreground=PURPLE, font=("Segoe UI Semibold", 11))
        s.configure("Status.TLabel", background=BG, foreground=MUTED)
        s.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT)
        s.configure("TCombobox", fieldbackground=FIELD, foreground=TEXT, background=FIELD,
                    arrowcolor=YELLOW)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        s.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT, arrowcolor=YELLOW)
        s.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        s.map("TCheckbutton", background=[("active", PANEL)], indicatorcolor=[("selected", YELLOW)])
        s.configure("TButton", background=FIELD, foreground=TEXT, borderwidth=0, padding=(12, 6))
        s.map("TButton", background=[("active", "#2e2838"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=YELLOW, foreground="#111",
                    font=("Segoe UI Semibold", 10))
        s.map("Accent.TButton", background=[("active", "#ffe066"), ("disabled", "#5c5320")])
        s.configure("Purple.TButton", background=PURPLE, foreground="#111")
        s.map("Purple.TButton", background=[("active", "#dcc7ff")])
        s.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                    rowheight=26, borderwidth=0)
        s.configure("Treeview.Heading", background=PANEL, foreground=PURPLE,
                    font=("Segoe UI Semibold", 9), borderwidth=0)
        s.map("Treeview", background=[("selected", "#3a3150")], foreground=[("selected", YELLOW)])
        s.configure("Horizontal.TProgressbar", background=YELLOW, troughcolor=FIELD,
                    bordercolor=FIELD, lightcolor=YELLOW, darkcolor=YELLOW, thickness=22)
        s.configure("Step.TLabel", background=PANEL, foreground=YELLOW, font=("Segoe UI Semibold", 12))
        s.configure("Pct.TLabel", background=PANEL, foreground=YELLOW, font=("Segoe UI Black", 16))
        s.configure("Detail.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("ChipOff.TLabel", background=FIELD, foreground=MUTED, font=("Segoe UI", 9), padding=(10, 3))
        s.configure("ChipOn.TLabel", background=YELLOW, foreground="#111", font=("Segoe UI Semibold", 9), padding=(10, 3))
        s.configure("ChipDone.TLabel", background="#3a3150", foreground=PURPLE, font=("Segoe UI", 9), padding=(10, 3))

    def _panel(self, parent, title):
        f = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        ttk.Label(f, text=title, style="H.TLabel").grid(row=0, column=0, columnspan=10, sticky="w",
                                                        pady=(0, 8))
        return f

    # ------------------------------------------------------------ UI
    def _build(self):
        head = ttk.Frame(self, padding=(16, 12, 16, 4))
        head.pack(fill="x")
        ttk.Label(head, text="VORTEXS CLIPS", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="   Tus rondas de 3K, 4K y ACE del VOD, en un solo vídeo",
                  style="Sub.TLabel").pack(side="left", pady=(8, 0))
        ttk.Button(head, text="Buscar actualizaciones",
                   command=lambda: self.check_update(silent=False)).pack(side="right")
        ttk.Label(head, text=f"v{VERSION}  ", style="Sub.TLabel").pack(side="right")

        top = ttk.Frame(self, padding=(16, 4))
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        # --- 1. cuenta
        p1 = self._panel(top, "1 · Cuenta y partidas")
        p1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.v_riot = tk.StringVar(value=self.cfg.get("riot_id", "Vortexs#vrtxs"))
        self.v_region = tk.StringVar(value=self.cfg.get("region", "eu"))
        self.v_key = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.v_link = tk.StringVar()
        ttk.Label(p1, text="Riot ID").grid(row=1, column=0, sticky="w")
        ttk.Entry(p1, textvariable=self.v_riot, width=22).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(p1, text="Región").grid(row=1, column=2, sticky="w")
        ttk.Combobox(p1, textvariable=self.v_region, values=REGIONS, width=6,
                     state="readonly").grid(row=1, column=3, sticky="w", padx=6)
        ttk.Label(p1, text="Clave API").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(p1, textvariable=self.v_key, width=40, show="•").grid(
            row=2, column=1, columnspan=3, sticky="we", padx=6)
        b = ttk.Frame(p1, style="Panel.TFrame")
        b.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.btn_load = ttk.Button(b, text="Cargar partidas", style="Purple.TButton",
                                   command=self.load_matches)
        self.btn_load.pack(side="left")
        self.btn_more = ttk.Button(b, text="Cargar 10 más", command=lambda: self.load_matches(True))
        self.btn_more.pack(side="left", padx=6)
        ttk.Label(p1, text="o pega el enlace de una partida de tracker.gg").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(10, 2))
        lf = ttk.Frame(p1, style="Panel.TFrame")
        lf.grid(row=5, column=0, columnspan=4, sticky="we")
        ttk.Entry(lf, textvariable=self.v_link, width=44).pack(side="left", fill="x", expand=True)
        ttk.Button(lf, text="Añadir", command=self.add_by_link).pack(side="left", padx=6)

        # --- 2. VOD
        p2 = self._panel(top, "2 · VOD y ajustes")
        p2.grid(row=0, column=1, sticky="nsew")
        self.v_vod = tk.StringVar()
        self.v_start = tk.StringVar()
        self.v_delay = tk.StringVar(value=str(self.cfg.get("delay", 0)))
        self.v_min = tk.IntVar(value=self.cfg.get("min_kills", 3))
        self.v_skipbuy = tk.BooleanVar(value=self.cfg.get("skip_buy", True))
        self.v_pre = tk.StringVar(value=str(self.cfg.get("pre", 3)))
        self.v_post = tk.StringVar(value=str(self.cfg.get("post", 4)))
        enc_default = next((k for k, v in ENCODERS.items() if v == self.cfg.get("encoder")),
                           list(ENCODERS)[0])
        self.v_enc = tk.StringVar(value=enc_default)

        vf = ttk.Frame(p2, style="Panel.TFrame")
        vf.grid(row=1, column=0, columnspan=6, sticky="we")
        ttk.Button(vf, text="Elegir VOD…", style="Purple.TButton", command=self.pick_vod).pack(side="left")
        self.lbl_vod = ttk.Label(vf, text="Ningún vídeo elegido", style="Muted.TLabel")
        self.lbl_vod.pack(side="left", padx=8)

        ttk.Label(p2, text="Inicio del directo").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(p2, textvariable=self.v_start, width=19).grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(p2, text="dd/mm/aaaa hh:mm:ss (hora de tu PC)", style="Muted.TLabel").grid(
            row=2, column=2, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(p2, text="Delay / desfase (s)").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(p2, textvariable=self.v_delay, width=8).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(p2, text="Kills mínimas").grid(row=3, column=2, sticky="w")
        ttk.Spinbox(p2, from_=2, to=6, textvariable=self.v_min, width=4).grid(row=3, column=3, sticky="w", padx=6)

        ttk.Checkbutton(p2, text="Saltar fase de compra", variable=self.v_skipbuy).grid(
            row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(p2, text="Margen antes / después (s)").grid(row=4, column=2, sticky="w")
        mf = ttk.Frame(p2, style="Panel.TFrame")
        mf.grid(row=4, column=3, columnspan=3, sticky="w", padx=6)
        ttk.Entry(mf, textvariable=self.v_pre, width=4).pack(side="left")
        ttk.Entry(mf, textvariable=self.v_post, width=4).pack(side="left", padx=4)

        ttk.Label(p2, text="Codificar con").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Combobox(p2, textvariable=self.v_enc, values=list(ENCODERS), state="readonly",
                     width=30).grid(row=5, column=1, columnspan=2, sticky="w", padx=6)

        ttk.Separator(p2).grid(row=6, column=0, columnspan=6, sticky="we", pady=8)
        ttk.Label(p2, text="o pega el enlace del VOD de Twitch y lo hago todo solo:").grid(
            row=7, column=0, columnspan=6, sticky="w")
        self.v_twitch = tk.StringVar()
        self.v_outdir = tk.StringVar(value=self.cfg.get("out_dir") or os.path.join(
            os.path.expanduser("~"), "Videos", "Vortexs Clips"))
        self.v_auto = tk.BooleanVar(value=self.cfg.get("auto_process", True))
        tf = ttk.Frame(p2, style="Panel.TFrame")
        tf.grid(row=8, column=0, columnspan=6, sticky="we", pady=(4, 0))
        ttk.Entry(tf, textvariable=self.v_twitch, width=46).pack(side="left", fill="x", expand=True)
        self.btn_dl = ttk.Button(tf, text="Descargar VOD", style="Accent.TButton", command=self.download_vod)
        self.btn_dl.pack(side="left", padx=6)
        of = ttk.Frame(p2, style="Panel.TFrame")
        of.grid(row=9, column=0, columnspan=6, sticky="we", pady=(6, 0))
        ttk.Button(of, text="Carpeta…", command=self.pick_outdir).pack(side="left")
        self.lbl_outdir = ttk.Label(of, text=self.v_outdir.get(), style="Muted.TLabel")
        self.lbl_outdir.pack(side="left", padx=8)
        ttk.Checkbutton(p2, text="Al terminar la descarga, sacar las rondas y generar el vídeo",
                        variable=self.v_auto).grid(row=10, column=0, columnspan=6, sticky="w", pady=(6, 0))

        # --- 3. partidas
        mid = ttk.Frame(self, padding=(16, 8))
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)
        mid.rowconfigure(0, weight=1)

        p3 = self._panel(mid, "3 · Partidas (clic en ☐ para incluir)")
        p3.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        p3.rowconfigure(1, weight=1)
        p3.columnconfigure(0, weight=1)
        cols = ("use", "date", "map", "agent", "score", "hl", "invod", "sync")
        self.tv_m = ttk.Treeview(p3, columns=cols, show="headings", selectmode="browse", height=5)
        for c, t, w in zip(cols, ("", "Fecha", "Mapa", "Agente", "Resultado", "Rondas", "¿En el VOD?", "Sincro"),
                           (34, 130, 80, 80, 70, 60, 90, 120)):
            self.tv_m.heading(c, text=t)
            self.tv_m.column(c, width=w, anchor="center" if c != "date" else "w", stretch=c in ("date", "sync"))
        self.tv_m.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(p3, orient="vertical", command=self.tv_m.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tv_m.configure(yscrollcommand=sb.set)
        self.tv_m.bind("<Button-1>", self.on_match_click)
        self.tv_m.tag_configure("in", foreground=YELLOW)
        self.tv_m.tag_configure("out", foreground=MUTED)
        mb = ttk.Frame(p3, style="Panel.TFrame")
        mb.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(mb, text="Marcar las que están en el VOD", command=self.auto_check).pack(side="left")
        ttk.Button(mb, text="Sincronizar partida…", command=self.calibrate).pack(side="left", padx=6)
        ttk.Button(mb, text="Quitar sincro", command=self.clear_calib).pack(side="left")

        # --- 4. rondas
        p4 = self._panel(mid, "4 · Rondas que se van a cortar")
        p4.grid(row=0, column=1, sticky="nsew")
        p4.rowconfigure(1, weight=1)
        p4.columnconfigure(0, weight=1)
        cols = ("round", "from", "to", "dur")
        self.tv_c = ttk.Treeview(p4, columns=cols, show="headings", selectmode="extended", height=5)
        for c, t, w in zip(cols, ("Ronda", "Desde (VOD)", "Hasta", "Dur."), (190, 90, 80, 50)):
            self.tv_c.heading(c, text=t)
            self.tv_c.column(c, width=w, anchor="w" if c == "round" else "center", stretch=c == "round")
        self.tv_c.grid(row=1, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(p4, orient="vertical", command=self.tv_c.yview)
        sb2.grid(row=1, column=1, sticky="ns")
        self.tv_c.configure(yscrollcommand=sb2.set)
        self.tv_c.bind("<Double-1>", lambda e: self.preview())
        cb = ttk.Frame(p4, style="Panel.TFrame")
        cb.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(cb, text="Calcular rondas", style="Purple.TButton", command=self.compute).pack(side="left")
        ttk.Button(cb, text="Ver ronda", command=self.preview).pack(side="left", padx=6)
        ttk.Button(cb, text="Quitar", command=self.remove_clips).pack(side="left")

        # --- pie
        foot = ttk.Frame(self, style="Panel.TFrame", padding=(16, 10, 16, 12))
        # el panel de progreso se coloca primero abajo para que nunca quede cortado;
        # si falta sitio, encogen las listas del medio
        foot.pack(side="bottom", fill="x", padx=16, pady=(0, 14))
        mid.pack(fill="both", expand=True)
        chips = ttk.Frame(foot, style="Panel.TFrame")
        chips.pack(fill="x")
        self.step_names = ["1 · Descargar VOD", "2 · Buscar partidas", "3 · Sacar rondas", "4 · Generar vídeo"]
        self.chip_lbls = []
        for i, n in enumerate(self.step_names):
            lb = ttk.Label(chips, text=n, style="ChipOff.TLabel")
            lb.pack(side="left", padx=(0, 4))
            self.chip_lbls.append(lb)
            if i < len(self.step_names) - 1:
                ttk.Label(chips, text="›", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self.lbl_time = ttk.Label(chips, text="", style="Muted.TLabel")
        self.lbl_time.pack(side="right")
        top_row = ttk.Frame(foot, style="Panel.TFrame")
        top_row.pack(fill="x", pady=(8, 4))
        self.lbl_step = ttk.Label(top_row, text="Listo para empezar", style="Step.TLabel")
        self.lbl_step.pack(side="left")
        self.lbl_pct = ttk.Label(top_row, text="", style="Pct.TLabel")
        self.lbl_pct.pack(side="right")
        self.pb = ttk.Progressbar(foot, mode="determinate", maximum=1, style="Horizontal.TProgressbar")
        self.pb.pack(fill="x")
        row = ttk.Frame(foot, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        self.lbl_status = ttk.Label(row, text="Pega un enlace de Twitch o elige un VOD.", style="Detail.TLabel")
        self.lbl_status.pack(side="left")
        self.btn_render = ttk.Button(row, text="GENERAR VÍDEO", style="Accent.TButton", command=self.render)
        self.btn_render.pack(side="right")
        self.btn_cancel = ttk.Button(row, text="Cancelar", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="right", padx=6)

    # ------------------------------------------------------------ util
    def status(self, txt):
        if txt:
            self.lbl_status.configure(text=txt)

    def set_step(self, idx, title=None):
        """Marca el paso actual (0-3) o None para reposo."""
        import time
        for i, lb in enumerate(self.chip_lbls):
            st = "ChipOff.TLabel" if idx is None or i > idx else ("ChipOn.TLabel" if i == idx else "ChipDone.TLabel")
            lb.configure(style=st)
        if idx is not None:
            self.lbl_step.configure(text=title or self.step_names[idx][4:] + "…")
            self._t0 = time.time()
            if getattr(self, "_tick_id", None):
                self.after_cancel(self._tick_id)
            self._tick()
        else:
            self.lbl_step.configure(text=title or "Listo")
            self.lbl_time.configure(text="")
            self._t0 = None

    def _tick(self):
        import time
        if getattr(self, "_t0", None) is None:
            return
        el = int(time.time() - self._t0)
        self.lbl_time.configure(text=f"tiempo en este paso: {el // 60}:{el % 60:02d}")
        self._tick_id = self.after(1000, self._tick)

    def set_progress(self, frac, detail=None):
        """frac 0..1, o None = trabajando sin porcentaje (barra animada)."""
        if frac is None:
            if str(self.pb.cget("mode")) != "indeterminate":
                self.pb.configure(mode="indeterminate", maximum=100)
                self.pb.start(12)
            self.lbl_pct.configure(text="")
        else:
            if str(self.pb.cget("mode")) != "determinate":
                self.pb.stop()
                self.pb.configure(mode="determinate", maximum=1)
            self.pb.configure(value=frac)
            self.lbl_pct.configure(text=f"{frac * 100:.0f} %")
        self.status(detail)

    def progress_cb(self, step=None):
        """Devuelve una función segura para llamar desde otro hilo: f(frac, detalle)."""
        def f(frac, detail=None):
            self.after(0, lambda: self.set_progress(frac, detail))
        return f

    def finish_steps(self, ok_text):
        self.set_step(None, ok_text)
        self.set_progress(1)

    def settings(self) -> core.CutSettings:
        def f(v, d):
            try:
                return float(str(v.get()).replace(",", "."))
            except Exception:
                return d
        return core.CutSettings(min_kills=int(self.v_min.get() or 3),
                                skip_buy_phase=self.v_skipbuy.get(),
                                pre_roll_s=f(self.v_pre, 3), post_roll_s=f(self.v_post, 4),
                                global_adjust_s=f(self.v_delay, 0))

    def vod_start_utc(self):
        txt = self.v_start.get().strip()
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(txt, fmt).astimezone().astimezone(tz=None)
            except ValueError:
                continue
        raise ValueError("La hora de inicio del directo no tiene el formato dd/mm/aaaa hh:mm:ss")

    def persist(self):
        self.cfg.update({"riot_id": self.v_riot.get(), "region": self.v_region.get(),
                         "api_key": self.v_key.get(), "delay": self.v_delay.get(),
                         "min_kills": int(self.v_min.get() or 3), "skip_buy": self.v_skipbuy.get(),
                         "pre": self.v_pre.get(), "post": self.v_post.get(),
                         "encoder": ENCODERS[self.v_enc.get()],
                         "calib": self.calib, "out_dir": self.v_outdir.get(),
                         "auto_process": self.v_auto.get()})
        try:
            save_cfg(self.cfg)
        except Exception:
            pass

    def run_bg(self, work, done, err_title="Error"):
        if self.busy:
            return
        self.busy = True

        def target():
            try:
                res = work()
                self.after(0, lambda: (setattr(self, "busy", False), done(res)))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: (setattr(self, "busy", False), self.set_progress(0, "Error."),
                                       self.set_step(None, "Se ha parado por un error"),
                                       messagebox.showerror(err_title, msg)))
        threading.Thread(target=target, daemon=True).start()

    # ------------------------------------------------------------ partidas
    def load_matches(self, more=False):
        if not self.v_key.get().strip():
            messagebox.showinfo(APP_NAME, "Falta la clave de la API de HenrikDev (ver README).")
            return
        self.persist()
        start = self.next_start if more else 0
        riot, key, reg = self.v_riot.get(), self.v_key.get(), self.v_region.get()
        self.set_progress(None, "Descargando partidas de competitivo…")

        def work():
            raws = core.fetch_recent_matches(riot, key, reg, size=10, start=start)
            out = []
            for r in raws:
                try:
                    out.append(core.parse_match(r, riot))
                except Exception:
                    pass
            return out

        def done(ms):
            if not more:
                self.matches.clear()
                self.checked.clear()
                self.next_start = 0
            self.next_start += 10
            for m in ms:
                self.matches[m.match_id] = m
            self.refresh_matches()
            self.auto_check()
            self.set_progress(1, f"{len(self.matches)} partidas cargadas.")
        self.run_bg(work, done, "No se pudieron cargar las partidas")

    def add_by_link(self):
        link = self.v_link.get().strip()
        if not link:
            return
        if not self.v_key.get().strip():
            messagebox.showinfo(APP_NAME, "Falta la clave de la API de HenrikDev (ver README).")
            return
        riot, key, reg = self.v_riot.get(), self.v_key.get(), self.v_region.get()
        self.set_progress(None, "Descargando partida…")
        self.run_bg(lambda: core.parse_match(core.fetch_match(link, key, reg), riot),
                    lambda m: (self.matches.__setitem__(m.match_id, m), self.checked.add(m.match_id),
                               self.refresh_matches(), self.v_link.set(""),
                               self.set_progress(1, f"Añadida: {m.map_name}")),
                    "No se pudo añadir la partida")

    def refresh_matches(self):
        self.tv_m.delete(*self.tv_m.get_children())
        st = self.settings()
        try:
            vs = self.vod_start_utc() if self.vod_info else None
        except ValueError:
            vs = None
        for m in sorted(self.matches.values(), key=lambda x: x.started_at, reverse=True):
            local = m.started_at.astimezone()
            n = len(m.highlight_rounds(st.min_kills))
            if vs and self.vod_info:
                inv = core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib)
                invt = "Sí" if inv else "No"
            else:
                inv, invt = None, "—"
            sync = "Manual ✔" if m.match_id in self.calib else "Por hora"
            self.tv_m.insert("", "end", iid=m.match_id, tags=(("in",) if inv else ("out",) if inv is False else ()),
                             values=("☑" if m.match_id in self.checked else "☐",
                                     local.strftime("%d/%m %H:%M"), m.map_name, m.agent, m.score,
                                     n, invt, sync))

    def on_match_click(self, ev):
        if self.tv_m.identify_region(ev.x, ev.y) != "cell":
            return
        if self.tv_m.identify_column(ev.x) != "#1":
            return
        iid = self.tv_m.identify_row(ev.y)
        if not iid:
            return
        if iid in self.checked:
            self.checked.discard(iid)
        else:
            self.checked.add(iid)
        self.tv_m.set(iid, "use", "☑" if iid in self.checked else "☐")

    def auto_check(self):
        if not self.vod_info:
            return
        try:
            vs = self.vod_start_utc()
        except ValueError:
            return
        st = self.settings()
        self.checked = {m.match_id for m in self.matches.values()
                        if core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib)}
        self.refresh_matches()

    def selected_match(self):
        sel = self.tv_m.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Selecciona una partida de la lista.")
            return None
        return self.matches[sel[0]]

    def calibrate(self):
        m = self.selected_match()
        if not m:
            return
        first = m.first_my_kill_ms()
        if first is None:
            messagebox.showinfo(APP_NAME, "No tienes kills en esa partida.")
            return
        k = min((x for x in m.kills if x.killer == m.my_puuid), key=lambda x: x.t_match)
        guess = ""
        if self.vod_info:
            try:
                off = core.match_offset_s(m, self.vod_start_utc(), {}) + self.settings().global_adjust_s
                guess = core.fmt_ts(off + first / 1000)
            except ValueError:
                pass
        dlg = tk.Toplevel(self)
        dlg.title("Sincronizar partida")
        dlg.configure(bg=PANEL)
        dlg.transient(self)
        dlg.grab_set()
        fr = ttk.Frame(dlg, style="Panel.TFrame", padding=16)
        fr.pack(fill="both", expand=True)
        ttk.Label(fr, text=f"{m.map_name} · {m.agent} · {m.started_at.astimezone():%d/%m %H:%M}",
                  style="H.TLabel").pack(anchor="w")
        ttk.Label(fr, text=(f"Busca en el VOD tu PRIMERA kill de esta partida:\n"
                            f"ronda {k.round + 1}, con {k.weapon or 'arma desconocida'}.\n\n"
                            f"Escribe el momento exacto del VOD (h:mm:ss) en que sale en el killfeed.")
                  ).pack(anchor="w", pady=8)
        v = tk.StringVar(value=guess)
        e = ttk.Entry(fr, textvariable=v, width=16)
        e.pack(anchor="w")
        e.focus_set()
        if guess:
            ttk.Label(fr, text=f"Según la hora debería estar sobre {guess}", style="Muted.TLabel").pack(anchor="w", pady=4)
        bf = ttk.Frame(fr, style="Panel.TFrame")
        bf.pack(anchor="w", pady=(10, 0))

        def look():
            try:
                t = core.parse_ts(v.get())
            except ValueError:
                return
            self.play(max(0, t - 5), 12)

        def ok():
            try:
                t = core.parse_ts(v.get())
            except ValueError:
                messagebox.showerror(APP_NAME, "Formato no válido. Ejemplo: 1:23:45", parent=dlg)
                return
            self.calib[m.match_id] = {"first_kill_vod_s": t}
            self.persist()
            dlg.destroy()
            self.refresh_matches()
            if self.clips:
                self.compute()
        ttk.Button(bf, text="Ver ese momento", command=look).pack(side="left")
        ttk.Button(bf, text="Guardar", style="Accent.TButton", command=ok).pack(side="left", padx=6)
        dlg.bind("<Return>", lambda ev: ok())

    def clear_calib(self):
        m = self.selected_match()
        if m and m.match_id in self.calib:
            del self.calib[m.match_id]
            self.persist()
            self.refresh_matches()

    # ------------------------------------------------------------ VOD
    def pick_vod(self):
        p = filedialog.askopenfilename(title="Elige el VOD descargado",
                                       filetypes=[("Vídeo", "*.mp4 *.mkv *.mov *.ts *.flv"), ("Todo", "*.*")])
        if not p:
            return
        self.v_start.set("")
        self.set_progress(None, "Leyendo el vídeo…")
        self.run_bg(lambda: core.probe_vod(p), lambda info: self.set_vod(p, info),
                    "No se pudo leer el vídeo")

    def set_vod(self, p, info, start_utc=None):
        self.v_vod.set(p)
        self.vod_info = info
        self.clips = []
        self.fill_clips()
        d = info["duration"]
        self.lbl_vod.configure(text=f"{os.path.basename(p)}  ·  {core.fmt_ts(d).split('.')[0]}  ·  "
                                    f"{info.get('width')}x{info.get('height')}")
        if start_utc is not None:
            self.v_start.set(start_utc.astimezone().strftime("%d/%m/%Y %H:%M:%S"))
        elif not self.v_start.get().strip():
            self.v_start.set(info["start_guess"].astimezone().strftime("%d/%m/%Y %H:%M:%S"))
        self.set_progress(1, "VOD cargado. Revisa la hora de inicio y pulsa «Marcar las que están en el VOD».")
        self.auto_check()

    def pick_outdir(self):
        d = filedialog.askdirectory(title="Carpeta para los VODs y los vídeos",
                                    initialdir=self.v_outdir.get())
        if d:
            self.v_outdir.set(d)
            self.lbl_outdir.configure(text=d)
            self.persist()

    # ------------------------------------------------------------ Twitch
    def set_running(self, running):
        self.btn_render.configure(state="disabled" if running else "normal")
        self.btn_dl.configure(state="disabled" if running else "normal")
        self.btn_cancel.configure(state="normal" if running else "disabled")

    def download_vod(self):
        url = self.v_twitch.get().strip()
        if not twitch.is_twitch_vod(url):
            messagebox.showinfo(APP_NAME, "Pega un enlace de VOD de Twitch, por ejemplo:\n"
                                          "https://www.twitch.tv/videos/1234567890")
            return
        if not core.find_tool("ffmpeg"):
            messagebox.showerror(APP_NAME, "Falta ffmpeg. Ejecuta Instalar.bat.")
            return
        auto = self.v_auto.get()
        if auto and not self.v_key.get().strip():
            if not messagebox.askyesno(APP_NAME, "No has puesto la clave de la API, así que solo "
                                                 "descargaré el VOD (sin sacar las rondas).\n\n¿Seguir?"):
                return
            auto = False
        self.persist()
        out_dir = self.v_outdir.get()
        self.stop_flag = False
        self.set_running(True)
        self.set_step(0, "Descargando el VOD de Twitch…")
        self.set_progress(None, "Leyendo el VOD de Twitch…")
        prog = self.progress_cb()

        def work():
            try:
                info = twitch.vod_info(url)
                self.after(0, lambda: self.lbl_step.configure(
                    text=f"Descargando «{info['title'][:50]}» en {info['quality']}…"))
                prog(None, "Empezando la descarga…")
                path = twitch.find_existing(out_dir, info)
                if path:
                    prog(1, f"Ya estaba descargado: {os.path.basename(path)}")
                else:
                    path = twitch.download(url, out_dir, prog, lambda: self.stop_flag, info)
                prog(None, "Leyendo el vídeo descargado…")
                return info, path, core.probe_vod(path)
            finally:
                self.after(0, lambda: self.set_running(False))

        def done(res):
            info, path, probe = res
            self.set_vod(path, probe, info["start"])
            self.v_twitch.set("")
            if auto:
                self.auto_process()
            else:
                self.finish_steps("VOD descargado")
                self.status(path)
        self.run_bg(work, done, "No se pudo descargar el VOD")

    def auto_process(self):
        """Tras descargar: trae las partidas del VOD, saca las rondas y genera el vídeo."""
        try:
            vs = self.vod_start_utc()
        except ValueError:
            return
        riot, key, reg = self.v_riot.get(), self.v_key.get(), self.v_region.get()
        self.set_step(1)
        self.set_progress(None, "Buscando las partidas de competitivo que jugaste durante el directo…")
        prog = self.progress_cb()

        def work():
            found = []
            for page in range(6):                     # hasta 60 partidas hacia atrás
                prog(None, f"Revisando partidas {page * 10 + 1}–{page * 10 + 10}…")
                raws = core.fetch_recent_matches(riot, key, reg, size=10, start=page * 10)
                if not raws:
                    break
                ms = []
                for r in raws:
                    try:
                        ms.append(core.parse_match(r, riot))
                    except Exception:
                        pass
                found += ms
                if ms and min(m.started_at for m in ms) < vs:
                    break
            return found

        def done(ms):
            for m in ms:
                self.matches[m.match_id] = m
            self.next_start = max(self.next_start, len(ms))
            self.auto_check()
            if not self.checked:
                self.set_step(None, "No hay partidas en este VOD")
                self.set_progress(0, "No hay partidas de competitivo dentro de este VOD.")
                messagebox.showinfo(APP_NAME, "He descargado el VOD, pero no encuentro partidas de "
                                              "competitivo jugadas durante ese directo.")
                return
            self.set_step(2)
            self.compute()
            if not self.clips:
                self.set_step(None, "Sin rondas de 3K o más")
                messagebox.showinfo(APP_NAME, f"En las partidas de este VOD no hay rondas de "
                                              f"{self.settings().min_kills} kills o más.")
                return
            vod = self.v_vod.get()
            base = os.path.splitext(os.path.basename(vod))[0]
            out = os.path.join(os.path.dirname(vod), f"{base} - rondas {self.settings().min_kills}K+.mp4")
            self.render(out)
        self.run_bg(work, done, "No se pudieron cargar las partidas")

    def play(self, start, dur):
        ffplay = core.find_tool("ffplay")
        if not ffplay or not self.v_vod.get():
            messagebox.showinfo(APP_NAME, f"Abre el VOD en tu reproductor y ve a {core.fmt_ts(start)}.")
            return
        subprocess.Popen([ffplay, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}",
                          "-t", f"{dur:.2f}", "-autoexit", "-window_title", "Vista previa",
                          "-x", "960", "-y", "540", self.v_vod.get()])

    # ------------------------------------------------------------ rondas
    def compute(self):
        if not self.vod_info:
            messagebox.showinfo(APP_NAME, "Primero elige el VOD.")
            return
        try:
            vs = self.vod_start_utc()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        ms = [self.matches[i] for i in self.checked if i in self.matches]
        if not ms:
            messagebox.showinfo(APP_NAME, "Marca al menos una partida (☐ → ☑).")
            return
        self.persist()
        self.clips = core.build_clips(ms, vs, self.vod_info["duration"], self.settings(), self.calib)
        self.fill_clips()
        total = sum(c.vod_end - c.vod_start for c in self.clips)
        self.status(f"{len(self.clips)} rondas · {core.fmt_ts(total).split('.')[0]} de vídeo. "
                    f"Doble clic en una ronda para comprobarla.")

    def fill_clips(self):
        self.tv_c.delete(*self.tv_c.get_children())
        for i, c in enumerate(self.clips):
            self.tv_c.insert("", "end", iid=str(i), values=(
                c.label, core.fmt_ts(c.vod_start).split(".")[0], core.fmt_ts(c.vod_end).split(".")[0],
                f"{c.vod_end - c.vod_start:.0f}s"))

    def preview(self):
        sel = self.tv_c.selection()
        if not sel:
            return
        c = self.clips[int(sel[0])]
        self.play(c.vod_start, c.vod_end - c.vod_start)

    def remove_clips(self):
        idx = sorted((int(i) for i in self.tv_c.selection()), reverse=True)
        for i in idx:
            del self.clips[i]
        self.fill_clips()

    # ------------------------------------------------------------ render
    def render(self, out=None):
        if not self.clips:
            self.compute()
            if not self.clips:
                return
        vod = self.v_vod.get()
        base = os.path.splitext(os.path.basename(vod))[0]
        out = out or filedialog.asksaveasfilename(title="Guardar vídeo", defaultextension=".mp4",
                                           initialdir=os.path.dirname(vod),
                                           initialfile=f"{base}_rondas_{self.settings().min_kills}K+.mp4",
                                           filetypes=[("MP4", "*.mp4")])
        if not out:
            return
        self.persist()
        self.stop_flag = False
        self.set_running(True)
        self.set_step(3, f"Generando el vídeo con {len(self.clips)} rondas…")
        self.set_progress(0, "Empezando…")
        clips = list(self.clips)
        enc = ENCODERS[self.v_enc.get()]

        import time
        t0 = time.time()
        total_s = sum(c.vod_end - c.vod_start for c in clips)
        cb = self.progress_cb()

        def prog(frac, txt):
            el = time.time() - t0
            bits = [f"{core.fmt_ts(frac * total_s).split('.')[0]} de {core.fmt_ts(total_s).split('.')[0]} de vídeo"]
            if frac > 0.02:
                left = el / frac - el
                bits.append(f"quedan {twitch.fmt_eta(left)}")
                bits.append(f"{(frac * total_s) / max(el, 0.1):.1f}x velocidad".replace(".", ","))
            if frac >= 1:
                cb(None, "Terminando el archivo…")
            else:
                cb(frac, "  ·  ".join(bits))

        def work():
            try:
                return core.render(vod, clips, out, encoder=enc, show_label=False,
                                   progress=prog, should_stop=lambda: self.stop_flag)
            finally:
                self.after(0, lambda: self.set_running(False))

        def finish(path):
            self.finish_steps("¡Vídeo listo!")
            self.status(path)
            if messagebox.askyesno(APP_NAME, f"¡Vídeo listo! {len(clips)} rondas.\n\n{path}\n\n¿Abrir la carpeta?"):
                if os.name == "nt":
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        self.run_bg(work, finish, "No se pudo generar el vídeo")

    def cancel(self):
        self.stop_flag = True
        self.status("Cancelando…")

    # ------------------------------------------------------------ actualizaciones
    def check_update(self, silent=True):
        def target():
            try:
                info = updater.check()
            except Exception as e:
                if not silent:
                    msg = str(e)
                    self.after(0, lambda: messagebox.showwarning(
                        APP_NAME, f"No se pudo comprobar si hay versión nueva.\n\n{msg}"))
                return
            self.after(0, lambda: self.offer_update(info, silent))
        threading.Thread(target=target, daemon=True).start()

    def offer_update(self, info, silent):
        if not info:
            if not silent:
                messagebox.showinfo(APP_NAME, f"Ya tienes la última versión ({VERSION}).")
            return
        if self.busy or str(self.btn_cancel.cget("state")) == "normal":
            if not silent:
                messagebox.showinfo(APP_NAME, "Hay una versión nueva. Actualiza cuando termine "
                                              "lo que está haciendo el programa.")
            return
        notes = info["notes"].strip()
        notes = ("\n\nNovedades:\n" + notes[:600]) if notes else ""
        if not messagebox.askyesno(APP_NAME, f"Hay una versión nueva: {info['version']} "
                                             f"(tienes la {VERSION}).{notes}\n\n¿Actualizar ahora?"):
            return
        self.persist()
        self.set_step(None, "Actualizando el programa…")
        self.set_progress(0, "Descargando actualización…")
        cb = self.progress_cb()

        def prog(p):
            cb(p, "Descargando actualización…")

        def done(_):
            messagebox.showinfo(APP_NAME, "Actualizado. El programa se va a reiniciar.")
            updater.restart()
        self.run_bg(lambda: updater.apply(info["url"], prog), done, "No se pudo actualizar")

    def on_close(self):
        self.persist()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
