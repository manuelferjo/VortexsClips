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
import player
import subtitles
import sys
import time
import sync
import tempfile
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
ENCODERS = {"Automático (tu gráfica si se puede)": "auto",
            "CPU (x264, compatible siempre)": "x264",
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
        self.queue = []            # [(url, origen)]
        self.flow_active = False
        self.flow_source = "manual"
        self.current_url = None
        self.batch_size = 0
        self.errors_log = []
        self.sync_warning = ""
        self.delay_fn = None

        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(1500, lambda: self.check_update(silent=True))
        self.load_delay()
        self.after(800, self.detect_encoder)
        self.after(1200, self.restore_last_vod)
        if self.v_watch.get():
            self.after(30_000, self.watch_tick)
            self.lbl_watch.configure(text="Vigilando… (primera comprobación en 30 s)")
        if "--minimized" in sys.argv:
            self.after(300, self.iconify)

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
        s.configure("TNotebook", background=PANEL, borderwidth=0)
        s.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(10, 5),
                    font=("Segoe UI Semibold", 9))
        s.map("TNotebook.Tab", background=[("selected", YELLOW)], foreground=[("selected", "#111")])
        s.configure("TRadiobutton", background=PANEL, foreground=TEXT)
        s.map("TRadiobutton", background=[("active", PANEL)], indicatorcolor=[("selected", YELLOW)])
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

        # --- 2. VOD y ajustes (pestañas)
        p2 = self._panel(top, "2 · VOD y ajustes")
        p2.grid(row=0, column=1, sticky="nsew")
        nb = ttk.Notebook(p2)
        nb.grid(row=1, column=0, columnspan=6, sticky="nsew")
        t_vod = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        t_cut = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        t_out = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        t_auto = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        nb.add(t_vod, text="  VOD  ")
        nb.add(t_cut, text="  Cortes  ")
        nb.add(t_out, text="  Salida  ")
        nb.add(t_auto, text="  Automático  ")

        cfg = self.cfg
        self.v_vod = tk.StringVar()
        self.v_start = tk.StringVar()
        self.v_delay = tk.StringVar(value=str(cfg.get("delay", 0)))
        self.v_min = tk.IntVar(value=cfg.get("min_kills", 3))
        self.v_skipbuy = tk.BooleanVar(value=cfg.get("skip_buy", True))
        self.v_pre = tk.StringVar(value=str(cfg.get("pre", 3)))
        self.v_post = tk.StringVar(value=str(cfg.get("post", 4)))
        self.v_mode = tk.StringVar(value=cfg.get("mode", "round"))
        self.v_kpre = tk.StringVar(value=str(cfg.get("kill_pre", 2.5)))
        self.v_kpost = tk.StringVar(value=str(cfg.get("kill_post", 1.5)))
        enc_default = next((k for k, v in ENCODERS.items() if v == cfg.get("encoder", "auto")), list(ENCODERS)[0])
        self.v_enc = tk.StringVar(value=enc_default)
        self.v_twitch = tk.StringVar()
        self.v_outdir = tk.StringVar(value=cfg.get("out_dir") or os.path.join(
            os.path.expanduser("~"), "Videos", "Vortexs Clips"))
        self.v_auto = tk.BooleanVar(value=cfg.get("auto_process", True))
        self.v_autosync = tk.BooleanVar(value=cfg.get("auto_sync", True))
        self.v_vertical = tk.BooleanVar(value=cfg.get("vertical", True))
        self.v_subs = tk.BooleanVar(value=cfg.get("subtitles", True))
        self.v_singles = tk.BooleanVar(value=cfg.get("singles", True))
        self.cam = cfg.get("cam")
        self.v_channel = tk.StringVar(value=cfg.get("watch_channel", ""))
        self.v_watch = tk.BooleanVar(value=cfg.get("watch", False))
        self.v_startup = tk.BooleanVar(value=cfg.get("startup", False))

        # ---- pestaña VOD
        ttk.Label(t_vod, text="Pega uno o varios enlaces de VOD de Twitch (se procesan en cola):").grid(
            row=0, column=0, columnspan=4, sticky="w")
        tf = ttk.Frame(t_vod, style="Panel.TFrame")
        tf.grid(row=1, column=0, columnspan=4, sticky="we", pady=(4, 0))
        ttk.Entry(tf, textvariable=self.v_twitch, width=50).pack(side="left", fill="x", expand=True)
        self.btn_dl = ttk.Button(tf, text="Descargar VOD", style="Accent.TButton", command=self.download_vod)
        self.btn_dl.pack(side="left", padx=6)
        self.lbl_queue = ttk.Label(t_vod, text="", style="Muted.TLabel")
        self.lbl_queue.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(t_vod, text="Al terminar la descarga, sacar las rondas y generar los vídeos",
                        variable=self.v_auto).grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))
        of = ttk.Frame(t_vod, style="Panel.TFrame")
        of.grid(row=4, column=0, columnspan=4, sticky="we", pady=(6, 0))
        ttk.Button(of, text="Carpeta…", command=self.pick_outdir).pack(side="left")
        self.lbl_outdir = ttk.Label(of, text=self.v_outdir.get(), style="Muted.TLabel")
        self.lbl_outdir.pack(side="left", padx=8)
        ttk.Separator(t_vod).grid(row=5, column=0, columnspan=4, sticky="we", pady=8)
        vf = ttk.Frame(t_vod, style="Panel.TFrame")
        vf.grid(row=6, column=0, columnspan=4, sticky="we")
        ttk.Button(vf, text="o elegir un VOD del PC…", command=self.pick_vod).pack(side="left")
        self.lbl_vod = ttk.Label(vf, text="Ningún vídeo elegido", style="Muted.TLabel")
        self.lbl_vod.pack(side="left", padx=8)
        ttk.Label(t_vod, text="Inicio del directo").grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(t_vod, textvariable=self.v_start, width=19).grid(row=7, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(t_vod, text="dd/mm/aaaa hh:mm:ss (con enlace de Twitch se pone sola)", style="Muted.TLabel").grid(
            row=7, column=2, columnspan=2, sticky="w", pady=(8, 0))

        # ---- pestaña Cortes
        ttk.Label(t_cut, text="Kills mínimas por ronda").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(t_cut, from_=2, to=6, textvariable=self.v_min, width=4).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(t_cut, text="Qué cortar").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Radiobutton(t_cut, text="Solo las kills (de la primera a la última, con cortes entre ellas)",
                        value="kills", variable=self.v_mode).grid(row=1, column=1, columnspan=4, sticky="w", pady=(8, 2))
        kf = ttk.Frame(t_cut, style="Panel.TFrame")
        kf.grid(row=2, column=1, columnspan=4, sticky="w", padx=(22, 0))
        ttk.Label(kf, text="Segundos antes de cada kill").pack(side="left")
        ttk.Entry(kf, textvariable=self.v_kpre, width=4).pack(side="left", padx=4)
        ttk.Label(kf, text="  y después").pack(side="left")
        ttk.Entry(kf, textvariable=self.v_kpost, width=4).pack(side="left", padx=4)
        ttk.Radiobutton(t_cut, text="La ronda completa", value="round", variable=self.v_mode).grid(
            row=3, column=1, columnspan=4, sticky="w", pady=(6, 2))
        rf = ttk.Frame(t_cut, style="Panel.TFrame")
        rf.grid(row=4, column=1, columnspan=4, sticky="w", padx=(22, 0))
        ttk.Checkbutton(rf, text="Saltar fase de compra", variable=self.v_skipbuy).pack(side="left")
        ttk.Label(rf, text="   Margen antes / después (s)").pack(side="left")
        ttk.Entry(rf, textvariable=self.v_pre, width=4).pack(side="left", padx=4)
        ttk.Entry(rf, textvariable=self.v_post, width=4).pack(side="left")
        ttk.Separator(t_cut).grid(row=5, column=0, columnspan=5, sticky="we", pady=8)
        ttk.Checkbutton(t_cut, text="Sincronizar solo cada partida (busca el killfeed en el VOD)",
                        variable=self.v_autosync).grid(row=6, column=0, columnspan=5, sticky="w")
        ttk.Label(t_cut, text="Delay / desfase (s)").grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(t_cut, textvariable=self.v_delay, width=8).grid(row=7, column=1, sticky="w", padx=6, pady=(6, 0))
        self.lbl_delaylog = ttk.Label(t_cut, text="", style="Muted.TLabel")
        self.lbl_delaylog.grid(row=8, column=0, columnspan=5, sticky="w", pady=(2, 0))

        # ---- pestaña Salida
        ttk.Label(t_out, text="Codificar con").grid(row=0, column=0, sticky="w")
        ttk.Combobox(t_out, textvariable=self.v_enc, values=list(ENCODERS), state="readonly",
                     width=34).grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        self.lbl_enc = ttk.Label(t_out, text="", style="Muted.TLabel")
        self.lbl_enc.grid(row=1, column=1, columnspan=3, sticky="w", padx=6)
        ttk.Label(t_out, text="Calidad: misma resolución y fps que el directo, sin pasar del bitrate original.",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 6))
        vf2 = ttk.Frame(t_out, style="Panel.TFrame")
        vf2.grid(row=3, column=0, columnspan=4, sticky="we")
        ttk.Checkbutton(vf2, text="Versión vertical 9:16 (como tus TikToks)",
                        variable=self.v_vertical).pack(side="left")
        ttk.Button(vf2, text="Elegir cámara…", command=self.pick_cam).pack(side="left", padx=8)
        self.lbl_cam = ttk.Label(vf2, text=self._cam_text(), style="Muted.TLabel")
        self.lbl_cam.pack(side="left")
        ttk.Checkbutton(t_out, text="Subtítulos automáticos en la vertical (lo que dices, en grande bajo la cámara)",
                        variable=self.v_subs).grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(t_out, text="Además, un clip suelto por cada ronda (carpeta aparte)",
                        variable=self.v_singles).grid(row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # ---- pestaña Automático
        ttk.Label(t_auto, text="Tu canal de Twitch").grid(row=0, column=0, sticky="w")
        ttk.Entry(t_auto, textvariable=self.v_channel, width=24).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Checkbutton(t_auto, text="Vigilar el canal: cuando acabe un directo, procesar el VOD solo",
                        variable=self.v_watch, command=self.on_watch_toggle).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(t_auto, text="Abrir Vortexs Clips al encender el PC (minimizado)",
                        variable=self.v_startup, command=self.on_startup_toggle).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.lbl_watch = ttk.Label(t_auto, text="", style="Muted.TLabel")
        self.lbl_watch.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(t_auto, text="Mira cada 10 minutos. Espera a que termine el directo, descarga el VOD y "
                               "genera todos los vídeos.\nEl programa tiene que estar abierto (aunque sea minimizado).",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

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
        ttk.Button(mb, text="Sincronizar solo", style="Purple.TButton", command=self.sync_checked).pack(side="left", padx=6)
        ttk.Button(mb, text="Sincronizar a mano…", command=self.calibrate).pack(side="left")
        ttk.Button(mb, text="Informes", command=self.open_reports).pack(side="right")
        ttk.Button(mb, text="Quitar sincro", command=self.clear_calib).pack(side="left")

        # --- 4. rondas
        p4o = self._panel(mid, "4 · Rondas y vista previa")
        p4o.grid(row=0, column=1, sticky="nsew")
        p4o.rowconfigure(1, weight=1)
        p4o.columnconfigure(0, weight=1)
        self.nb4 = ttk.Notebook(p4o)
        self.nb4.grid(row=1, column=0, sticky="nsew")
        p4 = ttk.Frame(self.nb4, style="Panel.TFrame", padding=6)
        self.tab_prev = ttk.Frame(self.nb4, style="Panel.TFrame", padding=6)
        self.nb4.add(p4, text="  Rondas que se van a cortar  ")
        self.nb4.add(self.tab_prev, text="  Vista previa  ")
        self.player = player.Player(self.tab_prev, lambda: self.v_vod.get())
        self.player.pack(anchor="n")
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
        self.step_names = ["1 · Descargar VOD", "2 · Buscar partidas", "3 · Sincronizar",
                           "4 · Sacar rondas", "5 · Generar vídeos"]
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
                                global_adjust_s=f(self.v_delay, 0),
                                mode=self.v_mode.get(), kill_pre_s=f(self.v_kpre, 2.5),
                                kill_post_s=f(self.v_kpost, 1.5),
                                residual_s=float(self.cfg.get("residual_s", 0.0)))

    # ------------------------------------------------------------ delay de Vortexs Live
    def load_delay(self):
        try:
            rows = core.load_delay_log()
        except Exception:
            rows = []
        self.delay_fn = core.make_delay_fn(rows)
        if rows:
            self.lbl_delaylog.configure(text=f"✔ Registro de delay de Vortexs Live encontrado ({len(rows)} cambios). "
                                             "Se usa en vez del número de arriba.")
        else:
            self.lbl_delaylog.configure(text="Sin registro de delay de Vortexs Live: se usa el número de arriba.")

    def delay_fn_for_vod(self):
        return getattr(self, "delay_fn", None)

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
                         "auto_process": self.v_auto.get(), "auto_sync": self.v_autosync.get(),
                         "vertical": self.v_vertical.get(), "singles": self.v_singles.get(),
                         "cam": self.cam, "subtitles": self.v_subs.get(), "mode": self.v_mode.get(),
                         "kill_pre": self.v_kpre.get(), "kill_post": self.v_kpost.get(),
                         "watch_channel": self.v_channel.get(), "watch": self.v_watch.get(),
                         "startup": self.v_startup.get()})
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

                def fail():
                    self.busy = False
                    self.set_progress(0, "Error: " + msg.splitlines()[0][:150] if msg else "Error.")
                    self.set_step(None, "Se ha parado por un error")
                    if self.flow_active:
                        self.flow_failed(f"{err_title}: {msg}")
                    else:
                        messagebox.showerror(err_title, msg)
                self.after(0, fail)
        threading.Thread(target=target, daemon=True).start()

    # ------------------------------------------------------------ partidas
    def load_matches(self, more=False):
        if not self.v_key.get().strip():
            messagebox.showinfo(APP_NAME, "Falta la clave de la API de HenrikDev (ver README).")
            return
        self.persist()
        start = self.next_start if more else 0
        riot, key, reg = self.v_riot.get(), self.v_key.get(), self.v_region.get()
        # si hay un VOD cargado, ir hacia atrás hasta llegar al día de ese directo
        target = None
        if not more and self.vod_info:
            try:
                target = self.vod_start_utc()
            except ValueError:
                target = None
        self.set_progress(None, "Descargando partidas de competitivo…")
        cb = self.progress_cb()

        def work():
            out, pos = [], start
            for page in range(12 if target else 1):          # hasta 120 partidas hacia atrás
                if target:
                    cb(None, f"Buscando las partidas del directo… (revisadas {pos})")
                raws = core.fetch_recent_matches(riot, key, reg, size=10, start=pos)
                pos += 10
                ms = []
                for r in raws:
                    try:
                        ms.append(core.parse_match(r, riot))
                    except Exception:
                        pass
                out += ms
                if not raws or not target or (ms and min(m.started_at for m in ms) < target):
                    break
            return out, pos

        def done(res):
            ms, pos = res
            if not more:
                self.matches.clear()
                self.checked.clear()
            self.next_start = pos
            for m in ms:
                self.matches[m.match_id] = m
            self.refresh_matches()
            self.auto_check()
            n_in = len(self.checked)
            extra = f" · {n_in} en el VOD" if self.vod_info else ""
            self.set_progress(1, f"{len(self.matches)} partidas cargadas{extra}.")
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
                inv = core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib, self.delay_fn_for_vod())
                invt = "Sí" if inv else "No"
            else:
                inv, invt = None, "—"
            c = self.calib.get(m.match_id) or {}
            sync_txt = ("Auto ≈ (aprox.)" if c.get("auto") and c.get("approx") else
                        f"Auto ✔ ({int(c.get('conf', 0) * 100)}%)" if c.get("auto") else
                        "Manual ✔" if c.get("first_kill_vod_s") is not None else "Por hora")
            self.tv_m.insert("", "end", iid=m.match_id, tags=(("in",) if inv else ("out",) if inv is False else ()),
                             values=("☑" if m.match_id in self.checked else "☐",
                                     local.strftime("%d/%m %H:%M"), m.map_name, m.agent, m.score,
                                     n, invt, sync_txt))

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
                        if core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib,
                                            self.delay_fn_for_vod())}
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
                off = core.match_offset_s(m, self.vod_start_utc(), self.calib)
                if not core.is_synced(self.calib, m.match_id):
                    off += core.delay_offset(m, self.settings(), self.delay_fn_for_vod())
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
                            f"1. Abajo se reproduce el VOD un minuto antes de donde debería estar.\n"
                            f"    Muévete con los botones (o flechas ←/→ 10 s, ↑/↓ 1 min, espacio pausa).\n"
                            f"2. Cuando salga tu kill en el killfeed, pausa y pulsa «✔ Usar este momento».\n"
                            f"3. Pulsa «Guardar».")
                  ).pack(anchor="w", pady=8)
        v = tk.StringVar(value=guess)
        e = ttk.Entry(fr, textvariable=v, width=16)
        e.pack(anchor="w")
        e.focus_set()
        if guess:
            ttk.Label(fr, text=f"Según la hora debería estar sobre {guess}", style="Muted.TLabel").pack(anchor="w", pady=4)
        bf = ttk.Frame(fr, style="Panel.TFrame")
        bf.pack(anchor="w", pady=(10, 0))

        pl = player.Player(fr, lambda: self.v_vod.get(),
                           on_pick=lambda t: v.set(core.fmt_ts(t)[:11] if t >= 3600 else core.fmt_ts(t)))
        pl.pack(anchor="w", pady=(10, 0))
        dlg.bind("<Destroy>", lambda e: pl.stop() if e.widget is dlg else None)

        def look():
            try:
                t = core.parse_ts(v.get() or guess)
            except ValueError:
                t = 0
            pl.open(max(0, t - 60), t + 240)
        if guess:
            self.after(300, look)

        def ok():
            try:
                t = core.parse_ts(v.get())
            except ValueError:
                messagebox.showerror(APP_NAME, "Formato no válido. Ejemplo: 1:23:45", parent=dlg)
                return
            self.calib[m.match_id] = {"first_kill_vod_s": t}
            dlg.destroy()
            # cuánto estaba desplazada según la hora -> ofrecer aplicarlo a todas
            try:
                by_time = core.match_offset_s(m, self.vod_start_utc(), {})
                diff = (t - first / 1000) - by_time
            except ValueError:
                diff = None
            if diff is not None and abs(diff - self.settings().global_adjust_s) > 1.5:
                sign = "después" if diff > 0 else "antes"
                if messagebox.askyesno(APP_NAME,
                        f"Esta partida en el VOD va {abs(diff):.0f} s {sign} de lo que decía la hora.\n\n"
                        "Eso suele ser el delay del directo + la selección de agente, y es igual en "
                        "todas las partidas del mismo directo.\n\n"
                        "¿Aplico este ajuste a TODAS las partidas del VOD?"):
                    self.v_delay.set(f"{diff:.1f}")
            self.persist()
            self.auto_check()
            if self.clips or self.checked:
                self.compute()
        ttk.Button(bf, text="Buscar en el VOD", style="Purple.TButton", command=look).pack(side="left")
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
        self.load_delay()
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
        # recordar el VOD para la próxima vez que se abra el programa
        self.cfg["last_vod"] = {"path": p, "start": self.v_start.get()}
        self.persist()

    def restore_last_vod(self):
        """Al abrir: vuelve a cargar el último VOD (y sus partidas, si hay clave de la API)."""
        last = self.cfg.get("last_vod") or {}
        p = last.get("path")
        if not p or not os.path.isfile(p) or self.vod_info:
            return
        start = last.get("start", "")

        def done(info):
            self.v_start.set(start)
            self.set_vod(p, info, None)
            if start:
                self.v_start.set(start)
            if self.v_key.get().strip() and not self.matches:
                self.load_matches()
        self.set_progress(None, "Abriendo el último VOD…")
        self.run_bg(lambda: core.probe_vod(p), done, "No se pudo abrir el último VOD")

    # ------------------------------------------------------------ gráfica
    def detect_encoder(self):
        def work():
            e = core.best_encoder()
            self.after(0, lambda: self.lbl_enc.configure(text=f"Detectado: {core.ENC_NAMES.get(e, e)}"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ vigilar el canal
    def on_watch_toggle(self):
        ch = twitch.channel_name(self.v_channel.get())
        if self.v_watch.get() and not ch:
            messagebox.showinfo(APP_NAME, "Escribe primero el nombre de tu canal de Twitch.")
            self.v_watch.set(False)
            return
        self.v_channel.set(ch)
        self.persist()
        if not self.v_watch.get():
            self.lbl_watch.configure(text="Vigilancia apagada.")
            return
        self.lbl_watch.configure(text="Preparando la vigilancia…")

        def base():
            try:
                vods = twitch.latest_vods(ch, 5)
                ids = [v["id"] for v in vods]
                self.after(0, lambda: (self.cfg.__setitem__("processed_vods", ids), self.persist(),
                                       self.lbl_watch.configure(
                                           text=f"Vigilando twitch.tv/{ch}. Procesaré los directos NUEVOS a partir de ahora.")))
                self.after(10 * 60_000, self.watch_tick)
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self.lbl_watch.configure(text="⚠ No pude leer el canal: " + msg[:120]))
        threading.Thread(target=base, daemon=True).start()

    def watch_tick(self):
        if not self.v_watch.get():
            return
        self.after(10 * 60_000, self.watch_tick)          # siguiente comprobación
        ch = twitch.channel_name(self.v_channel.get())
        if not ch:
            return

        def work():
            stamp = time.strftime("%H:%M")
            try:
                if twitch.is_live(ch):
                    self.after(0, lambda: self.lbl_watch.configure(
                        text=f"{stamp} · Estás en directo: espero a que termine para procesar el VOD."))
                    return
                vods = twitch.latest_vods(ch, 5)
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self.lbl_watch.configure(text=f"{stamp} · ⚠ {msg[:120]}"))
                return

            def apply():
                done_ids = set(self.cfg.get("processed_vods", []))
                new = [v for v in vods if v["id"] not in done_ids]
                if new:
                    self.cfg["processed_vods"] = (list(done_ids) + [v["id"] for v in new])[-50:]
                    self.persist()
                    self.enqueue([v["url"] for v in reversed(new)], "watch")
                    self.lbl_watch.configure(text=f"{stamp} · Directo nuevo encontrado: «{new[0]['title'][:50]}». Procesando…")
                else:
                    self.lbl_watch.configure(text=f"{stamp} · Sin directos nuevos. Vuelvo a mirar en 10 min.")
            self.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    def on_startup_toggle(self):
        folder = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
                              "Programs", "Startup")
        path = os.path.join(folder, "Vortexs Clips.cmd")
        try:
            if self.v_startup.get():
                os.makedirs(folder, exist_ok=True)
                if getattr(sys, "frozen", False):
                    line = f'@start "" "{sys.executable}" --minimized\r\n'
                else:
                    here = os.path.dirname(os.path.abspath(__file__))
                    line = f'@cd /d "{here}"\r\n@start "" pyw app.py --minimized\r\n'
                with open(path, "w", encoding="utf-8") as f:
                    f.write(line)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"No se pudo cambiar el inicio con Windows:\n{e}")
        self.persist()

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

    # ------------------------------------------------------------ cola de VODs
    def download_vod(self):
        urls = twitch.find_vod_urls(self.v_twitch.get())
        if not urls:
            messagebox.showinfo(APP_NAME, "Pega uno o varios enlaces de VOD de Twitch, por ejemplo:\n"
                                          "https://www.twitch.tv/videos/1234567890")
            return
        if not core.find_tool("ffmpeg"):
            messagebox.showerror(APP_NAME, "Falta ffmpeg. Reinstala Vortexs Clips.")
            return
        if self.v_auto.get() and not self.v_key.get().strip():
            if not messagebox.askyesno(APP_NAME, "No has puesto la clave de la API, así que solo "
                                                 "descargaré los VODs (sin sacar las rondas).\n\n¿Seguir?"):
                return
        self.v_twitch.set("")
        self.enqueue(urls, "manual")

    def enqueue(self, urls, source):
        known = {u for u, _ in self.queue}
        for u in urls:
            if u not in known and u != self.current_url:
                self.queue.append((u, source))
        self.batch_size = max(self.batch_size, len(self.queue) + (1 if self.flow_active else 0))
        self.update_queue_label()
        self.after(100, self.next_in_queue)

    def update_queue_label(self):
        n = len(self.queue)
        cur = "Procesando 1 VOD" if self.flow_active else ""
        txt = (f"{cur}{' · ' if cur and n else ''}{n} en cola" if n else cur)
        self.lbl_queue.configure(text=txt)

    def unattended(self):
        return self.flow_active and (self.flow_source == "watch" or self.batch_size > 1)

    def next_in_queue(self):
        if self.flow_active or self.busy:
            return
        if not self.queue:
            self.batch_size = 0
            self.update_queue_label()
            return
        url, source = self.queue.pop(0)
        self.flow_active, self.flow_source, self.current_url = True, source, url
        self.update_queue_label()
        self._download_one(url, self.v_auto.get() and bool(self.v_key.get().strip()))

    def flow_done(self, msg=""):
        self.flow_active, self.current_url = False, None
        if msg:
            self.status(msg)
        self.update_queue_label()
        self.after(1500, self.next_in_queue)

    def flow_failed(self, msg):
        if not self.unattended():
            messagebox.showerror(APP_NAME, msg)
        self.errors_log.append(msg)
        self.flow_done("⚠ " + msg.splitlines()[0][:160])

    def _download_one(self, url, auto):
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
            if auto:
                self.auto_process()
            else:
                self.finish_steps("VOD descargado")
                self.flow_done(path)
        self.run_bg(work, done, "No se pudo descargar el VOD")

    def auto_process(self):
        """Tras descargar: trae las partidas del VOD, saca las rondas y genera el vídeo."""
        try:
            vs = self.vod_start_utc()
        except ValueError:
            self.flow_done("No sé a qué hora empezó el directo.")
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
                if not self.unattended():
                    messagebox.showinfo(APP_NAME, "He descargado el VOD, pero no encuentro partidas de "
                                                  "competitivo jugadas durante ese directo.")
                self.flow_done("Sin partidas de competitivo en este VOD.")
                return
            if self.v_autosync.get():
                self.run_sync(sorted(self.checked), then=self._finish_auto)
            else:
                self._finish_auto()

        self.run_bg(work, done, "No se pudieron cargar las partidas")

    def _finish_auto(self):
        self.set_step(3)
        self.compute()
        if not self.clips:
            self.set_step(None, "Sin rondas de 3K o más")
            if not self.unattended():
                messagebox.showinfo(APP_NAME, f"En las partidas de este VOD no hay rondas de "
                                              f"{self.settings().min_kills} kills o más.")
            self.flow_done("Sin rondas para cortar en este VOD.")
            return
        vod = self.v_vod.get()
        base = os.path.splitext(os.path.basename(vod))[0]
        out = os.path.join(os.path.dirname(vod), f"{base} - rondas {self.settings().min_kills}K+.mp4")
        self.render(out)

    # ------------------------------------------------------------ sincronización automática
    def run_sync(self, match_ids, then=None):
        if not self.vod_info:
            messagebox.showinfo(APP_NAME, "Primero elige o descarga el VOD.")
            return
        try:
            vs = self.vod_start_utc()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        ms = [self.matches[i] for i in match_ids if i in self.matches]
        if not ms:
            messagebox.showinfo(APP_NAME, "Marca al menos una partida (☐ → ☑).")
            return
        st = self.settings()
        dfn = self.delay_fn_for_vod()
        vod, dur = self.v_vod.get(), self.vod_info["duration"]
        self.stop_flag = False
        self.set_running(True)
        self.set_step(2, f"Sincronizando {len(ms)} partidas con el VOD…")
        cb = self.progress_cb()

        def work():
            try:
                res = {}
                for i, m in enumerate(sorted(ms, key=lambda x: x.started_at)):
                    cb(i / len(ms), f"Partida {i + 1} de {len(ms)}: {m.map_name} "
                                    f"({m.started_at.astimezone():%H:%M}) — buscando el killfeed…")
                    # punto de partida: la sincro manual si la hay; si no, la hora + delay
                    c = dict(self.calib.get(m.match_id) or {})
                    c.pop("offset_s", None)
                    tmp_cal = {m.match_id: c} if c else {}
                    exp = core.match_offset_s(m, vs, tmp_cal)
                    if not c:
                        exp += core.delay_offset(m, st, dfn)
                    res[m.match_id] = sync.sync_match(
                        vod, m, exp, dur,
                        lambda f, t, i=i, n=len(ms): cb(i / n, f"Partida {i + 1} de {n}: {t}"),
                        lambda: self.stop_flag)
                if len(res) > 1 and any(not r.ok for r in res.values()):
                    cb(1, "Afinando con el desfase común de todas las partidas…")
                    res = sync.consensus(res, os.path.basename(vod))
                return res
            finally:
                self.after(0, lambda: self.set_running(False))

        def done(res):
            good = bad = 0
            shifts = []
            failed = []
            approx = []
            learned = []
            for mid, r in res.items():
                if r.ok and dfn is not None and mid in self.matches:
                    m_ = self.matches[mid]
                    d_ = dfn(m_.started_at)
                    if d_ is not None:
                        learned.append(r.offset_s - core.match_offset_s(m_, vs, {}) - d_)
                if r.ok:
                    self.calib[mid] = {"offset_s": r.offset_s, "auto": True,
                                       "conf": round(r.confidence, 2), "approx": r.approx}
                    if r.approx:
                        m_ = self.matches.get(mid)
                        if m_:
                            approx.append(f"{m_.map_name} {m_.started_at.astimezone():%d/%m %H:%M}")
                    good += 1
                    shifts.append(r.shift_s)
                else:
                    bad += 1
                    m_ = self.matches.get(mid)
                    if m_:
                        failed.append(f"{m_.map_name} {m_.started_at.astimezone():%d/%m %H:%M}")
            if learned:
                learned.sort()
                self.cfg["residual_s"] = round(learned[len(learned) // 2], 2)
            self.persist()
            self.auto_check()
            self.set_progress(1, f"Sincronizadas solas: {good}" + (f" · sin patrón claro: {bad} "
                              "(se quedan con la hora + delay; puedes sincronizarlas a mano)" if bad else ""))
            if approx and not failed:
                self.sync_warning = (f"{len(approx)} partida(s) se han ajustado con el desfase común del directo "
                                     f"({', '.join(approx[:6])}{'…' if len(approx) > 6 else ''}), sin pico propio "
                                     "claro. Revisa alguna ronda suya con «Ver ronda».")
            elif failed:
                self.sync_warning = (f"{len(failed)} partida(s) no se han podido sincronizar solas "
                                     f"({', '.join(failed[:6])}{'…' if len(failed) > 6 else ''}). "
                                     "Sus cortes pueden caer desplazados: revísalas con «Ver ronda» o "
                                     "sincronízalas a mano.")
            else:
                self.sync_warning = ""
            if then:
                then()
            else:
                self.set_step(None, "Sincronización terminada")
                if self.clips:
                    self.compute()
                if failed or approx:
                    self.show_warnings([self.sync_warning], ask_report=bool(failed))
        self.run_bg(work, done, "No se pudo sincronizar")

    # ------------------------------------------------------------ avisos
    def show_warnings(self, items, ask_report=False):
        """Ventana con los avisos (en vez de dejarlos solo en la línea de abajo)."""
        items = [i for i in items if i]
        if not items or self.unattended():
            return
        txt = "\n\n".join("⚠ " + i for i in items)
        if ask_report:
            txt += ("\n\nHe guardado un informe de esas partidas. Si me lo pasas, ajusto el detector "
                    "con tu imagen real.\n\n¿Abrir la carpeta de informes?")
            if messagebox.askyesno(APP_NAME, txt, icon="warning"):
                self.open_reports()
        else:
            messagebox.showwarning(APP_NAME, txt)

    def open_reports(self):
        d = sync.report_dir()
        if os.name == "nt":
            os.startfile(d)
        else:
            messagebox.showinfo(APP_NAME, d)

    def sync_checked(self):
        self.run_sync(sorted(self.checked))

    # ------------------------------------------------------------ cámara (vertical)
    def _cam_text(self):
        if not self.cam:
            return "sin cámara elegida (solo juego)"
        return f"cámara guardada ({self.cam['w'] * 100:.0f}% × {self.cam['h'] * 100:.0f}% de la imagen)"

    def pick_cam(self):
        if not self.vod_info:
            messagebox.showinfo(APP_NAME, "Primero elige o descarga un VOD: sacaré un fotograma para que marques tu cámara.")
            return
        at = self.clips[0].vod_start + 10 if self.clips else self.vod_info["duration"] / 2
        png = os.path.join(tempfile.gettempdir(), "vortexsclips_frame.png")
        try:
            core.grab_frame(self.v_vod.get(), at, png, 960)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        dlg = tk.Toplevel(self)
        dlg.title("Elige tu cámara")
        dlg.configure(bg=PANEL)
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(dlg, text="Arrastra con el ratón un recuadro justo encima de tu cámara y pulsa «Guardar».",
                  style="H.TLabel").pack(anchor="w", padx=12, pady=(10, 6))
        img = tk.PhotoImage(file=png)
        cv = tk.Canvas(dlg, width=img.width(), height=img.height(), highlightthickness=0, bg=BG,
                       cursor="crosshair")
        cv.pack(padx=12)
        cv.create_image(0, 0, anchor="nw", image=img)
        cv.img = img
        st = {"x0": 0, "y0": 0, "rect": None, "box": None}
        if self.cam:
            W_, H_ = img.width(), img.height()
            b = (self.cam["x"] * W_, self.cam["y"] * H_, (self.cam["x"] + self.cam["w"]) * W_,
                 (self.cam["y"] + self.cam["h"]) * H_)
            st["rect"] = cv.create_rectangle(*b, outline=YELLOW, width=3)
            st["box"] = b

        def down(e):
            st["x0"], st["y0"] = e.x, e.y
            if st["rect"]:
                cv.delete(st["rect"])
            st["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline=YELLOW, width=3)

        def drag(e):
            cv.coords(st["rect"], st["x0"], st["y0"], e.x, e.y)

        def up(e):
            x0, y0 = st["x0"], st["y0"]
            st["box"] = (min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y))
        cv.bind("<ButtonPress-1>", down)
        cv.bind("<B1-Motion>", drag)
        cv.bind("<ButtonRelease-1>", up)
        bf = ttk.Frame(dlg, style="Panel.TFrame")
        bf.pack(fill="x", padx=12, pady=10)

        def save():
            b = st["box"]
            if not b or b[2] - b[0] < 20 or b[3] - b[1] < 20:
                messagebox.showinfo(APP_NAME, "Marca un recuadro un poco más grande.", parent=dlg)
                return
            W_, H_ = img.width(), img.height()
            x0, y0 = max(0, b[0]) / W_, max(0, b[1]) / H_
            x1, y1 = min(W_, b[2]) / W_, min(H_, b[3]) / H_
            self.cam = {"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)}
            self.lbl_cam.configure(text=self._cam_text())
            self.persist()
            dlg.destroy()

        def none():
            self.cam = None
            self.lbl_cam.configure(text=self._cam_text())
            self.persist()
            dlg.destroy()
        ttk.Button(bf, text="Guardar", style="Accent.TButton", command=save).pack(side="right")
        ttk.Button(bf, text="Sin cámara (solo juego)", command=none).pack(side="right", padx=6)
        self.wait_window(dlg)

    def play(self, start, dur=None, clock=False):
        """Reproduce el VOD desde 'start'. clock=True muestra encima el minuto del VOD."""
        ffplay = core.find_tool("ffplay")
        if not ffplay or not self.v_vod.get():
            messagebox.showinfo(APP_NAME, f"Abre el VOD en tu reproductor y ve a {core.fmt_ts(start)}.")
            return
        cmd = [ffplay, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}",
               "-window_title", "Vista previa  (flechas: ←/→ 10 s, ↑/↓ 1 min, espacio: pausa)",
               "-x", "1280", "-y", "720"]
        if dur:
            cmd += ["-t", f"{dur:.2f}", "-autoexit"]
        font = core._font_for_drawtext()
        if clock and font:
            cmd += ["-vf", f"drawtext=fontfile='{core._esc_filter_path(font)}'"
                           ":text='%{pts\\:hms}':fontsize=h/14:fontcolor=0xFFD21F"
                           ":box=1:boxcolor=0x000000AA:boxborderw=12:x=(w-tw)/2:y=h/30"]
        cmd.append(self.v_vod.get())
        subprocess.Popen(cmd)

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
        self.clips = core.build_clips(ms, vs, self.vod_info["duration"], self.settings(), self.calib,
                                      self.delay_fn_for_vod())
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
        self.nb4.select(self.tab_prev)
        self.player.open(c.vod_start, c.vod_end)

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
        if self.v_vertical.get() and not self.cam and not self.cfg.get("cam_asked") and not self.unattended():
            self.cfg["cam_asked"] = True
            if messagebox.askyesno(APP_NAME, "Para la versión vertical como tus TikToks tengo que saber dónde está "
                                             "tu cámara en el directo.\n\n¿La marcas ahora? (solo una vez)"):
                self.pick_cam()
        self.persist()
        self.stop_flag = False
        self.set_running(True)
        self.set_step(4, f"Generando el vídeo con {len(self.clips)} rondas…")
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

        vertical, singles, cam = self.v_vertical.get(), self.v_singles.get(), self.cam
        subs = vertical and self.v_subs.get()
        warnings = []

        def sub(title):
            self.after(0, lambda: self.lbl_step.configure(text=title))

        def work():
            try:
                stop = lambda: self.stop_flag
                core.render(vod, clips, out, encoder=enc, show_label=False, progress=prog, should_stop=stop)
                made = [out]
                base = os.path.splitext(out)[0]
                words = None
                if subs:
                    sub("Transcribiendo lo que dices para los subtítulos…")
                    try:
                        words = subtitles.transcribe(out, cb, stop)
                    except Exception as e:
                        if "Cancelado" in str(e):
                            raise
                        warnings.append(f"Subtítulos: {e}")
                if vertical:
                    sub("Generando la versión vertical (TikTok)…")
                    v = base + " (vertical).mp4"
                    core.make_vertical(out, v, cam, enc, lambda f, t: cb(f, "Versión vertical 9:16…"), stop,
                                       words=words)
                    made.append(v)
                if singles:
                    sub("Sacando un clip por ronda…")
                    folder = base + " - clips sueltos"
                    core.export_singles(out, clips, folder, vertical_cam=cam, vertical=vertical,
                                        encoder=enc, progress=cb, should_stop=stop, words=words)
                    made.append(folder)
                return made
            finally:
                self.after(0, lambda: self.set_running(False))

        def finish(made):
            path = made[0]
            self.finish_steps("¡Vídeos listos!")
            self.status(path)
            if getattr(self, "sync_warning", ""):
                warnings.insert(0, self.sync_warning)
            if warnings:
                self.status(path + "   ⚠ " + " · ".join(warnings)[:200])
            if self.unattended():
                self.flow_done(f"Listo: {os.path.basename(path)}")
                return
            extra = ""
            if vertical:
                extra += "\n• Versión vertical 9:16"
            if singles:
                extra += "\n• Carpeta con un clip por ronda"
            if self.flow_active:
                self.after(2000, lambda: self.flow_done(f"Listo: {os.path.basename(path)}"))
            if warnings:
                self.show_warnings(warnings, ask_report=bool(getattr(self, "sync_warning", "")))
            if messagebox.askyesno(APP_NAME, f"¡Listo! {len(clips)} rondas.\n\n• Vídeo horizontal{extra}\n\n"
                                             f"Todo está en:\n{os.path.dirname(path)}\n\n¿Abrir la carpeta?"):
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
                info = updater.latest()
            except Exception as e:
                if not silent:
                    msg = str(e)
                    self.after(0, lambda: messagebox.showwarning(
                        APP_NAME, f"No se pudo comprobar si hay versión nueva.\n\n{msg}"))
                return
            self.after(0, lambda: self.offer_update(info, silent))
        threading.Thread(target=target, daemon=True).start()

    def offer_update(self, info, silent):
        newer = info and info["version"] and updater._ver(info["version"]) > updater._ver(VERSION)
        working = self.busy or self.flow_active or str(self.btn_cancel.cget("state")) == "normal"
        # versión antigua (Iniciar.bat) -> ofrecer pasarse a la instalable una vez
        if not updater.FROZEN and info and info.get("setup") and (newer or not self.cfg.get("migrate_asked")):
            if working:
                return
            self.cfg["migrate_asked"] = True
            self.persist()
            if messagebox.askyesno(APP_NAME, "Ya hay una versión INSTALABLE de Vortexs Clips (.exe):\n\n"
                                             "• Se abre con doble clic, con su icono, sin ventanas negras\n"
                                             "• Trae ffmpeg incluido\n"
                                             "• Se actualiza sola en silencio\n\n"
                                             "Tu clave, ajustes y cámara se mantienen.\n\n¿La instalo ahora?"):
                self._run_update(lambda prog: updater.run_installer(info["setup"], prog, silent=False))
                return
            if not newer:
                return
        if not newer:
            if not silent:
                messagebox.showinfo(APP_NAME, f"Ya tienes la última versión ({VERSION}).")
            return
        if working:
            if not silent:
                messagebox.showinfo(APP_NAME, "Hay una versión nueva. Actualiza cuando termine "
                                              "lo que está haciendo el programa.")
            return
        notes = info["notes"].strip()
        notes = ("\n\nNovedades:\n" + notes[:600]) if notes else ""
        if not messagebox.askyesno(APP_NAME, f"Hay una versión nueva: {info['version']} "
                                             f"(tienes la {VERSION}).{notes}\n\n¿Actualizar ahora?"):
            return
        if updater.FROZEN and info.get("setup"):
            self._run_update(lambda prog: updater.run_installer(info["setup"], prog, silent=True))
        else:
            def zip_update(prog):
                updater.apply(info["url"], prog)
                return "restart"
            self._run_update(zip_update)

    def _run_update(self, fn):
        self.persist()
        self.set_step(None, "Actualizando el programa…")
        self.set_progress(0, "Descargando actualización…")
        cb = self.progress_cb()

        def done(res):
            if res == "restart":
                messagebox.showinfo(APP_NAME, "Actualizado. El programa se va a reiniciar.")
                updater.restart()
        self.run_bg(lambda: fn(lambda p: cb(p, "Descargando actualización…")), done, "No se pudo actualizar")

    def on_close(self):
        self.persist()
        self.destroy()


def selftest(out_path):
    """Prueba automática (la usa GitHub antes de publicar): monta la ventana, reproduce
    un vídeo en la vista previa y genera una vertical con subtítulos."""
    import traceback
    results = []

    def log(ok, what):
        results.append(("OK  " if ok else "FAIL") + " " + what)

    try:
        tmp = tempfile.mkdtemp(prefix="vc_selftest_")
        ff = core.find_tool("ffmpeg")
        log(bool(ff), f"ffmpeg: {ff}")
        log(bool(core.find_tool("ffprobe")), "ffprobe")
        log(bool(core.find_tool("ffplay")), "ffplay")
        vid = os.path.join(tmp, "t.mp4")
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=1920x1080:rate=30", "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000",
                        "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", vid],
                       check=True, **core._no_window())
        log(os.path.isfile(vid), "vídeo de prueba")
        log(True, f"codificador: {core.best_encoder()}")
        words = [{"start": 0.5, "end": 0.9, "text": "vale"}, {"start": 1.0, "end": 1.4, "text": "mira"},
                 {"start": 1.5, "end": 2.0, "text": "esto"}]
        cam = {"x": 0.7, "y": 0.6, "w": 0.25, "h": 0.3}
        vout = os.path.join(tmp, "v.mp4")
        core.make_vertical(vid, vout, cam, "auto", words=words)
        info = core.probe_vod(vout)
        log(info["width"] == 1080 and info["height"] == 1920, f"vertical con subtítulos {info['width']}x{info['height']}")
        # sincronización automática con una partida simulada: cartel WON/LOST al final de cada ronda
        import random
        from datetime import datetime, timezone
        random.seed(3)
        true_off, t, rounds_ = 20.0, 10.0, []
        for i in range(12):
            st_ = t + 5
            en_ = st_ + random.uniform(8, 18)
            rounds_.append((st_, en_))
            t = en_ + 4
        boxes = []
        for st_, en_ in rounds_:
            b_ = true_off + en_ + 0.4
            boxes.append(f"drawbox=x=480:y=100:w=320:h=70:color=0x1f8f80:t=fill:enable='between(t,{b_:.2f},{b_ + 3:.2f})'")
            boxes.append(f"drawbox=x=540:y=118:w=200:h=26:color=white:t=fill:enable='between(t,{b_:.2f},{b_ + 3:.2f})'")
        svod = os.path.join(tmp, "s.mp4")
        total_ = true_off + t + 20
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=15", "-filter_complex", "[0:v]" + ",".join(boxes) + "[v]",
                        "-map", "[v]", "-t", f"{total_:.1f}", "-c:v", "libx264", "-preset", "ultrafast", svod],
                       check=True, **core._no_window())
        m = core.Match("t", "Prueba", datetime.now(timezone.utc), 0, "Jett", "me", "13-0")
        m.rounds, m.kills = [], []
        for i, (st_, en_) in enumerate(rounds_):
            rr_ = core.Round(i, start_match_ms=int(st_ * 1000))
            rr_.result, rr_.end_match_ms = "Elimination", int(en_ * 1000)
            m.rounds.append(rr_)
            m.kills.append(core.Kill(i, int(en_ * 1000), int((en_ - st_) * 1000), "me", "b"))
        r = sync.sync_match(svod, m, true_off - 37.5, total_)
        log(r.ok and abs(r.offset_s - true_off - 0.4) <= 0.5,
            f"sincronización por cartel de fin de ronda (error {r.offset_s - true_off - 0.4:+.2f} s, ok={r.ok})")
        app = App()
        log(True, "ventana creada")
        app.v_vod.set(vid)
        app.nb4.select(app.tab_prev)
        app.player.open(1.0, 4.0)

        def finish():
            log(app.player._img is not None, f"vista previa pinta fotogramas (raw={app.player._raw_ok})")
            app.player.stop()
            app.destroy()
        app.after(4000, finish)
        app.mainloop()
    except Exception:
        log(False, traceback.format_exc())
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(results) + "\n")
    sys.exit(0 if all(r.startswith("OK") for r in results) else 1)


def selftest_subs(wav, out_path):
    """Prueba del transcriptor: descarga whisper.cpp + modelo y transcribe un audio."""
    import traceback
    try:
        ws = subtitles.transcribe(wav)
        txt = " ".join(w["text"] for w in ws)
        ok = len(ws) >= 2
        res = ("OK  " if ok else "FAIL") + f" transcripción ({len(ws)} palabras): {txt[:200]}"
    except Exception:
        ok, res = False, "FAIL " + traceback.format_exc()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(res + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest-subs" in sys.argv:
        i = sys.argv.index("--selftest-subs")
        selftest_subs(sys.argv[i + 1], sys.argv[i + 2])
    elif "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
    else:
        App().mainloop()
