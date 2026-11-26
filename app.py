"""
Vortexs Clips - interfaz
Saca las rondas en las que haces 3 kills o más de tus partidas de competitivo
y las recorta del VOD de Twitch en un solo vídeo.
"""
import json
import re
import os
import shutil
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
import killfeed
import drive
import semana
import ui

ui.load_fonts()      # las fuentes de Vortexs Live, antes de crear ninguna ventana
from version import VERSION

APP_NAME = "Vortexs Clips"
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "VortexsClips")
CFG_FILE = os.path.join(CFG_DIR, "config.json")

# Colores: los mismos que Vortexs Live (ver ui.py)
BG = ui.BG
PANEL = ui.SURFACE
FIELD = ui.ELEVATED
TEXT = ui.TEXT
MUTED = "#9a9aa6"
YELLOW = ui.YELLOW
PURPLE = ui.VIOLET
BORDER = ui.BORDER
HOVER = ui.HOVER
ROW_ALT = "#14141a"       # filas alternas de las tablas
DARK = "#0b0a10"

REGIONS = ["eu", "na", "ap", "kr", "latam", "br"]
ENCODERS = {"Automático (tu gráfica si se puede)": "auto",
            "CPU (x264, compatible siempre)": "x264",
            "NVIDIA (NVENC, rápido)": "nvenc",
            "Intel (QuickSync)": "qsv",
            "AMD (AMF)": "amf"}


# colores de la tabla de partidas según cómo de fiable es su sincronización
SYNC_COLORS = {"s_ok": ui.GREEN, "s_approx": YELLOW, "s_hora": "#ff9f43", "s_fail": ui.RED}


class _SettingsTabs:
    """Lo que antes eran las pestañas VOD / Cortes / Salida / Automático: abre la parte de los ajustes."""
    KEYS = (None, "cortar", "videos", "auto")

    def __init__(self, app):
        self.app = app

    def select(self, i):
        k = self.KEYS[i] if isinstance(i, int) and 0 <= i < len(self.KEYS) else None
        if k is None:
            self.app.show_page("vod")
        else:
            self.app.open_settings(k)


class _PageTabs:
    """Lo que antes eran las pestañas Rondas / Vista previa: ahora todo está en la página Vídeos."""

    def __init__(self, app, page):
        self.app, self.page = app, page

    def select(self, *_a):
        self.app.show_page(self.page)


def sync_tag(c: dict) -> str:
    if c.get("check") == "dudosa":
        return "s_approx"
    if c.get("auto") and c.get("approx"):
        return "s_approx"
    if c.get("auto") and float(c.get("conf") or 0) < 0.5:
        return "s_approx"
    if c.get("auto") or c.get("first_kill_vod_s") is not None:
        return "s_ok"
    if c.get("failed"):
        return "s_fail"
    return "s_hora"


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
        # sincronizaciones POR VOD: el desfase de una partida solo vale para el vídeo en el que se midió
        # (antes era una lista común y las partidas de otros días se «colaban» en el VOD abierto)
        self._calib_by_vod: dict = self.cfg.get("calib_by_vod", {})
        self._calib_legacy: dict = self.cfg.get("calib", {})
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
        ui.keep_cards_in_sync(self)
        ui.keep_scroll_in_sync(self)
        self.after(300, self._relayout)

        def _relay_loop():
            self._relayout()
            self.after(1000, _relay_loop)
        self.after(1500, _relay_loop)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(1500, lambda: self.check_update(silent=True))
        self.after(3000, updater.cleanup_old)
        core.API_WAIT_CB = lambda left: self.after(0, lambda: self.status(
            f"Esperando a la API de Riot: límite de peticiones por minuto ({left:.0f} s)…"))
        # con el programa abierto también: mira cada 10 min si hay versión nueva y, si está trabajando,
        # espera a que termine para avisar (sin tener que cerrarlo y volver a abrirlo)
        self._upd_declined = set()
        self._upd_pending = None

        def _upd_loop():
            self.check_update(silent=True)
            self.after(10 * 60 * 1000, _upd_loop)
        self.after(10 * 60 * 1000, _upd_loop)

        def _upd_wait():
            info = self._upd_pending
            if info and not self._is_working():
                self._upd_pending = None
                self.offer_update(info, True)
            self.after(5000, _upd_wait)
        self.after(5000, _upd_wait)
        self.load_delay()
        self.after(800, self.detect_encoder)
        self.after(1200, self.restore_last_vod)
        self.after(20000, self.purge_old_vods)
        self.after(30000, subtitles.prefetch_gpu_async)   # en PCs con NVIDIA: transcriptor rápido
        self.after(90000, self.top_week_auto)
        self.after(1500, lambda: self.mirror_delay_log(True))
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
        s = ui.apply_style(self)
        F = ui
        s.configure("Title.TLabel", background=BG, foreground=TEXT, font=F.display(22))
        s.configure("TitleY.TLabel", background=BG, foreground=YELLOW, font=F.display(22))
        s.configure("Sub.TLabel", background=BG, foreground=ui.DIM, font=F.mono(8))
        s.configure("Ver.TLabel", background=BG, foreground=ui.DIM, font=F.mono(8))
        s.configure("Num.TLabel", background=PANEL, foreground=PURPLE, font=F.display(14))
        s.configure("Badge.TLabel", background=PANEL, foreground=PURPLE, font=F.display(14))
        s.configure("Kpi.TFrame", background=FIELD)
        s.configure("Step.TLabel", background=PANEL, foreground=TEXT, font=F.display(15))
        s.configure("Pct.TLabel", background=PANEL, foreground=YELLOW, font=F.display(22))
        s.configure("StepS.TLabel", background=PANEL, foreground=TEXT, font=F.display(12))
        s.configure("PctS.TLabel", background=PANEL, foreground=YELLOW, font=F.display(12))
        s.configure("Detail.TLabel", background=PANEL, foreground=MUTED, font=F.body(10))
        s.configure("ChipOff.TLabel", background=PANEL, foreground=ui.DIM, font=F.mono(8), padding=(10, 5))
        s.configure("ChipOn.TLabel", background=YELLOW, foreground=DARK, font=F.mono(8), padding=(10, 5))
        s.configure("ChipDone.TLabel", background=ui.SEL, foreground=PURPLE, font=F.mono(8), padding=(10, 5))

    def _panel(self, parent, title, sub=""):
        """Tarjeta redondeada con número y título: «1 · Cuenta y partidas» -> 01 Cuenta y partidas."""
        f = ui.Card(parent, pad=13, radius=18)
        hd = ttk.Frame(f, style="Panel.TFrame")
        hd.grid(row=0, column=0, columnspan=10, sticky="w", pady=(0, 4))
        num, _, txt = title.partition(" · ")
        if txt:
            ttk.Label(hd, text=num.zfill(2), style="Num.TLabel").pack(side="left", padx=(0, 10))
        else:
            txt = title
        ttk.Label(hd, text=txt, style="H.TLabel").pack(side="left")
        if sub:
            ttk.Label(hd, text="   " + sub, style="HSub.TLabel").pack(side="left", pady=(5, 0))
        return f

    # ------------------------------------------------------------ UI
    def _build(self):
        """
        Pantalla principal en 3 pasos (VOD → Partidas → Vídeos), uno a la vista cada vez; los ajustes
        que se tocan una vez (cuenta, cortes, vídeos, carpeta, automático) van en la ventana Ajustes.
        """
        head = ui.GlowHeader(self, height=80)
        head.pack(fill="x")
        self._logo = ui.logo_image(self, 44)
        upd = ui.Button(head, text="Buscar actualizaciones", style="Ghost.TButton",
                        command=lambda: self.check_update(silent=False))
        b_dir = ui.Button(head, text="Carpeta", command=self.open_outdir)
        for b_ in (upd, b_dir):
            b_.configure(bg=BG)
            b_._bgc = BG
            b_._cache.clear()
            b_._draw()
        self.pill = ui.status_pill(head)
        self._head_vod = ""

        def place_head(w):
            head.delete("fg")
            x = 22
            if self._logo:
                head.create_image(x, 16, image=self._logo, anchor="nw", tags="fg")
                x += 58
            f1 = ui.display(22)
            t1 = head.create_text(x, 14, text="Vortexs", font=f1, fill=TEXT, anchor="nw", tags="fg")
            bx = head.bbox(t1)
            t2 = head.create_text(bx[2] + 8, 14, text="Clips", font=f1, fill=YELLOW, anchor="nw", tags="fg")
            bx2 = head.bbox(t2)
            if self._head_vod:
                head.create_text(bx2[2] + 18, 24, text=self._head_vod, font=ui.body(10), fill=MUTED,
                                 anchor="nw", tags="fg")
            head.create_text(x + 1, 52, text=f"V{VERSION}  ·  TUS 3K, 4K, ACE Y CLUTCHES, LISTOS PARA TIKTOK",
                             font=ui.mono(8), fill=ui.DIM, anchor="nw", tags="fg")
            xr = w - 22
            for wdg in (b_dir, upd, self.pill):
                head.create_window(xr, 40, window=wdg, anchor="e", tags="fg")
                xr -= wdg.winfo_reqwidth() + 10
        head.items.append(place_head)
        self._head = head

        # ---------------- cuerpo: pasos arriba y una página cada vez
        self.body = ui.ScrollArea(self)
        stepper = tk.Frame(self.body.inner, bg=BG)
        stepper.pack(fill="x", padx=16, pady=(10, 2))
        self._steps = []
        for i, (key, txt) in enumerate((("vod", "VOD"), ("partidas", "Partidas"), ("videos", "Vídeos"))):
            stepper.columnconfigure(i, weight=1, uniform="st")
            f_ = tk.Frame(stepper, bg=PANEL, cursor="hand2", highlightthickness=1, highlightbackground=BORDER)
            f_.grid(row=0, column=i, sticky="we", padx=(0 if i == 0 else 8, 0))
            ic = tk.Label(f_, text="○", bg=PANEL, fg=ui.DIM, font=ui.display(13), cursor="hand2")
            ic.pack(side="left", padx=(14, 6), pady=9)
            tl = tk.Label(f_, text=f"{i + 1} · {txt}", bg=PANEL, fg=TEXT, font=ui.display(13), cursor="hand2")
            tl.pack(side="left", pady=9)
            sl = tk.Label(f_, text="", bg=PANEL, fg=ui.DIM, font=ui.body(9), cursor="hand2")
            sl.pack(side="left", padx=(10, 0), pady=9)
            for w_ in (f_, ic, tl, sl):
                w_.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            self._steps.append((key, f_, ic, tl, sl))
        stepper.columnconfigure(3, weight=0)
        fa = tk.Frame(stepper, bg=PANEL, cursor="hand2", highlightthickness=1, highlightbackground=BORDER)
        fa.grid(row=0, column=3, sticky="nswe", padx=(8, 0))
        la = tk.Label(fa, text="⚙  Ajustes", bg=PANEL, fg=MUTED, font=ui.display(13), cursor="hand2")
        la.pack(padx=18, pady=9)
        for w_ in (fa, la):
            w_.bind("<Button-1>", lambda e: self.open_settings())
        self._tab_set = (fa, la)
        pages = ttk.Frame(self.body.inner, padding=(16, 8, 16, 8))
        pages.pack(fill="both", expand=True)
        pages.columnconfigure(0, weight=1)
        pages.rowconfigure(0, weight=1)
        self._pages = {k: ttk.Frame(pages) for k in ("vod", "partidas", "videos", "ajustes")}
        for f_ in self._pages.values():
            f_.grid(row=0, column=0, sticky="nsew")
            f_.columnconfigure(0, weight=1)
        self._page = None

        # ---------------- pestaña Ajustes (lo que se toca una vez), con sus secciones a la izquierda
        sw = self._pages["ajustes"]
        sw.columnconfigure(0, weight=0)
        sw.columnconfigure(1, weight=1)
        sw.rowconfigure(0, weight=1)
        self.setwin = sw
        nav = tk.Frame(sw, bg=BG, width=220)
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        nav.pack_propagate(False)
        scard = ui.Card(sw, pad=18, radius=18)
        scard.grid(row=0, column=1, sticky="nsew")
        self._set_title = ttk.Label(scard, text="", style="H.TLabel")
        self._set_title.grid(row=0, column=0, sticky="w", pady=(0, 10))
        scard.columnconfigure(0, weight=1)
        self._set_pages, self._set_btns = {}, {}
        for key, txt in (("cuenta", "Cuenta"), ("cortar", "Qué cortar"), ("videos", "Vídeos y subtítulos"),
                         ("carpeta", "Carpeta y Drive"), ("auto", "Automático"), ("otro", "Otro PC")):
            pg = ttk.Frame(scard, style="Panel.TFrame")
            self._set_pages[key] = (pg, txt)
            lb = tk.Label(nav, text="   " + txt, anchor="w", bg=BG, fg=MUTED, font=ui.semi(10),
                          padx=8, pady=9, cursor="hand2")
            lb.pack(fill="x", pady=1)
            lb.bind("<Button-1>", lambda e, k=key: self.open_settings(k))
            self._set_btns[key] = lb
        self._set_cur = None
        tk.Label(nav, text="Se guarda solo.", bg=BG, fg=ui.DIM, font=ui.body(9)).pack(anchor="w", padx=10,
                                                                                       pady=(14, 0))


        # --- Ajustes · Cuenta
        p1 = self._set_pages["cuenta"][0]
        self.v_riot = tk.StringVar(value=self.cfg.get("riot_id", "Vortexs#vrtxs"))
        self.v_region = tk.StringVar(value=self.cfg.get("region", "eu"))
        self.v_key = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.v_link = tk.StringVar()
        ttk.Label(p1, text="Riot ID").grid(row=1, column=0, sticky="w")
        ttk.Entry(p1, textvariable=self.v_riot, width=22).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(p1, text="Región").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(p1, textvariable=self.v_region, values=REGIONS, width=6,
                     state="readonly").grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(p1, text="Clave API").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(p1, textvariable=self.v_key, width=46, show="•").grid(
            row=3, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(p1, text="La clave de HenrikDev (api.henrikdev.xyz) para leer tus partidas.",
                  style="Muted.TLabel").grid(row=4, column=1, sticky="w", padx=6, pady=(2, 0))

        # --- página Partidas
        pp = self._pages["partidas"]
        p3 = self._panel(pp, "Partidas de este directo")
        p3.grid(row=0, column=0, sticky="nsew")
        p3.columnconfigure(0, weight=1)
        sumr = ttk.Frame(p3, style="Panel.TFrame")
        sumr.grid(row=1, column=0, sticky="we", pady=(0, 8))
        self.kpi = {}
        for key, cap in (("invod", "en el VOD"), ("synced", "sincronizadas"), ("plays", "jugadas para cortar")):
            n_ = tk.Label(sumr, text="—", bg=PANEL, fg=TEXT, font=ui.display(13))
            n_.pack(side="left")
            tk.Label(sumr, text=" " + cap + "     ", bg=PANEL, fg=MUTED, font=ui.body(10)).pack(side="left")
            self.kpi[key] = n_
        self._cut_lbls = []
        self._cut_line(p3).grid(row=5, column=0, sticky="w", pady=(8, 0))
        lk = tk.Label(sumr, text="Marcar las que tienen rondas", bg=PANEL, fg=PURPLE, font=ui.body(10),
                      cursor="hand2")
        lk.pack(side="right")
        lk.bind("<Button-1>", lambda e: self.auto_check())
        self.lbl_hidden = tk.Label(sumr, text="", bg=PANEL, fg=PURPLE, font=ui.body(10), cursor="hand2")
        self.lbl_hidden.pack(side="right", padx=(0, 18))
        self.lbl_hidden.bind("<Button-1>", lambda e: self._toggle_hidden())
        self._show_hidden = False
        self.mcards = tk.Frame(p3, bg=PANEL)
        self.mcards.grid(row=2, column=0, sticky="nsew")
        self.mcards.columnconfigure(0, weight=1)
        self._msel = None
        # (la tabla de antes sigue existiendo, sin mostrarse: guarda la lista y la selección)
        cols = ("use", "date", "map", "agent", "score", "hl", "invod", "sync")
        self.tv_m = ttk.Treeview(p3, columns=cols, show="headings", selectmode="browse", height=5)
        mb = ttk.Frame(p3, style="Panel.TFrame")
        mb.grid(row=3, column=0, sticky="we", pady=(10, 0))
        # izquierda: qué partidas · derecha: su sincronización
        self.btn_load = ui.Button(mb, text="Buscar partidas", command=self.load_matches)
        self.btn_load.pack(side="left")
        self.btn_more = ui.Button(mb, text="Cargar 10 más", style="Ghost.TButton",
                                  command=lambda: self.load_matches(True))
        self.btn_more.pack(side="left", padx=(6, 0))
        ui.Button(mb, text="Añadir por enlace…", style="Ghost.TButton",
                  command=self.add_link_dialog).pack(side="left", padx=(6, 0))
        ui.Button(mb, text="Informes", style="Ghost.TButton", command=self.open_reports).pack(side="right")
        ui.Button(mb, text="Quitar sincro", style="Ghost.TButton", command=self.clear_calib).pack(side="right")
        ui.Button(mb, text="Revisar sincro", style="Ghost.TButton",
                  command=self.review_sync).pack(side="right", padx=(6, 0))
        ui.Button(mb, text="Sincronizar a mano…", command=self.calibrate).pack(side="right", padx=(6, 0))
        ttk.Label(p3, text="Clic en una partida para elegirla (para sincronizar a mano) · clic en ☐ para "
                           "incluirla o no", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 0))

        # --- página VOD y páginas de la ventana de ajustes
        pv = self._panel(self._pages["vod"], "El directo", "de qué directo sacar los vídeos")
        pv.grid(row=0, column=0, sticky="nsew")
        pv.columnconfigure(0, weight=1)
        t_vod = ttk.Frame(pv, style="Panel.TFrame")
        t_vod.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        t_cut = self._set_pages["cortar"][0]
        t_out = self._set_pages["videos"][0]
        t_auto = self._set_pages["auto"][0]
        t_dir = self._set_pages["carpeta"][0]
        t_pc = self._set_pages["otro"][0]
        self.nb2 = _SettingsTabs(self)

        cfg = self.cfg
        self.v_vod = tk.StringVar()
        self.v_start = tk.StringVar()
        self.v_delay = tk.StringVar(value=str(cfg.get("delay", 0)))
        self.v_min = tk.IntVar(value=cfg.get("min_kills", 3))
        self.v_clutch = tk.BooleanVar(value=cfg.get("clutch_on", True))
        self.v_clutchn = tk.IntVar(value=cfg.get("clutch_min", 2))
        self.v_skipbuy = tk.BooleanVar(value=cfg.get("skip_buy", True))
        self.v_pre = tk.StringVar(value=str(cfg.get("pre", 3)))
        self.v_post = tk.StringVar(value=str(cfg.get("post", 4)))
        self.v_mode = tk.StringVar(value=cfg.get("mode", "round"))
        self.v_kpre = tk.StringVar(value=str(cfg.get("kill_pre", 2.5)))
        self.v_kpost = tk.StringVar(value=str(cfg.get("kill_post", 1.5)))
        self.v_kgap = tk.StringVar(value=str(cfg.get("kill_gap", 4.0)))
        enc_default = next((k for k, v in ENCODERS.items() if v == cfg.get("encoder", "auto")), list(ENCODERS)[0])
        self.v_enc = tk.StringVar(value=enc_default)
        self.v_twitch = tk.StringVar()
        self.v_outdir = tk.StringVar(value=cfg.get("out_dir") or os.path.join(
            os.path.expanduser("~"), "Videos", "Vortexs Clips"))
        self.v_auto = tk.BooleanVar(value=cfg.get("auto_process", True))
        self.v_autosync = tk.BooleanVar(value=cfg.get("auto_sync", True))
        self.v_vertical = tk.BooleanVar(value=cfg.get("vertical", True))
        self.v_subs = tk.BooleanVar(value=cfg.get("subtitles", True))
        self.v_subhl = tk.BooleanVar(value=cfg.get("sub_highlight", True))
        self.v_subedit = tk.BooleanVar(value=cfg.get("sub_edit", True))
        self.v_singles = tk.BooleanVar(value=cfg.get("singles", True))
        self.v_suboff = tk.StringVar(value=f"{float(cfg.get('sub_offset', 0.0)):.1f}")
        self.v_cutshift = tk.StringVar(value=f"{float(cfg.get('cut_shift', 0.0)):.1f}")
        self.v_kfeed = tk.BooleanVar(value=cfg.get("killfeed_vertical", True))
        self.v_capcut = tk.BooleanVar(value=cfg.get("capcut_subs", False))
        self.v_drive_link = tk.StringVar(value=cfg.get("drive_link", drive.DEFAULT_LINK))
        _sub = cfg.get("drive_sub", drive.DEFAULT_SUB)
        if drive.EXAMPLE_DAY_ID in (cfg.get("drive_link") or ""):
            _sub = ""
        self.v_drive_sub = tk.StringVar(value=_sub)
        self.v_topweek = tk.BooleanVar(value=cfg.get("top_week", True))
        self.v_partial = tk.BooleanVar(value=cfg.get("partial_download", True))
        self.v_autodel = tk.BooleanVar(value=cfg.get("auto_delete_vod", True))
        self.cam = cfg.get("cam")
        self.v_channel = tk.StringVar(value=cfg.get("watch_channel", ""))
        self.v_watch = tk.BooleanVar(value=cfg.get("watch", False))
        self.v_startup = tk.BooleanVar(value=cfg.get("startup", False))

        # ---- pestaña VOD
        ttk.Label(t_vod, text="Pega uno o varios enlaces de VOD de Twitch, como quieras (juntos, con espacios, "
                              "en líneas…): se separan solos y se procesan en cola").grid(
            row=0, column=0, columnspan=4, sticky="w")
        tf = ttk.Frame(t_vod, style="Panel.TFrame")
        tf.grid(row=1, column=0, columnspan=4, sticky="we", pady=(4, 0))
        ttk.Entry(tf, textvariable=self.v_twitch, width=50).pack(side="left", fill="x", expand=True)
        self.btn_dl = ui.Button(tf, text="Descargar VOD", style="Accent.TButton", command=self.download_vod)
        self.btn_dl.pack(side="left", padx=6)
        self.lbl_queue = ttk.Label(t_vod, text="", style="Muted.TLabel")
        self.lbl_queue.grid(row=2, column=0, columnspan=4, sticky="w")
        # al pegar varios enlaces: se separan en el propio campo y se dice cuántos VODs hay
        self._links_fixing = False

        def on_links(*_a):
            if self._links_fixing:
                return
            raw = self.v_twitch.get()
            urls = twitch.find_vod_urls(raw)
            n = len(urls)
            try:
                self.btn_dl.configure(text=f"Descargar {n} VODs" if n > 1 else "Descargar VOD")
            except AttributeError:
                pass
            if n > 1:
                tidy = "   ".join(urls)
                if raw.strip() != tidy:
                    self._links_fixing = True
                    self.after_idle(lambda: (self.v_twitch.set(tidy), setattr(self, "_links_fixing", False)))
                self.lbl_queue.configure(text=f"{n} VODs detectados: " + ", ".join(u.rsplit("/", 1)[-1] for u in urls)
                                         + " · se descargan y procesan uno detrás de otro")
            elif not self.flow_active and not self.queue:
                self.lbl_queue.configure(text="")
        self.v_twitch.trace_add("write", on_links)
        af = ttk.Frame(t_vod, style="Panel.TFrame")
        af.grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))
        ui.Toggle(af, text="Al terminar la descarga, sacar las rondas y generar los vídeos",
                        variable=self.v_auto).pack(anchor="w")
        ui.Toggle(af, text="Bajar solo los trozos con partidas (no el directo entero: mucho más rápido "
                                 "y ocupa una fracción)", variable=self.v_partial).pack(anchor="w")
        ui.Toggle(af, text="Borrar el VOD descargado 5 días después de sacar sus vídeos "
                                 "(los vídeos generados no se tocan)", variable=self.v_autodel).pack(anchor="w")
        ttk.Label(t_dir, text="Aquí se guardan los vídeos: una carpeta por directo, con su fecha (dd-mm-aaaa).",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        of = ttk.Frame(t_dir, style="Panel.TFrame")
        of.grid(row=1, column=0, columnspan=4, sticky="we", pady=(6, 0))
        ui.Button(of, text="Cambiar carpeta…", command=self.pick_outdir).pack(side="left")
        self.lbl_outdir = ttk.Label(of, text=self.v_outdir.get(), style="Muted.TLabel")
        self.lbl_outdir.pack(side="left", padx=8)
        ttk.Separator(t_dir).grid(row=2, column=0, columnspan=4, sticky="we", pady=12)
        ttk.Separator(t_vod).grid(row=5, column=0, columnspan=4, sticky="we", pady=8)
        vf = ttk.Frame(t_vod, style="Panel.TFrame")
        vf.grid(row=6, column=0, columnspan=4, sticky="we")
        ui.Button(vf, text="o elegir un VOD del PC…", command=self.pick_vod).pack(side="left")
        self.lbl_vod = ttk.Label(vf, text="Ningún vídeo elegido", style="Muted.TLabel")
        self.lbl_vod.pack(side="left", padx=8)
        ttk.Label(t_vod, text="Inicio del directo").grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(t_vod, textvariable=self.v_start, width=19).grid(row=7, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(t_vod, text="dd/mm/aaaa hh:mm:ss (con enlace de Twitch se pone sola)", style="Muted.TLabel").grid(
            row=7, column=2, columnspan=2, sticky="w", pady=(8, 0))

        # ---- pestaña Cortes
        ttk.Label(t_cut, text="Kills mínimas por ronda").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(t_cut, from_=2, to=6, textvariable=self.v_min, width=4).grid(row=0, column=1, sticky="w", padx=6)
        clf = ttk.Frame(t_cut, style="Panel.TFrame")
        clf.grid(row=0, column=2, columnspan=3, sticky="w", padx=(14, 0))
        ui.Toggle(clf, text="y clutches ganados de 1 contra", variable=self.v_clutch).pack(side="left")
        ttk.Spinbox(clf, from_=1, to=5, textvariable=self.v_clutchn, width=3).pack(side="left", padx=4)
        ttk.Label(clf, text="o más").pack(side="left")
        ttk.Label(t_cut, text="Qué cortar").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Radiobutton(t_cut, text="Solo las kills (de la primera a la última, con cortes entre ellas)",
                        value="kills", variable=self.v_mode).grid(row=1, column=1, columnspan=4, sticky="w", pady=(8, 2))
        kfc = ttk.Frame(t_cut, style="Panel.TFrame")
        kfc.grid(row=2, column=1, columnspan=4, sticky="w", padx=(22, 0))
        kf = ttk.Frame(kfc, style="Panel.TFrame")
        kf.pack(anchor="w")
        kf2 = ttk.Frame(kfc, style="Panel.TFrame")
        kf2.pack(anchor="w", pady=(4, 0))
        ttk.Label(kf2, text="Ajuste fino de todos los cortes (s)").pack(side="left")
        ttk.Spinbox(kf2, from_=-3.0, to=3.0, increment=0.1, textvariable=self.v_cutshift, width=6,
                    format="%.1f").pack(side="left", padx=4)
        ttk.Label(kf2, text="− si la kill pasa antes de tiempo (sobra al final) · + si se corta",
                  style="Muted.TLabel").pack(side="left", padx=(2, 0))
        ttk.Label(kf, text="Segundos antes de cada kill").pack(side="left")
        ttk.Entry(kf, textvariable=self.v_kpre, width=4).pack(side="left", padx=4)
        ttk.Label(kf, text="  y después").pack(side="left")
        ttk.Entry(kf, textvariable=self.v_kpost, width=4).pack(side="left", padx=4)
        ttk.Label(kf, text="  · no cortar si entre kills hay menos de").pack(side="left")
        ttk.Entry(kf, textvariable=self.v_kgap, width=4).pack(side="left", padx=4)
        ttk.Label(kf, text="s").pack(side="left")
        ttk.Radiobutton(t_cut, text="La ronda completa", value="round", variable=self.v_mode).grid(
            row=3, column=1, columnspan=4, sticky="w", pady=(6, 2))
        rf = ttk.Frame(t_cut, style="Panel.TFrame")
        rf.grid(row=4, column=1, columnspan=4, sticky="w", padx=(22, 0))
        ui.Toggle(rf, text="Saltar fase de compra", variable=self.v_skipbuy).pack(side="left")
        ttk.Label(rf, text="   Margen antes / después (s)").pack(side="left")
        ttk.Entry(rf, textvariable=self.v_pre, width=4).pack(side="left", padx=4)
        ttk.Entry(rf, textvariable=self.v_post, width=4).pack(side="left")
        ttk.Separator(t_cut).grid(row=5, column=0, columnspan=5, sticky="we", pady=8)
        ui.Toggle(t_cut, text="Sincronizar solo cada partida (cartel de fin de ronda, marcador y killfeed)",
                        variable=self.v_autosync).grid(row=6, column=0, columnspan=5, sticky="w")
        ttk.Label(t_cut, text="Desfase (s)").grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Label(t_cut, text="≈115 s: selección de agente + carga. Se ajusta solo al sincronizar.",
                  style="Muted.TLabel").grid(row=7, column=2, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Entry(t_cut, textvariable=self.v_delay, width=8).grid(row=7, column=1, sticky="w", padx=6, pady=(6, 0))
        self.lbl_delaylog = ttk.Label(t_cut, text="", style="Muted.TLabel")
        self.lbl_delaylog.grid(row=8, column=0, columnspan=5, sticky="w", pady=(2, 0))
        mf_ = ttk.Frame(t_cut, style="Panel.TFrame")
        mf_.grid(row=9, column=0, columnspan=5, sticky="we", pady=(4, 0))
        ui.Button(mf_, text="Elegir registro…", command=self.pick_delay_log).pack(side="left")
        ui.Button(mf_, text="Copiar mi registro a…", command=self.pick_mirror).pack(side="left", padx=6)
        self.lbl_mirror = ttk.Label(mf_, text="", style="Muted.TLabel")
        self.lbl_mirror.pack(side="left", padx=8)

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
        ui.Toggle(vf2, text="Versión vertical 9:16 (como tus TikToks)",
                        variable=self.v_vertical).pack(side="left")
        ui.Button(vf2, text="Elegir cámara…", command=self.pick_cam).pack(side="left", padx=8)
        self.lbl_cam = ttk.Label(vf2, text=self._cam_text(), style="Muted.TLabel")
        self.lbl_cam.pack(side="left")
        ui.Toggle(t_out, text="Subtítulos automáticos en la vertical (lo que dices, en grande bajo la cámara)",
                        variable=self.v_subs).grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ui.Toggle(t_out, text="    Palabras destacadas en amarillo y morado (colores del canal)",
                        variable=self.v_subhl).grid(row=5, column=0, columnspan=4, sticky="w", pady=(2, 0))
        ui.Toggle(t_out, text="    Revisar y editar los subtítulos viendo el vídeo generado",
                        variable=self.v_subedit).grid(row=6, column=0, columnspan=4, sticky="w", pady=(2, 0))
        sf_ = ttk.Frame(t_out, style="Panel.TFrame")
        sf_.grid(row=7, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(sf_, text="    Ajuste de tiempo (s):").pack(side="left")
        ttk.Spinbox(sf_, from_=-3.0, to=3.0, increment=0.1, textvariable=self.v_suboff, width=6,
                    format="%.1f").pack(side="left", padx=4)
        ttk.Label(sf_, text="+ salen más tarde · − salen antes", style="Muted.TLabel").pack(side="left", padx=(2, 12))
        ui.Button(sf_, text="Diccionario…", command=self.edit_sub_dict).pack(side="left")
        ui.Toggle(t_out, text="    Subtítulos para CapCut: la vertical sin subtítulos pegados y al lado su archivo .srt "
                              "(palabra a palabra) para editarlos en CapCut",
                  variable=self.v_capcut).grid(row=8, column=0, columnspan=4, sticky="w", pady=(2, 0))
        ui.Toggle(t_out, text="Además, un clip suelto por cada ronda (en carpetas 3K / 4K / ACE)",
                        variable=self.v_singles).grid(row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ui.Toggle(t_out, text="Killfeed de tus kills en la vertical (recortado del directo, se apila en cada ronda, "
                              "con fade y destello)", variable=self.v_kfeed).grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(2, 0))

        # ---- pestaña Automático
        ttk.Label(t_auto, text="Tu canal de Twitch").grid(row=0, column=0, sticky="w")
        ttk.Entry(t_auto, textvariable=self.v_channel, width=24).grid(row=0, column=1, sticky="w", padx=6)
        ui.Toggle(t_auto, text="Vigilar el canal: cuando acabe un directo, procesar el VOD solo",
                        variable=self.v_watch, command=self.on_watch_toggle).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ui.Toggle(t_auto, text="Abrir Vortexs Clips al encender el PC (minimizado)",
                        variable=self.v_startup, command=self.on_startup_toggle).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.lbl_watch = ttk.Label(t_auto, text="", style="Muted.TLabel")
        self.lbl_watch.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(t_auto, text="Mira cada 10 minutos. Espera a que termine el directo, descarga el VOD y "
                               "genera todos los vídeos.\nEl programa tiene que estar abierto (aunque sea minimizado).",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Separator(t_auto).grid(row=5, column=0, columnspan=4, sticky="we", pady=8)
        tw_ = ttk.Frame(t_auto, style="Panel.TFrame")
        tw_.grid(row=6, column=0, columnspan=4, sticky="w")
        ui.Toggle(tw_, text="Top de la semana cada lunes (las 10 mejores jugadas, horizontal y vertical)",
                  variable=self.v_topweek).pack(side="left")
        ui.Button(tw_, text="Hacer el top ahora…", command=self.top_week_now).pack(side="left", padx=10)
        # Google Drive: la carpeta del directo se sube tal cual a <enlace>/<subcarpeta>/<dd-mm-aaaa>/
        df_ = ttk.Frame(t_dir, style="Panel.TFrame")
        df_.grid(row=3, column=0, columnspan=4, sticky="we")
        ttk.Label(df_, text="Google Drive: carpeta").grid(row=0, column=0, sticky="w")
        ttk.Entry(df_, textvariable=self.v_drive_link, width=46).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(df_, text="dentro de").grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Entry(df_, textvariable=self.v_drive_sub, width=16).grid(row=0, column=3, sticky="w", padx=6)
        dfb = ttk.Frame(df_, style="Panel.TFrame")
        dfb.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.btn_drive_conn = ui.Button(dfb, text="Conectar con Google Drive", command=self.drive_connect)
        self.btn_drive_conn.pack(side="left")
        self.lbl_drive = ttk.Label(dfb, text="", style="Muted.TLabel")
        self.lbl_drive.pack(side="left", padx=10)
        ttk.Label(df_, text="Se sube con el botón «Subir a Drive» de abajo (siempre te pide confirmación), en una "
                            "carpeta con la fecha del directo.", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._drive_status()
        ttk.Label(t_pc, text="Lleva tus ajustes a otro ordenador (incluye tu clave API: no lo compartas).",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        sf = ttk.Frame(t_pc, style="Panel.TFrame")
        sf.grid(row=1, column=0, columnspan=4, sticky="w")
        ttk.Label(sf, text="Usar en otro PC:").pack(side="left")
        ui.Button(sf, text="Exportar ajustes", command=self.export_settings).pack(side="left", padx=6)
        ui.Button(sf, text="Importar ajustes", command=self.import_settings).pack(side="left")

        # --- página Vídeos: las rondas que se van a cortar y la vista previa
        pvd = self._pages["videos"]
        pvd.columnconfigure(0, weight=3)
        pvd.columnconfigure(1, weight=2)
        pvd.rowconfigure(0, weight=1)
        p4 = self._panel(pvd, "Rondas que se van a cortar", "doble clic para verla")
        p4.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        p4.rowconfigure(1, weight=1)
        p4.columnconfigure(0, weight=1)
        cols = ("round", "from", "to", "dur")
        self.tv_c = ttk.Treeview(p4, columns=cols, show="headings", selectmode="extended", height=12)
        for c, t, w in zip(cols, ("Ronda", "Desde (VOD)", "Hasta", "Dur."), (220, 90, 80, 50)):
            self.tv_c.heading(c, text=t)
            self.tv_c.column(c, width=w, anchor="w" if c == "round" else "center", stretch=c == "round")
        self.tv_c.grid(row=1, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(p4, orient="vertical", command=self.tv_c.yview)
        sb2.grid(row=1, column=1, sticky="ns")
        self.tv_c.configure(yscrollcommand=sb2.set)
        self.tv_c.bind("<Double-1>", lambda e: self.preview())
        self.tv_c.tag_configure("alt", background=ROW_ALT)
        cb = ttk.Frame(p4, style="Panel.TFrame")
        cb.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self._cut_line(p4).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ui.Button(cb, text="Calcular rondas", command=self.compute).pack(side="left")
        ui.Button(cb, text="Ver ronda", style="Ghost.TButton", command=self.preview).pack(side="left", padx=6)
        ui.Button(cb, text="Quitar", style="Ghost.TButton", command=self.remove_clips).pack(side="left")
        self.btn_drive = ui.Button(cb, text="Subir a Drive…", style="Ghost.TButton", command=self.drive_upload_last)
        self.btn_drive.pack(side="right")
        self.btn_subs = ui.Button(cb, text="Corregir subtítulos…", style="Ghost.TButton",
                                  command=self.fix_subtitles_later)
        self.btn_subs.pack(side="right", padx=6)
        p5 = self._panel(pvd, "Vista previa")
        p5.grid(row=0, column=1, sticky="nsew")
        self.tab_prev = ttk.Frame(p5, style="Panel.TFrame")
        self.tab_prev.grid(row=1, column=0, sticky="nsew")
        self.player = player.Player(self.tab_prev, lambda: self.v_vod.get(), fit=True,
                                    on_expand=self.big_preview)
        self.player.pack(anchor="n")
        self.nb4 = _PageTabs(self, "videos")

        # --- pie: una fila (estado + barra fina) y a la derecha «Cancelar», «Más» y UN botón principal
        foot = ui.Card(self, pad=12, radius=16)
        foot.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        self.body.pack(fill="both", expand=True)
        self.step_names = ["1 · Descargar VOD", "2 · Buscar partidas", "3 · Sincronizar",
                           "4 · Sacar rondas", "5 · Generar vídeos"]
        self.chip_lbls = []
        row = ttk.Frame(foot, style="Panel.TFrame")
        row.pack(fill="x")
        right = ttk.Frame(row, style="Panel.TFrame")
        right.pack(side="right", padx=(16, 0))
        left = ttk.Frame(row, style="Panel.TFrame")
        left.pack(side="left", fill="x", expand=True)
        l1 = ttk.Frame(left, style="Panel.TFrame")
        l1.pack(fill="x")
        self.lbl_step = ttk.Label(l1, text="Listo para empezar", style="StepS.TLabel")
        self.lbl_step.pack(side="left")
        self.lbl_status = ttk.Label(l1, text="Pega un enlace de Twitch o elige un VOD.", style="Muted.TLabel")
        self.lbl_status.pack(side="left", padx=(10, 0))
        self.lbl_pct = ttk.Label(l1, text="", style="PctS.TLabel")
        self.lbl_pct.pack(side="right")
        self.lbl_time = ttk.Label(l1, text="", style="Muted.TLabel")
        self.lbl_time.pack(side="right", padx=10)
        self.pb = ui.GradBar(left, mode="determinate", maximum=1)
        self.pb.pack(fill="x", pady=(6, 0))
        self.btn_render = ui.Button(right, text="Descargar VOD", style="Yellow.TButton", command=self.primary_action)
        self.btn_render.pack(side="right")
        self.btn_cancel = ui.Button(right, text="Cancelar", style="Ghost.TButton", command=self.cancel)
        self.btn_cancel.configure(state="disabled")      # (el botón no toma state= al crearse)
        self.btn_cancel.pack(side="right", padx=(0, 8))
        self._lay = None
        self.show_page("vod")
        self.after(500, self._ui_tick)

    def big_preview(self):
        """La vista previa en una ventana grande (se puede agrandar o maximizar)."""
        p = self.player
        was = p.playing
        pos = p._now() if was else p.pos
        p.pause()
        dlg = tk.Toplevel(self)
        dlg.title("Vista previa")
        dlg.configure(bg=PANEL)
        dlg.geometry("1180x760")
        fr = ttk.Frame(dlg, style="Panel.TFrame", padding=12)
        fr.pack(fill="both", expand=True)
        big = player.Player(fr, lambda: self.v_vod.get(), fit=True)
        big.pack(anchor="n")
        a, b = p.range
        big.open(a + 30, max(a + 31, b - 30), autoplay=False)
        dlg.after(350, lambda: (big.seek(pos), big.play() if was else None))

        def close():
            big.stop()
            dlg.destroy()
        dlg.protocol("WM_DELETE_WINDOW", close)
        return dlg

    # ------------------------------------------------------------ navegación (pasos y ajustes)
    def show_page(self, key):
        if key not in self._pages:
            return
        if self._page == "ajustes" and key != "ajustes":
            self.after_idle(self.close_settings)
        if key == "ajustes" and not self._set_cur:
            self._page = key        # (open_settings vuelve a llamar aquí con la sección elegida)
            self.open_settings("cuenta")
            return
        self._page = key
        self._pages[key].tkraise()
        self._paint_steps()
        try:
            self.body.cv.yview_moveto(0)
        except Exception:
            pass

    def _step_state(self):
        """(estado, texto) de cada paso: 'done', 'now' o 'todo'."""
        st = self.settings()
        has_vod = bool(self.vod_info)
        ch = [self.matches[i] for i in self.checked if i in self.matches]
        synced = [m for m in ch if self._is_synced(m.match_id)]
        n_cl = len({c.group for c in self.clips}) if self.clips else 0
        vod_txt = ""
        if has_vod:
            vod_txt = (self.v_start.get().split(" ")[0] or "listo")
        p_txt = ""
        if self.matches:
            p_txt = f"{len(synced)}/{len(ch)} sincronizadas" if ch else "ninguna marcada"
        v_txt = f"{n_cl} jugadas" if n_cl else ""
        s_v = "done" if has_vod else "now"
        s_p = "todo" if not has_vod else ("done" if ch and len(synced) == len(ch) else "now")
        s_c = "todo" if s_p != "done" else ("done" if getattr(self, "_rendered_ok", False) else "now")
        del st
        return [(s_v, vod_txt), (s_p, p_txt), (s_c, v_txt)]

    def _is_synced(self, mid):
        c = self.calib.get(mid) or {}
        return bool(c.get("auto") or c.get("first_kill_vod_s") is not None or c.get("failed"))

    def _paint_steps(self):
        try:
            states = self._step_state()
        except Exception:
            return
        fa, la = self._tab_set
        on = self._page == "ajustes"
        fa.configure(bg=ui.SEL if on else PANEL, highlightthickness=2 if on else 1,
                     highlightbackground=PURPLE if on else BORDER)
        la.configure(bg=ui.SEL if on else PANEL, fg=TEXT if on else MUTED)
        tint = {"done": ("#0f2a20", ui.GREEN), "now": ("#2b2908", YELLOW), "todo": (PANEL, ui.DIM)}
        for (key, f_, ic, tl, sl), (st, txt) in zip(self._steps, states):
            cur = key == self._page
            bg, fg = tint[st]
            ic.configure(text={"done": "✓", "now": "●", "todo": "○"}[st], fg=fg)
            # la página que estás viendo, con borde (morado); el resto, con el borde de su color
            f_.configure(bg=bg, highlightthickness=2 if cur else 1,
                         highlightbackground=PURPLE if cur else (fg if st != "todo" else BORDER))
            for w_ in (ic, tl, sl):
                w_.configure(bg=bg)
            tl.configure(fg=fg if st != "todo" else (TEXT if cur else ui.DIM))
            sl.configure(text=txt, fg=fg if st != "todo" else ui.DIM)

    def open_settings(self, key=None):
        key = key or self._set_cur or "cuenta"
        if self._set_cur and self._set_cur != key:
            self._set_pages[self._set_cur][0].grid_forget()
        pg, txt = self._set_pages[key]
        pg.grid(row=1, column=0, sticky="nsew")
        self._set_title.configure(text=txt)
        for k, lb in self._set_btns.items():
            lb.configure(bg=ui.SEL if k == key else BG, fg=TEXT if k == key else MUTED)
        self._set_cur = key
        self.show_page("ajustes")

    def close_settings(self):
        """Al salir de la pestaña Ajustes: se guardan y se recalcula la lista de partidas."""
        try:
            self.persist()
        except Exception:
            pass
        self.refresh_matches()

    def _cut_line(self, parent):
        """«Se corta: 3K o más · clutches 1v2 o más · solo las kills   Cambiar opciones de corte»."""
        f_ = tk.Frame(parent, bg=PANEL)
        lb = tk.Label(f_, text="", bg=PANEL, fg=MUTED, font=ui.body(10))
        lb.pack(side="left")
        ch = tk.Label(f_, text="Cambiar opciones de corte", bg=PANEL, fg=PURPLE, font=ui.semi(10), cursor="hand2")
        ch.pack(side="left", padx=(12, 0))
        ch.bind("<Button-1>", lambda e: self.open_settings("cortar"))
        self._cut_lbls.append(lb)
        return f_

    def _cut_text(self):
        try:
            mk = int(self.v_min.get() or 3)
        except (tk.TclError, ValueError):
            mk = 3
        parts = [f"{mk}K o más"]
        if self.v_clutch.get():
            try:
                parts.append(f"clutches 1v{int(self.v_clutchn.get() or 2)} o más")
            except (tk.TclError, ValueError):
                parts.append("clutches")
        parts.append("solo las kills" if self.v_mode.get() == "kills" else "la ronda entera")
        return "Se corta: " + " · ".join(parts)

    def open_outdir(self):
        d = self.v_outdir.get()
        os.makedirs(d, exist_ok=True)
        if os.name == "nt":
            os.startfile(d)
        else:
            messagebox.showinfo(APP_NAME, d)

    # ------------------------------------------------------------ partidas en tarjetas
    def _toggle_hidden(self):
        self._show_hidden = not self._show_hidden
        self.render_match_cards()

    def _round_tags(self, m, st):
        out = []
        for r in m.highlight_rounds(st.min_kills, st.clutch_min):
            k = r.my_kills
            lab = {3: "3K", 4: "4K"}.get(k, "ACE" if k >= 5 else "")
            if core.is_clutch(r, st.clutch_min):
                lab = (lab + " " if lab else "") + f"1v{r.clutch_vs}"
            out.append(f"{lab or f'{k}K'} R{r.index + 1}")
        return out

    def _sync_words(self, m, inv):
        """Estado de la partida en palabras: (texto, color)."""
        if inv is False:
            return "No está en este directo", ui.DIM
        c = self.calib.get(m.match_id) or {}
        if c.get("check") == "dudosa":
            return "⚠ Revisar", YELLOW
        if c.get("auto") and (c.get("approx") or float(c.get("conf") or 0) < 0.5):
            return "≈ Aproximada (revísala)", YELLOW
        if c.get("auto"):
            return f"✓ Lista ({int(float(c.get('conf') or 0) * 100)}%)", ui.GREEN
        if c.get("first_kill_vod_s") is not None:
            return "✓ Lista (a mano)", ui.GREEN
        if c.get("failed"):
            return "✕ No se encontró", ui.RED
        return "Sin sincronizar", "#ff9f43"

    def render_match_cards(self):
        box = getattr(self, "mcards", None)
        if box is None:
            return
        for w_ in box.winfo_children():
            w_.destroy()
        st = self.settings()
        try:
            vs = self.vod_start_utc() if self.vod_info else None
        except ValueError:
            vs = None
        rows = []
        for m in self.matches.values():
            inv = None
            if vs and self.vod_info:
                inv = core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib, self.delay_fn_for_vod())
            tags = self._round_tags(m, st)
            group = 0 if (inv is not False and tags) else (1 if inv is not False else 2)
            rows.append((group, -m.started_at.timestamp(), m, inv, tags))
        rows.sort(key=lambda r: (r[0], r[1]))
        hidden = [r for r in rows if r[0] == 1]
        if hidden:
            self.lbl_hidden.configure(text=(f"Ocultar las {len(hidden)} sin rondas" if self._show_hidden else
                                            f"{len(hidden)} sin rondas que cortar (ocultas) · ver"))
        else:
            self.lbl_hidden.configure(text="")
        if not rows:
            tk.Label(box, text="Todavía no hay partidas. Pulsa «Buscar partidas» (o el botón de abajo).",
                     bg=PANEL, fg=MUTED, font=ui.body(10)).grid(row=0, column=0, sticky="w", pady=10)
            return
        r_ = 0
        for group, _t, m, inv, tags in rows:
            if group == 1 and not self._show_hidden:
                continue
            sel = m.match_id == self._msel
            dim = group == 2
            bg = ui.SEL if sel else (FIELD if not dim else PANEL)
            card = tk.Frame(box, bg=bg, highlightthickness=1,
                            highlightbackground=PURPLE if sel else BORDER, cursor="hand2")
            card.grid(row=r_, column=0, sticky="we", pady=2)
            r_ += 1
            chk = tk.Label(card, text="☑" if m.match_id in self.checked else "☐", bg=bg,
                           fg=YELLOW if m.match_id in self.checked else ui.DIM, font=ui.body(15), cursor="hand2")
            chk.pack(side="left", padx=(12, 10), pady=3)
            info = tk.Frame(card, bg=bg)
            info.pack(side="left", pady=4)
            fg1 = TEXT if not dim else ui.DIM
            tk.Label(info, text=f"{m.map_name} · {m.agent}", bg=bg, fg=fg1, font=ui.semi(10),
                     anchor="w", width=20).pack(anchor="w")
            local = m.started_at.astimezone()
            tk.Label(info, text=f"{local.strftime('%d/%m %H:%M')} · {m.score}", bg=bg, fg=ui.DIM,
                     font=ui.body(9), anchor="w").pack(anchor="w")
            mid_ = tk.Frame(card, bg=bg)
            mid_.pack(side="left", padx=(14, 0), fill="x", expand=True)
            if tags and not dim:
                for t_ in tags[:8]:
                    tk.Label(mid_, text=t_, bg=ui.SEL if not sel else FIELD, fg=PURPLE, font=ui.semi(9),
                             padx=8, pady=2).pack(side="left", padx=(0, 6))
                if len(tags) > 8:
                    tk.Label(mid_, text=f"+{len(tags) - 8}", bg=bg, fg=ui.DIM, font=ui.body(9)).pack(side="left")
            else:
                tk.Label(mid_, text=(f"{len(tags)} rondas buenas" if tags else "sin rondas que cortar"),
                         bg=bg, fg=ui.DIM, font=ui.body(9)).pack(side="left")
            txt, col = self._sync_words(m, inv)
            stl = tk.Label(card, text=txt, bg=bg, fg=col, font=ui.semi(10))
            stl.pack(side="right", padx=14)

            def toggle(e, mid=m.match_id):
                if mid in self.checked:
                    self.checked.discard(mid)
                else:
                    self.checked.add(mid)
                self.refresh_matches()
                return "break"

            def pick(e, mid=m.match_id):
                self._msel = mid
                try:
                    self.tv_m.selection_set(mid)
                except tk.TclError:
                    pass
                self.render_match_cards()

            chk.bind("<Button-1>", toggle)
            for w_ in [card, info, mid_, stl] + list(info.winfo_children()) + list(mid_.winfo_children()):
                w_.bind("<Button-1>", pick)
                w_.bind("<Double-1>", lambda e, mid=m.match_id: (pick(e, mid), self.calibrate()))
        self._paint_steps()

    # ------------------------------------------------------------ botón principal y menú «Más»
    def _primary(self):
        """(texto, acción) del botón principal según en qué punto estás."""
        if not self.vod_info:
            if self.v_twitch.get().strip():
                n = len(twitch.find_vod_urls(self.v_twitch.get()))
                return ("Descargar VOD" if n <= 1 else f"Descargar {n} VODs"), self.download_vod
            return "Elegir el directo", lambda: (self.show_page("vod"), self.status(
                "Pega el enlace del directo de Twitch arriba (o elige un vídeo del PC)."))
        if not self.matches:
            return "Buscar partidas", lambda: (self.show_page("partidas"), self.load_matches())
        ch = [i for i in self.checked if i in self.matches]
        pend = [i for i in ch if not self._is_synced(i)]
        if not ch:
            return "Marca alguna partida", lambda: self.show_page("partidas")
        if pend:
            return (f"Sincronizar {len(pend)} partida" + ("s" if len(pend) > 1 else ""),
                    lambda: (self.show_page("partidas"), self.sync_checked()))
        return "GENERAR VÍDEOS", lambda: (self.show_page("videos"), self.render())

    def primary_action(self):
        _t, fn = self._primary()
        fn()

    def _ui_tick(self):
        try:
            if str(self.btn_cancel.cget("state")) != "normal":
                t, _fn = self._primary()
                if self.btn_render.cget("text") != t:
                    self.btn_render.configure(text=t)
            self._paint_steps()
            ct = self._cut_text()
            for lb in getattr(self, "_cut_lbls", []):
                if lb.cget("text") != ct:
                    lb.configure(text=ct)
            hv = ""
            if self.vod_info and self.v_start.get().strip():
                hv = "Directo del " + self.v_start.get().split(" ")[0]
            if hv != self._head_vod:
                self._head_vod = hv
                self._head._last = None
                self._head._redraw(self._head.winfo_width())
        except Exception:
            pass
        self.after(700, self._ui_tick)

    def add_link_dialog(self):
        from tkinter import simpledialog
        url = simpledialog.askstring(APP_NAME, "Pega el enlace de la partida (tracker.gg):", parent=self)
        if url:
            self.v_link.set(url.strip())
            self.show_page("partidas")
            self.add_by_link()

    # ------------------------------------------------------------ ventana adaptable
    def _relayout(self):
        """Si la ventana es estrecha, las tarjetas van una debajo de otra en vez de recortarse."""
        L = getattr(self, "_lay", None)
        if not L:
            return      # (diseño por páginas: no hay tarjetas que recolocar)
        try:
            w = self.body.cv.winfo_width()
            if w < 100:
                return
            mw = L["maxw"]
            for i, p in enumerate(L["p"]):
                mw[i] = max(mw[i], p._cv.winfo_reqwidth())
            top_wide = w >= mw[0] + mw[1] + 60
            mid_wide = w >= mw[2] + mw[3] + 60
            mode = (top_wide, mid_wide)
            if mode == L["mode"]:
                return
            L["mode"] = mode
            p1, p2, p3, p4o = L["p"]
            top, mid = L["top"], L["mid"]
            if top_wide:
                p1.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 12), pady=0)
                p2.grid(row=0, column=1, columnspan=1, sticky="nsew", padx=0, pady=0)
            else:
                p1.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=0, pady=(0, 12))
                p2.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=0, pady=0)
            if mid_wide:
                p3.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 12), pady=0)
                p4o.grid(row=0, column=1, columnspan=1, sticky="nsew", padx=0, pady=0)
                mid.rowconfigure(0, weight=1, minsize=280)
                mid.rowconfigure(1, weight=0, minsize=0)
            else:
                p3.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=0, pady=(0, 12))
                p4o.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=0, pady=0)
                mid.rowconfigure(0, weight=1, minsize=300)
                mid.rowconfigure(1, weight=1, minsize=420)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ util
    def status(self, txt):
        if txt:
            t = str(txt).replace("\n", " ")
            self.lbl_status.configure(text=t if len(t) <= 95 else t[:92] + "…")

    def set_step(self, idx, title=None):
        """Marca el paso actual (0-3) o None para reposo."""
        import time
        for i, lb in enumerate(self.chip_lbls):
            st = "ChipOff.TLabel" if idx is None or i > idx else ("ChipOn.TLabel" if i == idx else "ChipDone.TLabel")
            lb.configure(style=st)
        if idx is not None:
            # la pantalla sigue al trabajo: VOD → Partidas (buscar y sincronizar) → Vídeos
            try:
                self.show_page(("vod", "partidas", "partidas", "videos", "videos")[idx])
            except Exception:
                pass
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
        self.lbl_time.configure(text=f"{el // 60}:{el % 60:02d}")
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
        try:
            cmin = int(self.v_clutchn.get() or 2) if self.v_clutch.get() else 0
        except (tk.TclError, ValueError):
            cmin = 2 if self.v_clutch.get() else 0
        return core.CutSettings(min_kills=int(self.v_min.get() or 3), clutch_min=cmin,
                                skip_buy_phase=self.v_skipbuy.get(),
                                pre_roll_s=max(0.0, f(self.v_pre, 3)), post_roll_s=max(0.0, f(self.v_post, 4)),
                                global_adjust_s=f(self.v_delay, 0),
                                # sin negativos: cortarían la propia kill
                                mode=self.v_mode.get(), kill_pre_s=max(0.0, f(self.v_kpre, 2.5)),
                                kill_post_s=max(0.0, f(self.v_kpost, 1.5)),
                                merge_gap_s=f(self.v_kgap, 4.0),
                                residual_s=float(self.cfg.get("residual_s", 0.0)),
                                cut_shift_s=max(-3.0, min(3.0, f(self.v_cutshift, 0.0))))

    # ------------------------------------------------------------ delay de Vortexs Live
    def load_delay(self):
        custom = self.cfg.get("delay_log_path")
        try:
            rows = core.load_delay_log(custom if custom and os.path.isfile(custom) else None)
            if custom and os.path.isfile(custom):
                old = os.path.join(os.path.dirname(custom), "delay_log.old.csv")
                if os.path.isfile(old):
                    rows = sorted(rows + core.load_delay_log(old))
        except Exception:
            rows = []
        self.delay_fn = core.make_delay_fn(rows)
        core.DELAY_FN = self.delay_fn          # cada kill con el delay de SU momento (cambios a mitad de partida)
        if rows:
            self.lbl_delaylog.configure(text=f"✔ Registro de delay de Vortexs Live encontrado ({len(rows)} cambios): "
                                             "el delay de cada momento se suma al desfase solo.")
        else:
            self.lbl_delaylog.configure(text="Sin registro de delay de Vortexs Live: si pusiste delay, súmalo al desfase.")

    def pick_delay_log(self):
        """Para procesar en un PC distinto del que streamea: usar una copia del registro de delay."""
        p = filedialog.askopenfilename(title="Registro de delay de Vortexs Live (delay_log.csv)",
                                       filetypes=[("Registro de delay", "delay_log*.csv"), ("CSV", "*.csv")])
        if not p:
            if self.cfg.get("delay_log_path") and messagebox.askyesno(
                    APP_NAME, "¿Volver a usar el registro de este PC (el de Vortexs Live)?"):
                self.cfg.pop("delay_log_path", None)
                self.persist()
                self.load_delay()
            return
        self.cfg["delay_log_path"] = p
        self.persist()
        self.load_delay()

    # ------------------------------------------------------------ copia del registro (PC de streaming)
    def pick_mirror(self):
        d = filedialog.askdirectory(title="Carpeta donde copiar el registro de delay (p. ej. tu carpeta de Google Drive)")
        if not d:
            if self.cfg.get("delay_log_mirror") and messagebox.askyesno(APP_NAME, "¿Dejar de copiar el registro?"):
                self.cfg.pop("delay_log_mirror", None)
                self.persist()
                self.update_mirror_label()
            return
        self.cfg["delay_log_mirror"] = d
        self.persist()
        self.mirror_delay_log()

    def update_mirror_label(self, extra=""):
        d = self.cfg.get("delay_log_mirror")
        self.lbl_mirror.configure(text=(f"Se copia a: {d}{extra}" if d else
                                        "En el PC donde streameas: copia el registro a una carpeta compartida."))

    def mirror_delay_log(self, schedule=False):
        """Copia delay_log.csv de Vortexs Live a la carpeta elegida (si ha cambiado)."""
        import shutil as _sh
        d = self.cfg.get("delay_log_mirror")
        extra = ""
        if d and os.path.isdir(d):
            copied = 0
            for src in core.delay_log_candidates():
                if os.path.isfile(src):
                    dst = os.path.join(d, os.path.basename(src))
                    try:
                        if not os.path.isfile(dst) or os.path.getmtime(src) > os.path.getmtime(dst) + 1:
                            _sh.copy2(src, dst)
                        copied += 1
                    except OSError:
                        pass
            extra = f"  ·  última copia {time.strftime('%H:%M')}" if copied else "  ·  (aún no hay registro en este PC)"
        elif d:
            extra = "  ·  ⚠ la carpeta no existe ahora mismo"
        self.update_mirror_label(extra)
        if schedule:
            self.after(10 * 60_000, lambda: self.mirror_delay_log(True))

    # ------------------------------------------------------------ ajustes entre PCs
    def export_settings(self):
        p = filedialog.asksaveasfilename(title="Exportar ajustes de Vortexs Clips", defaultextension=".json",
                                         initialfile="VortexsClips-ajustes.json", filetypes=[("Ajustes", "*.json")])
        if not p:
            return
        self.persist()
        keep = {k: v for k, v in self.cfg.items() if k not in ("calib", "last_vod", "processed_vods",
                                                              "migrate_asked", "cam_asked", "delay_log_mirror")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=2, ensure_ascii=False)
        messagebox.showinfo(APP_NAME, "Ajustes exportados (incluye tu clave de la API: no lo compartas).\n\n"
                                      "En el otro PC: pestaña Automático → «Importar ajustes».")

    def import_settings(self):
        p = filedialog.askopenfilename(title="Importar ajustes de Vortexs Clips", filetypes=[("Ajustes", "*.json")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No se pudo leer el archivo:\n{e}")
            return
        data.pop("out_dir", None) if not os.path.isdir(data.get("out_dir", "")) else None
        data.pop("delay_log_path", None) if not os.path.isfile(data.get("delay_log_path", "")) else None
        self.cfg.update(data)
        save_cfg(self.cfg)
        messagebox.showinfo(APP_NAME, "Ajustes importados. El programa se va a reiniciar para aplicarlos.")
        updater.restart()

    def delay_fn_for_vod(self):
        return getattr(self, "delay_fn", None)

    def _vod_key(self) -> str:
        try:
            return os.path.basename(self.v_vod.get() or "")
        except Exception:
            return ""

    @property
    def calib(self) -> dict:
        """Sincronizaciones de las partidas para el VOD abierto."""
        key = self._vod_key()
        d = self._calib_by_vod.setdefault(key, {})
        mig = self.__dict__.setdefault("_calib_migrated", set())
        if key and key not in mig and self.matches and self.vod_info:
            mig.add(key)
            for mid, c in self._migrate_legacy(key).items():
                d.setdefault(mid, c)
        return d

    @calib.setter
    def calib(self, value):
        self._calib_by_vod[self._vod_key()] = value

    def _migrate_legacy(self, key) -> dict:
        """De la lista común antigua, solo las sincronizaciones que de verdad son de este VOD: su sitio
        tiene que cuadrar con la hora de la partida (± unos minutos de delay); las de otros días no."""
        out = {}
        if not self._calib_legacy:
            return out
        try:
            vs = self.vod_start_utc()
            dur = float((self.vod_info or {}).get("duration") or 0)
        except Exception:
            return out
        for mid, c in self._calib_legacy.items():
            m = self.matches.get(mid)
            if m is None or not isinstance(c, dict):
                continue
            try:
                off = core.match_offset_s(m, vs, {mid: c})
                hora = core.match_offset_s(m, vs, {})
            except Exception:
                continue
            if c.get("failed"):
                continue
            if abs(off - hora) <= 420 and (not dur or -60 <= off <= dur + 60):
                out[mid] = dict(c)
        return out

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
                         "clutch_on": self.v_clutch.get(), "clutch_min": self.settings().clutch_min or 2,
                         "pre": self.v_pre.get(), "post": self.v_post.get(),
                         "encoder": ENCODERS[self.v_enc.get()],
                         "calib_by_vod": {k: v for k, v in self._calib_by_vod.items() if k and v},
                         "out_dir": self.v_outdir.get(),
                         "auto_process": self.v_auto.get(), "auto_sync": self.v_autosync.get(),
                         "vertical": self.v_vertical.get(), "singles": self.v_singles.get(),
                         "cam": self.cam, "subtitles": self.v_subs.get(), "mode": self.v_mode.get(),
                         "sub_highlight": self.v_subhl.get(), "sub_edit": self.v_subedit.get(),
                         "kill_pre": self.v_kpre.get(), "kill_post": self.v_kpost.get(),
                         "kill_gap": self.v_kgap.get(),
                         "watch_channel": self.v_channel.get(), "watch": self.v_watch.get(),
                         "startup": self.v_startup.get(), "partial_download": self.v_partial.get(),
                         "auto_delete_vod": self.v_autodel.get(), "sub_offset": self.sub_offset(),
                         "top_week": self.v_topweek.get(), "capcut_subs": self.v_capcut.get(), "killfeed_vertical": self.v_kfeed.get(),
                         "drive_link": self.v_drive_link.get().strip(), "drive_sub": self.v_drive_sub.get().strip(),
                         "cut_shift": self.settings().cut_shift_s})
        try:
            save_cfg(self.cfg)
        except Exception:
            pass

    def run_bg(self, work, done, err_title="Error"):
        if self.busy:
            return
        self.busy = True
        self.keep_awake(True)

        def target():
            try:
                res = work()
                self.after(0, lambda: (setattr(self, "busy", False), done(res),
                                       self.after(1500, lambda: self.keep_awake(self.busy or self.flow_active))))
            except Exception as e:
                msg = str(e)

                def fail():
                    self.busy = False
                    self.after(1500, lambda: self.keep_awake(self.busy or self.flow_active))
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
            core.API_STATUS["from_cache"] = False
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
            n_vod = getattr(self, "in_vod_count", n_in)
            extra = (f" · {n_vod} en el VOD" + (f", {n_in} con rondas que cortar" if n_vod != n_in else "")
                     if self.vod_info else "")
            if core.API_STATUS.get("from_cache"):
                extra += " · ⚠ la API no respondía: uso las partidas guardadas (pueden faltar las más nuevas)"
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
        self.after_idle(self.update_kpis)
        self.tv_m.delete(*self.tv_m.get_children())
        st = self.settings()
        try:
            vs = self.vod_start_utc() if self.vod_info else None
        except ValueError:
            vs = None
        for m in sorted(self.matches.values(), key=lambda x: x.started_at, reverse=True):
            local = m.started_at.astimezone()
            n = len(m.highlight_rounds(st.min_kills, st.clutch_min))
            if vs and self.vod_info:
                inv = core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib, self.delay_fn_for_vod())
                invt = "Sí" if inv else "No"
            else:
                inv, invt = None, "—"
            c = self.calib.get(m.match_id) or {}
            sync_txt = ("⚠ Dudosa (revísala)" if c.get("check") == "dudosa" else
                        "Auto ≈ (aprox.)" if c.get("auto") and c.get("approx") else
                        f"Auto ✔ ({int(c.get('conf', 0) * 100)}%)" if c.get("auto") else
                        "Manual ✔" if c.get("first_kill_vod_s") is not None else
                        "✖ No se encontró" if c.get("failed") else "Por hora")
            if inv is False:
                tags = ("out",)
            elif inv is None:
                tags = ()
            else:
                tags = (sync_tag(c),)
            tags = tuple(tags) + (("alt",) if len(self.tv_m.get_children()) % 2 else ())
            self.tv_m.insert("", "end", iid=m.match_id, tags=tags,
                             values=("☑" if m.match_id in self.checked else "☐",
                                     local.strftime("%d/%m %H:%M"), m.map_name, m.agent, m.score,
                                     n, invt, sync_txt))
        self.render_match_cards()

    def update_kpis(self):
        """Cifras del resumen (tarjeta 1)."""
        if not getattr(self, "kpi", None):
            return
        try:
            vs = self.vod_start_utc() if self.vod_info else None
        except ValueError:
            vs = None
        st = self.settings()
        inv = synced = 0
        for m in self.matches.values():
            if vs and core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib, self.delay_fn_for_vod()):
                inv += 1
                if sync_tag(self.calib.get(m.match_id) or {}) == "s_ok":
                    synced += 1
        self.kpi["invod"].configure(text=str(inv) if vs else "—")
        self.kpi["synced"].configure(text=f"{synced}/{inv}" if vs else "—")
        self.kpi["plays"].configure(text=str(len({c.group for c in self.clips})) if self.clips else "—")

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
        inside = [m for m in self.matches.values()
                  if core.match_in_vod(m, vs, self.vod_info["duration"], st, self.calib,
                                       self.delay_fn_for_vod())]
        self.in_vod_count = len(inside)
        # las partidas sin ninguna ronda que cortar no se marcan: sincronizarlas es tiempo perdido
        # (se pueden marcar a mano si se quiere)
        self.checked = {m.match_id for m in inside if m.highlight_rounds(st.min_kills, st.clutch_min)}
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
            self.learn_in_background(m, t - first / 1000)
            # cuánto estaba desplazada según la hora -> ofrecer aplicarlo a todas
            try:
                by_time = core.match_offset_s(m, self.vod_start_utc(), {})
                dfn_ = self.delay_fn_for_vod()
                d_ = dfn_(m.started_at) if dfn_ is not None else None
                diff = (t - first / 1000) - by_time - (d_ or 0.0)     # desfase base (sin el delay)
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
        ui.Button(bf, text="Buscar en el VOD", style="Purple.TButton", command=look).pack(side="left")
        ui.Button(bf, text="Guardar", style="Accent.TButton", command=ok).pack(side="left", padx=6)
        dlg.bind("<Return>", lambda ev: ok())

    def learn_in_background(self, m, offset_s):
        """Tras sincronizar a mano: aprender (sin bloquear) qué vio cada método en el sitio correcto."""
        vod = self.v_vod.get()
        dur = (self.vod_info or {}).get("duration") or 0
        if not vod or not os.path.isfile(vod):
            return

        def target():
            try:
                rows, sug = sync.learn_from_manual(vod, m, offset_s, dur)
                if sug is not None:
                    self.after(0, lambda: self._manual_suspicious(m, offset_s, sug))
                    return
                good = sum(1 for r in rows if r["true"])
                self.after(0, lambda: self.status(f"Aprendido de tu sincronización de {m.map_name}: "
                                                  f"{good} método(s) lo veían bien. Se ajustará solo."))
            except Exception:
                pass
        threading.Thread(target=target, daemon=True).start()

    def _manual_suspicious(self, m, offset_s, sug):
        """Tu sincronización a mano no cuadra con lo que se ve clarísimo en el vídeo: ofrecer corregirla."""
        if not messagebox.askyesno(
                APP_NAME, f"En {m.map_name} {m.started_at.astimezone():%d/%m %H:%M} el momento que has puesto a mano "
                          f"parece {abs(sug):.1f} s {'adelantado' if sug > 0 else 'atrasado'}: los finales de ronda "
                          f"y el marcador se ven con mucha claridad {sug:+.1f} s respecto a él.\n\n"
                          "¿Lo corrijo a ese sitio? (Si estás seguro de tu momento, pulsa No y se queda como está.)"):
            return
        c = self.calib.get(m.match_id) or {}
        new = offset_s + sug
        self.calib[m.match_id] = {"offset_s": new, "auto": True, "conf": 0.9, "approx": False,
                                  "extra_s": c.get("extra_s", 0.0)}
        self.persist()
        self.refresh_matches()
        if self.clips or self.checked:
            self.compute()
        self.status(f"{m.map_name}: corregida {sug:+.1f} s.")

    def clear_calib(self):
        m = self.selected_match()
        if m and m.match_id in self.calib:
            del self.calib[m.match_id]
            self.persist()
            self.refresh_matches()

    # ------------------------------------------------------------ VOD
    def pick_vod(self):
        p = filedialog.askopenfilename(title="Elige el VOD descargado",
                                       filetypes=[("Vídeo o VOD por partidas", "*.mp4 *.mkv *.mov *.ts *.flv *.partes.json"),
                                                  ("Todo", "*.*")])
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
        extra = f"  ·  solo partidas ({info['parts']} trozos)" if info.get("parts") else ""
        self.lbl_vod.configure(text=f"{core.vod_display_base(p)}  ·  {core.fmt_ts(d).split('.')[0]}  ·  "
                                    f"{info.get('width')}x{info.get('height')}{extra}")
        if start_utc is not None:
            self.v_start.set(start_utc.astimezone().strftime("%d/%m/%Y %H:%M:%S"))
        elif not self.v_start.get().strip() and info.get("start_guess"):
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
    def keep_awake(self, on: bool):
        """Mientras trabaja, pide a Windows que no se suspenda (la pantalla sí puede apagarse o bloquearse)."""
        if os.name != "nt":
            return
        try:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        except Exception:
            pass

    def set_running(self, running):
        self.keep_awake(running or self.flow_active)
        try:
            self.pill.set_state("TRABAJANDO" if running else "LISTO", YELLOW if running else ui.GREEN)
        except Exception:
            pass
        self.btn_render.configure(state="disabled" if running else "normal")
        self.btn_dl.configure(state="disabled" if running else "normal")
        self.btn_cancel.configure(state="normal" if running else "disabled")
        self.btn_subs.configure(state="disabled" if running else "normal")
        try:
            self.btn_drive.configure(state="disabled" if running else "normal")
        except AttributeError:
            pass

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
        self.after(500, lambda: self.keep_awake(self.busy or self.flow_active))
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
                elif auto and self.v_partial.get() and info.get("start"):
                    path = self._download_matches_only(url, out_dir, info, prog)
                if not path:
                    path = twitch.download(url, out_dir, prog, lambda: self.stop_flag, info)
                prog(None, "Leyendo el vídeo descargado…")
                self.cfg.setdefault("downloaded_vods", {})[os.path.abspath(path)] = time.time()
                self.after(0, self.persist)
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

    def _match_sections(self, info, prog):
        """Tramos del VOD (segundos) donde están tus partidas de competitivo, con margen de sobra
        para la sincronización (que busca ±2 min) y el delay del directo."""
        vs, dur = info["start"], float(info.get("duration") or 0)
        riot, key, reg = self.v_riot.get(), self.v_key.get(), self.v_region.get()
        st = self.settings()
        secs = []
        for page in range(6):
            prog(None, f"Buscando qué trozos del directo tienen partidas ({page * 10 + 1}–{page * 10 + 10})…")
            raws = core.fetch_recent_matches(riot, key, reg, size=10, start=page * 10)
            if not raws:
                break
            ms = []
            for r in raws:
                try:
                    ms.append(core.parse_match(r, riot))
                except Exception:
                    pass
            for m in ms:
                rel = (m.started_at - vs).total_seconds()
                known = self.delay_fn_for_vod() is not None
                off = rel + core.delay_offset(m, st, self.delay_fn_for_vod())
                length = (m.length_ms or 3000 * 1000) / 1000
                a = off - 6 * 60
                b = off + length + (5 if known else 9) * 60
                if b > 0 and a < dur:
                    secs.append((a, b))
            if ms and min(m.started_at for m in ms) < vs:
                break
        return twitch.merge_sections(secs, dur)

    def _download_matches_only(self, url, out_dir, info, prog):
        """Baja solo las partidas. Si algo falla, devuelve None y se baja el VOD entero."""
        try:
            secs = self._match_sections(info, prog)
        except Exception as e:
            prog(None, f"No pude ver las partidas ({str(e)[:80]}); bajo el VOD entero…")
            return None
        if not secs:
            return None
        have = twitch.find_parts(out_dir, info, secs)
        if have:
            prog(1, "Las partidas ya estaban descargadas")
            return have
        tot = sum(b - a for a, b in secs)
        self.after(0, lambda: self.lbl_step.configure(
            text=f"Descargando solo las partidas: {core.fmt_ts(tot).split('.')[0]} de "
                 f"{core.fmt_ts(info.get('duration') or 0).split('.')[0]} del directo…"))
        # mientras baja el siguiente trozo, el anterior ya se va analizando para la sincronización
        # (lo lento); al terminar la descarga, sincronizar es casi inmediato
        from concurrent.futures import ThreadPoolExecutor
        ex = ThreadPoolExecutor(max_workers=max(1, self.sync_workers() - 1)) if self.v_autosync.get() else None
        pre = []

        def on_part(man, start, dur):
            if ex:
                pre.append(ex.submit(sync.prewarm, man, start, dur, lambda: self.stop_flag))
        try:
            man = twitch.download_sections(url, out_dir, secs, prog, lambda: self.stop_flag, info,
                                           on_part=on_part)
            if ex:
                left = [f for f in pre if not f.done()]
                if left:
                    prog(None, f"Terminando de analizar {len(left)} trozo(s) para la sincronización…")
                for f in pre:
                    try:
                        f.result()
                    except Exception as e2:
                        if "Cancelado" in str(e2):
                            raise
            return man
        except Exception as e:
            if "cancelad" in str(e).lower():
                raise
            prog(None, f"La descarga por trozos falló ({str(e)[:80]}); bajo el VOD entero…")
            return None
        finally:
            if ex:
                ex.shutdown(wait=False, cancel_futures=True)

    def sub_offset(self) -> float:
        try:
            return max(-3.0, min(3.0, round(float(str(self.v_suboff.get()).replace(",", ".")), 2)))
        except (ValueError, tk.TclError):
            return 0.0

    def prepare_words(self, words):
        """Tras transcribir: tu diccionario de correcciones y tu ajuste de tiempo."""
        if not words:
            return words
        words = subtitles.apply_dict(words, self.cfg.get("sub_dict") or {})
        return subtitles.shift(words, self.sub_offset())

    def learn_word(self, original: str, new: str):
        """Lo que corriges en el editor se guarda en el diccionario para la próxima vez."""
        a, b = subtitles.norm(original), new.strip()
        if not a or not b or subtitles.norm(b) == a or len(a.split()) > 4:
            return
        self.cfg.setdefault("sub_dict", {})[a] = re.sub(r"[.,;:!?…]+$", "", b) or b
        self.persist()

    def edit_sub_dict(self):
        """Ver, añadir y quitar correcciones del diccionario de subtítulos."""
        d = self.cfg.setdefault("sub_dict", {})
        dlg = tk.Toplevel(self)
        dlg.title("Diccionario de subtítulos")
        dlg.configure(bg=PANEL)
        dlg.geometry("560x480")
        dlg.transient(self)
        ttk.Label(dlg, text="Diccionario de subtítulos", style="H.TLabel").pack(anchor="w", padx=12, pady=(10, 0))
        ttk.Label(dlg, text="Lo que corriges en el editor se apunta aquí solo y se aplica en los siguientes vídeos. "
                            "Puede ser una palabra o varias (hasta 4), que se juntan en una.",
                  style="Muted.TLabel", wraplength=530).pack(anchor="w", padx=12, pady=(2, 8))
        fr = ttk.Frame(dlg, style="Panel.TFrame")
        fr.pack(fill="both", expand=True, padx=12)
        tv = ttk.Treeview(fr, columns=("a", "b"), show="headings", selectmode="extended")
        tv.heading("a", text="Cuando entienda…")
        tv.heading("b", text="…poner")
        tv.column("a", width=250)
        tv.column("b", width=250)
        tv.pack(side="left", fill="both", expand=True)

        def fill():
            tv.delete(*tv.get_children())
            for k in sorted(d):
                tv.insert("", "end", iid=k, values=(k, d[k]))
        af = ttk.Frame(dlg, style="Panel.TFrame")
        af.pack(fill="x", padx=12, pady=8)
        va, vb = tk.StringVar(), tk.StringVar()
        ttk.Entry(af, textvariable=va, width=22).pack(side="left")
        ttk.Label(af, text="→").pack(side="left", padx=4)
        ttk.Entry(af, textvariable=vb, width=22).pack(side="left")

        def add():
            a_, b_ = subtitles.norm(va.get()), vb.get().strip()
            if a_ and b_:
                d[a_] = b_
                va.set("")
                vb.set("")
                fill()
                self.persist()

        def remove():
            for k in tv.selection():
                d.pop(k, None)
            fill()
            self.persist()
        ui.Button(af, text="Añadir", command=add).pack(side="left", padx=6)
        ui.Button(af, text="Quitar seleccionadas", command=remove).pack(side="right")
        tv.bind("<Delete>", lambda e: remove())
        ui.Button(dlg, text="Cerrar", style="Accent.TButton", command=dlg.destroy).pack(anchor="e", padx=12,
                                                                                         pady=(0, 12))
        fill()
        dlg._api = {"add": add, "remove": remove, "a": va, "b": vb, "tv": tv}
        return dlg

    # ------------------------------------------------------------ top de la semana
    def top_week_auto(self):
        """Cada lunes (o el primer día que se abra después): top de la semana pasada, si hay jugadas."""
        self.after(3 * 3600 * 1000, self.top_week_auto)
        if not self.v_topweek.get() or self.busy or self.flow_active:
            return
        mon, sun = semana.last_week()
        key = semana.week_key(mon)
        if key in (self.cfg.get("top_weeks_done") or []):
            return
        if len(semana.pick(mon, sun)) < 3:
            return
        self._make_top(mon, sun, auto=True)

    def top_week_now(self):
        if self.busy or self.flow_active:
            messagebox.showinfo(APP_NAME, "Espera a que termine lo que está haciendo.")
            return
        from datetime import datetime as _dt, timedelta as _td
        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        mon_l, sun_l = semana.last_week()
        mon_t = today - _td(days=today.weekday())
        n_this, n_last = len(semana.pick(mon_t, today)), len(semana.pick(mon_l, sun_l))
        if n_this < 3 and n_last < 3:
            messagebox.showinfo(APP_NAME, "Todavía no hay suficientes jugadas guardadas (hacen falta 3).\n\n"
                                          "Se apuntan solas cada vez que se generan los clips sueltos de un directo.")
            return
        if n_this >= 3 and (n_last < 3 or messagebox.askyesno(
                APP_NAME, f"¿Top de ESTA semana (hasta hoy, {n_this} jugadas)?\n\n"
                          f"«No» = el de la semana pasada ({n_last} jugadas).")):
            self._make_top(mon_t, today, auto=False)
        else:
            self._make_top(mon_l, sun_l, auto=False)

    def _make_top(self, mon, sun, auto=False):
        out_dir = self.v_outdir.get()
        enc = ENCODERS[self.v_enc.get()]
        chan = (self.v_channel.get() or "").strip().lstrip("@")
        self.stop_flag = False
        self.set_running(True)
        self.set_step(4, f"Top de la semana ({mon:%d/%m} – {sun:%d/%m})…")
        cb = self.progress_cb()

        def work():
            try:
                return semana.make_week(out_dir, mon, sun, self.v_vertical.get(), enc, cb,
                                        lambda: self.stop_flag, chan)
            except Exception as e:
                if not auto or "Cancelado" in str(e):
                    raise
                return str(e)                 # automático: sin ventanas de error, solo un aviso abajo
            finally:
                self.after(0, lambda: self.set_running(False))

        def done(made):
            if isinstance(made, str):
                self.cfg.setdefault("top_weeks_done", []).append(semana.week_key(mon))
                self.persist()
                self.set_step(None, "Top de la semana: no se pudo")
                self.status("⚠ Top de la semana: " + made[:150])
                return
            self.cfg.setdefault("top_weeks_done", []).append(semana.week_key(mon))
            self.persist()
            self.finish_steps("Top de la semana listo")
            self.status(f"Top de la semana listo: {os.path.dirname(made[0])}")
            if not auto and not self.unattended() and messagebox.askyesno(
                    APP_NAME, f"¡Top de la semana listo!\n\n{os.path.dirname(made[0])}\n\n¿Abrir la carpeta?"):
                if os.name == "nt":
                    subprocess.Popen(["explorer", os.path.normpath(os.path.dirname(made[0]))])
        self.run_bg(work, done, "No se pudo hacer el top de la semana")

    VOD_KEEP_DAYS = 5

    def purge_old_vods(self):
        """Borra los VODs que bajó la app y de los que ya se sacaron los vídeos hace más de 5 días.
        Nunca toca un vídeo elegido a mano ni los vídeos generados."""
        if not self.v_autodel.get() or self.busy or self.flow_active:
            self.after(3600 * 1000, self.purge_old_vods)
            return
        gen = self.cfg.get("generated_vods") or {}
        dl = self.cfg.get("downloaded_vods") or {}
        cur = os.path.abspath(self.v_vod.get()) if self.v_vod.get() else ""
        now, freed, gone = time.time(), 0, []
        kept = []
        for path, when in list(gen.items()):
            if path not in dl or now - when < self.VOD_KEEP_DAYS * 86400 or path == cur:
                if path not in dl and now - when > 60 * 86400:
                    gen.pop(path, None)
                continue
            # antes de borrar: ¿siguen ahí los vídeos que se sacaron de este VOD y se abren bien?
            if path not in (self.cfg.get("generated_outputs") or {}):
                continue          # de antes de 1.16.1: sin lista de vídeos no se borra nada
            bad = self.outputs_problem(path)
            if bad:
                kept.append((os.path.basename(path), bad))
                continue
            try:
                if core.is_parts(path):
                    if os.path.isfile(path):
                        m = core.load_parts(path)
                        for p in m["parts"]:
                            if os.path.isfile(p["file"]):
                                freed += os.path.getsize(p["file"])
                                os.remove(p["file"])
                        d = os.path.dirname(m["parts"][0]["file"]) if m["parts"] else ""
                        os.remove(path)
                        if d and os.path.isdir(d) and not os.listdir(d):
                            os.rmdir(d)
                elif os.path.isfile(path):
                    freed += os.path.getsize(path)
                    os.remove(path)
                gone.append(os.path.basename(path))
            except OSError:
                continue          # abierto en otro programa: se intenta otro día
            gen.pop(path, None)
            dl.pop(path, None)
        if gone:
            self.persist()
            self.status(f"Liberados {twitch.fmt_size(freed)}: borrados {len(gone)} VOD(s) de hace más de "
                        f"{self.VOD_KEEP_DAYS} días (los vídeos generados siguen ahí).")
        warned = self.cfg.setdefault("purge_warned", [])
        new_kept = [(n, why) for n, why in kept if n not in warned]
        if new_kept:
            warned.extend(n for n, _ in new_kept)
            self.persist()
            txt = "\n".join(f"• {n}: {why}" for n, why in new_kept[:5])
            self.status("⚠ No he borrado " + ", ".join(n for n, _ in new_kept[:3]) +
                        ": sus vídeos no están bien (mira el aviso).")
            if not self.unattended():
                messagebox.showwarning(APP_NAME, "Tocaba borrar estos VODs (pasaron 5 días), pero NO los he "
                                                 "borrado porque sus vídeos generados no están o no se abren:\n\n"
                                                 f"{txt}\n\nAsí puedes volver a generarlos. Si ya no los "
                                                 "necesitas, bórralos tú a mano.")
        self.after(6 * 3600 * 1000, self.purge_old_vods)

    def outputs_problem(self, vod_path: str) -> str:
        """'' si los vídeos sacados de ese VOD están y se abren; si no, el motivo."""
        outs = (self.cfg.get("generated_outputs") or {}).get(vod_path)
        if not outs:
            return "no sé qué vídeos se sacaron de él"
        for p in outs:
            if os.path.isdir(p):
                if not any(f.lower().endswith(".mp4") for _, _, fs in os.walk(p) for f in fs):
                    return f"la carpeta «{os.path.basename(p)}» está vacía"
                continue
            if not os.path.isfile(p):
                return f"falta «{os.path.basename(p)}»"
            try:
                d = core.probe_vod(p).get("duration") or 0
            except Exception:
                d = 0
            if d < 1:
                return f"«{os.path.basename(p)}» no se abre o está vacío"
        return ""

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
            if not self.checked and getattr(self, "in_vod_count", 0):
                self.set_step(None, "Sin rondas que cortar")
                self.set_progress(0, "Las partidas de este VOD no tienen ninguna ronda que cumpla tus ajustes.")
                if not self.unattended():
                    messagebox.showinfo(APP_NAME, "En este VOD hay partidas, pero ninguna tiene rondas que "
                                                  "cumplan tus ajustes (kills mínimas / clutch).")
                self.flow_done("Sin rondas que cortar en este VOD.")
                return
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
        self.render(self.default_out())

    # ------------------------------------------------------------ sincronización automática
    def sync_workers(self) -> int:
        """Partidas que se leen a la vez (cada una es un ffmpeg decodificando el VOD)."""
        try:
            n = int(self.cfg.get("sync_workers") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return min(n, 6)
        # cada partida es un ffmpeg decodificando 1080p60: unos 3-4 hilos del procesador por partida;
        # con NVIDIA la decodificación va en la gráfica y cabe una más
        cpus = os.cpu_count() or 4
        n = 4 if cpus >= 16 else 3 if cpus >= 12 else 2
        if subtitles.has_nvidia():
            n += 1
        return max(2, min(n, 5))

    def _bg_subs_hook(self, vod, vs, dur, st, dfn):
        """Mientras se sincronizan las demás partidas, ir transcribiendo las jugadas de las que ya están
        (al llegar a «Generar vídeos» los subtítulos ya están hechos)."""
        if not (self.v_vertical.get() and self.v_subs.get()):
            return None
        from concurrent.futures import ThreadPoolExecutor
        if getattr(self, "_sub_ex", None) is None:
            self._sub_ex = ThreadPoolExecutor(max_workers=1)
            self.sub_jobs = []
        # sin gráfica NVIDIA, el transcriptor en segundo plano usa pocos hilos para no frenar la sincronización
        thr = None if subtitles.find_gpu_cli() else max(2, (os.cpu_count() or 4) // 3)

        def on_done(m, r):
            if not r.ok:
                return
            cl = core.build_clips([m], vs, dur, st, {m.match_id: {"offset_s": r.offset_s}}, dfn)
            if not cl:
                return
            have = subtitles.cache_get(vod)
            rng = [(c.vod_start - 2, c.vod_end + 2) for c in cl
                   if subtitles._covering(have, c.vod_start, c.vod_end) is None]
            if rng:
                self.sub_jobs.append(self._sub_ex.submit(subtitles.transcribe_ranges, vod, rng, None,
                                                         lambda: self.stop_flag, thr))
        return on_done

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
        # solo las partidas de ESTE vídeo (las de otros días no son «fallos»)
        ms = [m for m in ms if core.match_in_vod(m, vs, dur, st, self.calib, dfn)]
        if not ms:
            messagebox.showinfo(APP_NAME, "Ninguna de las partidas marcadas cae dentro de este VOD.")
            return
        # si ya sincronizaste a mano alguna partida de este VOD, su desfase sirve de punto de partida para
        # las demás (p. ej. un día con mucho más delay del normal: con una a mano, el resto se encuentran solas)
        anchors = []
        for mid, c in self.calib.items():
            m_ = self.matches.get(mid)
            if m_ is None or c.get("first_kill_vod_s") is None:
                continue
            if not core.match_in_vod(m_, vs, dur, st, self.calib, dfn):
                continue
            man = core.match_offset_s(m_, vs, {mid: {"first_kill_vod_s": c["first_kill_vod_s"]}})
            anchors.append(man - (core.match_offset_s(m_, vs, {}) + core.delay_offset(m_, st, dfn)))
        anchor = sorted(anchors)[len(anchors) // 2] if anchors else 0.0
        self.stop_flag = False
        self.set_running(True)
        self.set_step(2, f"Sincronizando {len(ms)} partidas con el VOD…")
        cb = self.progress_cb()

        def work():
            try:
                items = []
                for m in sorted(ms, key=lambda x: x.started_at):
                    # punto de partida: tu sincronización a mano si la hay; si no, la hora + el desfase
                    # (las sincronizaciones automáticas anteriores NO se usan como punto de partida)
                    c = self.calib.get(m.match_id) or {}
                    if c.get("first_kill_vod_s") is not None:
                        exp = core.match_offset_s(m, vs, {m.match_id: {"first_kill_vod_s": c["first_kill_vod_s"]}})
                    else:
                        exp = core.match_offset_s(m, vs, {}) + core.delay_offset(m, st, dfn) + anchor
                    items.append((m, exp))
                on_done = self._bg_subs_hook(vod, vs, dur, st, dfn)
                res = sync.sync_many(vod, items, dur, self.sync_workers(), cb, lambda: self.stop_flag,
                                     on_done=on_done)
                # si NO sale ninguna (p. ej. un día con más delay o el directo empezó a otra hora: el
                # 17/11 iba +150 s), segunda pasada buscando mucho más lejos (±5,5 min; el VOD por
                # partidas se baja con 6 min de margen). Con varias partidas, el consenso confirma.
                if len(items) >= 2 and not any(r.ok for r in res.values()) and not self.stop_flag:
                    cb(None, "No ha salido ninguna: buscando más lejos (±5 min)…")
                    old_s = sync.SEARCH_S
                    try:
                        sync.SEARCH_S = 330.0
                        res2 = sync.sync_many(vod, items, dur, self.sync_workers(), cb, lambda: self.stop_flag,
                                              on_done=on_done)
                        if len(res2) > 1:
                            times2 = {mid: self.matches[mid].started_at.timestamp()
                                      for mid in res2 if mid in self.matches}
                            res2 = sync.consensus(res2, os.path.basename(vod), times2)
                    finally:
                        sync.SEARCH_S = old_s
                    oks2 = [r for r in res2.values() if r.ok]
                    # se acepta solo si al menos dos partidas coinciden en el desfase (±15 s)
                    if len(oks2) >= 2:
                        sh = sorted(r.shift_s for r in oks2)
                        med = sh[len(sh) // 2]
                        if sum(1 for x in sh if abs(x - med) <= 15) >= 2:
                            res = res2
                # referencias extra: partidas de este mismo VOD que ya se sincronizaron solas antes
                # (así, al repetir solo las que fallaron, hay desfase común con el que compararlas)
                refs = {}
                if any(not r.ok for r in res.values()):
                    for mid, c in self.calib.items():
                        m = self.matches.get(mid)
                        if (mid in res or m is None or not c.get("auto") or c.get("approx")
                                or c.get("offset_s") is None or float(c.get("conf") or 0) < 0.6):
                            continue
                        if not core.match_in_vod(m, vs, dur, st, {}, dfn):
                            continue
                        exp = core.match_offset_s(m, vs, {}) + core.delay_offset(m, st, dfn)
                        off = float(c["offset_s"])
                        if abs(off - exp) > sync.SEARCH_S:
                            continue
                        refs[mid] = sync.SyncResult(off, off - exp, float(c["conf"]), True)
                if len(res) + len(refs) > 1 and any(not r.ok for r in res.values()):
                    cb(1, "Afinando con el desfase común de todas las partidas…")
                    times = {mid: self.matches[mid].started_at.timestamp()
                             for mid in list(refs) + list(res) if mid in self.matches}
                    res = sync.consensus({**refs, **res}, os.path.basename(vod), times)
                    res = {mid: r for mid, r in res.items() if mid not in refs}
                # comprobación independiente de cada partida colocada (rápida: usa lo ya leído)
                oks = [(mid, r) for mid, r in res.items() if r.ok and mid in self.matches]
                if oks:
                    from concurrent.futures import ThreadPoolExecutor
                    cb(1, f"Comprobando las {len(oks)} partidas colocadas…")

                    def chk(item):
                        mid, r = item
                        try:
                            r.check = sync.verify_match(vod, self.matches[mid], r.offset_s, dur,
                                                        lambda: self.stop_flag)
                            # si la comprobación ve con mucha claridad (pico fuerte y las dos mitades de
                            # acuerdo) que está en otro sitio —típico: se enganchó al «eco» del cartel,
                            # ~7 s tarde—, se mueve ahí y se vuelve a comprobar; solo se queda si sale bien
                            ck = r.check or {}
                            if ck.get("state") == "dudosa" and abs(float(ck.get("shift", 0))) <= 15:
                                sh = float(ck["shift"])
                                ck2 = sync.verify_match(vod, self.matches[mid], r.offset_s + sh, dur,
                                                        lambda: self.stop_flag)
                                if ck2.get("state") == "ok":
                                    r.offset_s += sh
                                    r.shift_s += sh
                                    r.check = ck2
                                    m_ = self.matches[mid]
                                    sync._log_line(f"{m_.map_name} {m_.started_at.astimezone():%d/%m %H:%M}: "
                                                   f"corregida por la comprobación {sh:+.2f} s")
                        except Exception as e:
                            if "Cancelado" in str(e):
                                raise
                            r.check = {"state": "?"}
                    with ThreadPoolExecutor(max_workers=self.sync_workers()) as ex:
                        list(ex.map(chk, oks))
                return res
            finally:
                self.after(0, lambda: self.set_running(False))

        def done(res):
            good = bad = 0
            shifts = []
            failed = []
            approx = []
            learned = []
            doubtful = []
            for mid, r in res.items():
                # aprender el desfase base solo de las partidas que se han encontrado solas con claridad
                if r.ok and not r.approx and r.confidence >= 0.7 and mid in self.matches:
                    m_ = self.matches[mid]
                    d_ = dfn(m_.started_at) if dfn is not None else None
                    learned.append(r.offset_s - core.match_offset_s(m_, vs, {}) - (d_ or 0.0))
                if r.ok:
                    self.calib[mid] = {"offset_s": r.offset_s, "auto": True,
                                       "conf": round(r.confidence, 2), "approx": r.approx}
                    ck = getattr(r, "check", None) or {}
                    if ck.get("state"):
                        self.calib[mid]["check"] = ck["state"]
                    if ck.get("state") == "dudosa":
                        self.calib[mid]["check_shift"] = round(float(ck.get("shift", 0)), 2)
                        m_ = self.matches.get(mid)
                        if m_:
                            doubtful.append(f"{m_.map_name} {m_.started_at.astimezone():%d/%m %H:%M}")
                    if r.approx:
                        m_ = self.matches.get(mid)
                        if m_:
                            approx.append(f"{m_.map_name} {m_.started_at.astimezone():%d/%m %H:%M}")
                    good += 1
                    shifts.append(r.shift_s)
                elif r.reason == "la partida no está en el VOD":
                    continue
                else:
                    bad += 1
                    if not core.is_synced(self.calib, mid):
                        self.calib[mid] = {"failed": True}
                    m_ = self.matches.get(mid)
                    if m_:
                        failed.append(f"{m_.map_name} {m_.started_at.astimezone():%d/%m %H:%M}")
            # cuándo se VEN las kills en tu directo respecto a la hora de Riot (mediana del VOD, estable):
            # los cortes se calculan desde ahí, así «antes» y «después» son exactos sobre la imagen
            biases = sorted(r.kill_bias for r in res.values() if r.ok and r.kill_bias is not None)
            if len(biases) >= 2:
                kb = max(-0.75, min(0.5, biases[len(biases) // 2]))
                kb = round(kb * 4) / 4
                for mid, r in res.items():
                    if r.ok and mid in self.calib:
                        self.calib[mid]["extra_s"] = kb
            if len(learned) >= 2:
                learned.sort()
                base = learned[len(learned) // 2]
                self.v_delay.set(f"{base:.1f}")      # el campo se ajusta solo para la próxima vez
            self.persist()
            self.auto_check()
            self.set_progress(1, f"Sincronizadas solas: {good}" + (f" · sin patrón claro: {bad} "
                              "(se quedan con la hora + delay; puedes sincronizarlas a mano)" if bad else ""))
            if doubtful:
                self.sync_warning = (f"La comprobación automática ve raras {len(doubtful)} partida(s) "
                                     f"({', '.join(doubtful[:6])}{'…' if len(doubtful) > 6 else ''}): el killfeed "
                                     "no cambia donde deberían estar tus kills. Míralas con «Revisar sincro» o "
                                     "sincronízalas a mano.")
            elif approx and not failed:
                self.sync_warning = (f"{len(approx)} partida(s) se han ajustado con el desfase común del directo "
                                     f"({', '.join(approx[:6])}{'…' if len(approx) > 6 else ''}), sin pico propio "
                                     "claro. Revisa alguna ronda suya con «Ver ronda».")
            elif failed:
                self.sync_warning = (f"{len(failed)} partida(s) no se han podido sincronizar solas "
                                     f"({', '.join(failed[:6])}{'…' if len(failed) > 6 else ''}). "
                                     "Sus cortes pueden caer desplazados: revísalas con «Ver ronda» o "
                                     "sincronízalas a mano."
                                     + (" Si no ha salido NINGUNA de este directo (p. ej. ese día el delay era "
                                        "mucho mayor), sincroniza UNA a mano y vuelve a darle a Sincronizar: "
                                        "las demás se buscan desde ahí." if good == 0 else ""))
            else:
                self.sync_warning = ""
            if then:
                then()
            else:
                self.set_step(None, "Sincronización terminada")
                if self.clips:
                    self.compute()
                if failed or approx or doubtful:
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

    # ------------------------------------------------------------ editor de subtítulos
    def edit_subtitles(self, words, callback, video=None, highlight=True):
        """
        Ventana para corregir los subtítulos: cambiar el texto de una palabra, borrarla, ponerla en
        amarillo / morado / sin color, o quitar todos los subtítulos.
        Con video (el vídeo horizontal ya generado) se ve y se oye el vídeo dentro de la ventana con
        el subtítulo pintado encima: cada cambio se ve al momento.
        callback(lista de palabras) al continuar, o callback(None) para no poner subtítulos.
        """
        ws = [dict(w) for w in words]
        dlg = tk.Toplevel(self)
        dlg.title("Revisar subtítulos")
        dlg.configure(bg=PANEL)
        dlg.geometry("1240x720" if video else "760x640")
        dlg.transient(self)
        ttk.Label(dlg, text="Revisa los subtítulos " + ("viendo el vídeo" if video else "antes de generar la vertical"),
                  style="H.TLabel").pack(anchor="w", padx=12, pady=(10, 0))
        ttk.Label(dlg, text=("Clic en una palabra: el vídeo salta ahí. Doble clic: corregirla. Espacio en el "
                             "vídeo: reproducir/pausa. " if video else "Doble clic en una palabra para corregirla. ") +
                            "Puedes seleccionar varias (Ctrl/Mayús) para borrarlas o cambiarles el color. "
                            "«auto» = el programa decide si va en color.",
                  style="Muted.TLabel", wraplength=1180 if video else 720).pack(anchor="w", padx=12, pady=(2, 8))
        body = ttk.Frame(dlg, style="Panel.TFrame")
        body.pack(fill="both", expand=True, padx=12)
        pl = None
        if video:
            left = ttk.Frame(body, style="Panel.TFrame")
            left.pack(side="left", fill="y", padx=(0, 12))
            pl = player.Player(left, lambda: video)
            pl.pack(anchor="n")
            try:
                vdur = core.probe_vod(video)["duration"]
            except Exception:
                vdur = (ws[-1]["end"] + 5) if ws else 60
            pl.range = (0.0, vdur)
            pl.scale.configure(from_=0, to=vdur)
            pl.cv.itemconfigure(pl.msg, text="")
            ttk.Label(left, text="Así quedará el texto en la vertical (aquí en pequeño).",
                      style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        fr = ttk.Frame(body, style="Panel.TFrame")
        fr.pack(side="left", fill="both", expand=True)
        tv = ttk.Treeview(fr, columns=("t", "w", "c"), show="headings", selectmode="extended")
        for c_, t_, w_ in (("t", "Momento", 110), ("w", "Palabra", 380), ("c", "Color", 160)):
            tv.heading(c_, text=t_)
            tv.column(c_, width=w_, anchor="w" if c_ == "w" else "center", stretch=c_ == "w")
        tv.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        sb.pack(side="right", fill="y")
        tv.configure(yscrollcommand=sb.set)
        names = {"y": "amarillo", "p": "morado", "none": "sin color"}

        def color_txt(w):
            hl = w.get("hl")
            if hl in names:
                return names[hl]
            return "auto ✓" if subtitles.auto_highlight(w["text"]) else "auto"

        tv.tag_configure("alt", background=ROW_ALT)
        tv.tag_configure("y", foreground=YELLOW)
        tv.tag_configure("p", foreground=PURPLE)
        tv.tag_configure("none", foreground=MUTED)

        tv.tag_configure("now", background="#3a2f52")
        ov = {"chunks": [], "items": [], "cur": None, "row": None}
        cols = {subtitles.ASS_YELLOW: YELLOW, subtitles.ASS_PURPLE: PURPLE}

        def rebuild_overlay():
            ov["chunks"] = subtitles.chunk_words_rich(ws, highlight=highlight) if ws else []
            ov["cur"] = "x"
            if pl:
                on_time(pl._now())

        def on_time(t):
            if not pl:
                return
            ch = next((c for c in ov["chunks"] if c[0] <= t < c[1]), None)
            key = id(ch) if ch else None
            if key != ov["cur"]:
                ov["cur"] = key
                for it in ov["items"]:
                    pl.cv.delete(it)
                ov["items"] = []
                if ch:
                    fnt = ("Arial Black", 22)
                    txts = [(w, cols.get(c, "#ffffff")) for w, c in ch[2]]
                    widths = []
                    for w, _c in txts:
                        it = pl.cv.create_text(0, 0, text=w, font=fnt)
                        b = pl.cv.bbox(it)
                        pl.cv.delete(it)
                        widths.append((b[2] - b[0]) if b else 10 * len(w))
                    gap = 10
                    x = pl.vw / 2 - (sum(widths) + gap * (len(widths) - 1)) / 2
                    y = pl.vh * 0.78
                    for (w, c), wd in zip(txts, widths):
                        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)):
                            ov["items"].append(pl.cv.create_text(x + dx, y + dy, text=w, font=fnt, fill="#000000",
                                                                 anchor="w", tags=("overlay",)))
                        ov["items"].append(pl.cv.create_text(x, y, text=w, font=fnt, fill=c, anchor="w",
                                                             tags=("overlay",)))
                        x += wd + gap
            # marcar en la lista la palabra que suena
            idx = None
            for i, w in enumerate(ws):
                if w["start"] <= t:
                    idx = i
                else:
                    break
            if idx != ov["row"]:
                if ov["row"] is not None and tv.exists(str(ov["row"])):
                    tv.item(str(ov["row"]), tags=tuple(x for x in tv.item(str(ov["row"]), "tags") if x != "now"))
                ov["row"] = idx
                if idx is not None and tv.exists(str(idx)) and pl.playing:
                    tv.item(str(idx), tags=tuple(tv.item(str(idx), "tags")) + ("now",))
                    tv.see(str(idx))

        if pl:
            pl.on_time = on_time

        def fill(keep=None):
            ov["row"] = None
            tv.delete(*tv.get_children())
            for i, w in enumerate(ws):
                hl = w.get("hl")
                tg = [hl] if hl in ("y", "p", "none") else []
                if i % 2:
                    tg.append("alt")
                tv.insert("", "end", iid=str(i), tags=tuple(tg),
                          values=(core.fmt_ts(w["start"]), w["text"], color_txt(w)))
            if keep:
                ok_ = [k for k in keep if tv.exists(k)]
                if ok_:
                    tv.selection_set(ok_)
                    tv.see(ok_[0])
            rebuild_overlay()

        ed = ttk.Frame(dlg, style="Panel.TFrame")
        ed.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(ed, text="Texto:").pack(side="left")
        v_txt = tk.StringVar()
        ent = ttk.Entry(ed, textvariable=v_txt, width=40)
        ent.pack(side="left", padx=6)

        def sel():
            return sorted((int(i) for i in tv.selection()))

        def load(_e=None):
            s_ = sel()
            if s_:
                v_txt.set(" ".join(ws[i]["text"] for i in s_) if len(s_) > 1 else ws[s_[0]]["text"])
                ent.focus_set()
                ent.select_range(0, "end")

        def apply_text(_e=None):
            s_ = sel()
            if not s_:
                return
            t = v_txt.get().strip()
            if not t:
                delete()
                return
            orig = " ".join(ws[i].get("raw", ws[i]["text"]) for i in s_)
            self.learn_word(orig, t)
            if len(s_) > 1:
                # varias seleccionadas: se juntan en una (p. ej. «tres kills» mal partido o mal oído)
                first = ws[s_[0]]
                first["raw"] = orig
                first["text"] = t
                first["end"] = max(ws[i]["end"] for i in s_)
                for i in reversed(s_[1:]):
                    del ws[i]
            else:
                ws[s_[0]]["text"] = t
            fill([str(s_[0])])

        def delete():
            s_ = sel()
            if not s_:
                return
            for i in reversed(s_):
                del ws[i]
            fill([str(min(s_[0], len(ws) - 1))] if ws else None)

        def set_color(hl):
            s_ = sel()
            for i in s_:
                if hl == "auto":
                    ws[i].pop("hl", None)
                else:
                    ws[i]["hl"] = hl
            fill([str(i) for i in s_])

        ui.Button(ed, text="Cambiar", command=apply_text).pack(side="left")
        ui.Button(ed, text="Borrar", command=delete).pack(side="left", padx=6)
        cl = ttk.Frame(dlg, style="Panel.TFrame")
        cl.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(cl, text="Color:").pack(side="left")
        ui.Button(cl, text="Amarillo", command=lambda: set_color("y")).pack(side="left", padx=4)
        ui.Button(cl, text="Morado", command=lambda: set_color("p")).pack(side="left", padx=4)
        ui.Button(cl, text="Sin color", command=lambda: set_color("none")).pack(side="left", padx=4)
        ui.Button(cl, text="Automático", command=lambda: set_color("auto")).pack(side="left", padx=4)
        tf_ = ttk.Frame(dlg, style="Panel.TFrame")
        tf_.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(tf_, text="Tiempo de todos:").pack(side="left")
        moved = {"s": 0.0}
        lbl_mv = ttk.Label(tf_, text="0,0 s", width=8, anchor="center")
        v_keep = tk.BooleanVar(value=False)

        def move(d_):
            moved["s"] = round(moved["s"] + d_, 2)
            for w in ws:
                w["start"] = max(0.0, w["start"] + d_)
                w["end"] = max(0.05, w["end"] + d_)
            lbl_mv.configure(text=f"{moved['s']:+.1f} s".replace(".", ","))
            fill([str(i) for i in sel()])
            if pl and not pl.playing:
                on_time(pl._now())

        ui.Button(tf_, text="◀ −0,1 s (antes)", command=lambda: move(-0.1)).pack(side="left", padx=4)
        lbl_mv.pack(side="left")
        ui.Button(tf_, text="+0,1 s (después) ▶", command=lambda: move(0.1)).pack(side="left", padx=4)
        ui.Toggle(tf_, text="Usar este ajuste en los próximos vídeos", variable=v_keep).pack(side="left",
                                                                                                  padx=10)
        sl = ttk.Frame(dlg, style="Panel.TFrame")
        sl.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(sl, text="Seleccionar:").pack(side="left")

        def select_phrase():
            """Toda la frase (lo que sale junto en pantalla) de las palabras seleccionadas."""
            s_ = sel()
            if not s_:
                return
            want = set()
            for a_, b_, _it in subtitles.chunk_words_rich(ws, max_words=12, max_chars=200, highlight=highlight):
                idx = [i for i, w in enumerate(ws) if a_ - 0.001 <= w["start"] < b_ - 0.001]
                if any(i in idx for i in s_):
                    want.update(idx)
            want.update(s_)
            tv.selection_set([str(i) for i in sorted(want)])

        def select_all(_e=None):
            tv.selection_set(tv.get_children())
            return "break"

        def select_none():
            tv.selection_set([])

        ui.Button(sl, text="Toda la frase", command=select_phrase).pack(side="left", padx=4)
        ui.Button(sl, text="Todo (Ctrl+A)", command=select_all).pack(side="left", padx=4)
        ui.Button(sl, text="Ninguna", command=select_none).pack(side="left", padx=4)
        lbl_n = ttk.Label(sl, text="", style="Muted.TLabel")
        lbl_n.pack(side="left", padx=10)
        ttk.Label(dlg, text="Varias a la vez: arrastra con el ratón, o Ctrl+clic / Mayús+clic.  Clic derecho: "
                            "menú.  Teclas: Supr borrar · A amarillo · M morado · S sin color · Z automático.",
                  style="Muted.TLabel").pack(anchor="w", padx=12, pady=(4, 0))

        def count(_e=None):
            n_ = len(tv.selection())
            lbl_n.configure(text=f"{n_} seleccionadas" if n_ > 1 else "")

        # arrastrar para seleccionar varias
        drag = {"a": None}

        def press(e):
            r_ = tv.identify_row(e.y)
            drag["a"] = r_ if r_ and not (e.state & 0x0005) else None   # sin Ctrl/Mayús

        def motion(e):
            r_ = tv.identify_row(e.y)
            if drag["a"] and r_:
                a_, b_ = sorted((int(drag["a"]), int(r_)))
                tv.selection_set([str(i) for i in range(a_, b_ + 1)])
                tv.see(r_)

        tv.bind("<ButtonPress-1>", press, add="+")
        tv.bind("<B1-Motion>", motion)
        tv.bind("<Control-a>", select_all)
        tv.bind("<Control-A>", select_all)
        for key_, hl_ in (("a", "y"), ("m", "p"), ("s", "none"), ("z", "auto")):
            tv.bind(f"<KeyPress-{key_}>", lambda e, h=hl_: set_color(h))
            tv.bind(f"<KeyPress-{key_.upper()}>", lambda e, h=hl_: set_color(h))
        menu = tk.Menu(dlg, tearoff=0)
        menu.add_command(label="Amarillo", command=lambda: set_color("y"))
        menu.add_command(label="Morado", command=lambda: set_color("p"))
        menu.add_command(label="Sin color", command=lambda: set_color("none"))
        menu.add_command(label="Automático", command=lambda: set_color("auto"))
        menu.add_separator()
        menu.add_command(label="Seleccionar toda la frase", command=select_phrase)
        menu.add_command(label="Corregir / juntar en una…", command=lambda: load())
        menu.add_separator()
        menu.add_command(label="Borrar", command=delete)

        def popup(e):
            r_ = tv.identify_row(e.y)
            if r_ and r_ not in tv.selection():
                tv.selection_set([r_])
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()

        tv.bind("<Button-3>", popup)
        def jump(_e=None):
            s_ = sel()
            count()
            if pl and len(s_) == 1 and not pl.playing:
                pl.seek(max(0.0, ws[s_[0]]["start"] - 0.05))

        def listen():
            s_ = sel()
            if pl and s_:
                pl.pos = max(0.0, ws[s_[0]]["start"] - 1.0)
                pl.play()

        if pl:
            ui.Button(ed, text="▶ Escuchar desde aquí", command=listen).pack(side="left", padx=(12, 0))
            tv.bind("<<TreeviewSelect>>", jump)
        else:
            tv.bind("<<TreeviewSelect>>", count)
        tv.bind("<Double-1>", load)
        tv.bind("<Delete>", lambda e: delete())
        ent.bind("<Return>", apply_text)
        done = {"v": False}

        def finish(res):
            if done["v"]:
                return
            done["v"] = True
            if v_keep.get() and moved["s"]:
                self.v_suboff.set(f"{max(-3.0, min(3.0, self.sub_offset() + moved['s'])):.1f}")
                self.persist()
            if pl:
                pl.on_time = None
                pl.stop()
            dlg.destroy()
            callback(res)

        bf = ttk.Frame(dlg, style="Panel.TFrame")
        bf.pack(fill="x", padx=12, pady=12)
        ui.Button(bf, text="Guardar y rehacer la vertical" if video else "Continuar con estos subtítulos",
                   style="Accent.TButton", command=lambda: finish(ws or None)).pack(side="right")
        ui.Button(bf, text="Quitar todos los subtítulos",
                   command=lambda: finish(None)).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(ws or None))
        fill()
        # para la prueba automática
        dlg._api = {"tv": tv, "words": ws, "set_color": set_color, "delete": delete, "text": v_txt,
                    "apply": apply_text, "finish": finish, "player": pl, "overlay": ov, "on_time": on_time,
                    "select_phrase": select_phrase, "select_all": select_all, "move": move, "keep": v_keep}
        if pl:
            pl.seek(ws[0]["start"] if ws else 0.0)
        return dlg

    # ------------------------------------------------------------ revisión rápida de la sincro
    REVIEW_BEFORE_S = 1.0
    REVIEW_AFTER_S = 1.5

    def review_sync(self):
        """Antes/después del killfeed en kills tuyas de cada partida marcada: de un vistazo se ve
        si la partida está bien sincronizada (la línea nueva aparece en «después»)."""
        if self.busy:
            return
        if not self.vod_info:
            messagebox.showinfo(APP_NAME, "Primero elige o descarga el VOD.")
            return
        try:
            vs = self.vod_start_utc()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        ms = sorted((self.matches[i] for i in self.checked if i in self.matches), key=lambda m: m.started_at)
        if not ms:
            messagebox.showinfo(APP_NAME, "Marca las partidas que quieres revisar (☐ → ☑).")
            return
        st, dfn = self.settings(), self.delay_fn_for_vod()
        vod, dur = self.v_vod.get(), self.vod_info["duration"]
        crop = sync.REGIONS["killfeed"][1]
        tmp = tempfile.mkdtemp(prefix="vortexsclips_rev_")
        jobs, rows = [], []
        for m in ms:
            mine = sorted((k for k in m.kills if k.killer == m.my_puuid), key=lambda k: k.t_match)
            pick = mine[:1] + ([mine[len(mine) // 2]] if len(mine) > 2 else [])
            base = core.match_base_s(m, vs, st, self.calib, dfn)
            ks = []
            for j, k in enumerate(pick):
                t = base + k.t_match / 1000
                if t < 0 or (dur and t > dur):
                    continue
                a = os.path.join(tmp, f"{m.match_id[:8]}_{j}_a.png")
                b = os.path.join(tmp, f"{m.match_id[:8]}_{j}_b.png")
                jobs += [(a, t - self.REVIEW_BEFORE_S), (b, t + self.REVIEW_AFTER_S)]
                ks.append((k, t, a, b))
            rows.append((m, ks))
        if not jobs:
            messagebox.showinfo(APP_NAME, "En las partidas marcadas no hay kills tuyas dentro del VOD.")
            return
        self.set_step(None, "Preparando la revisión…")
        cb = self.progress_cb()

        def work():
            from concurrent.futures import ThreadPoolExecutor
            lock, cnt = threading.Lock(), [0]

            def one(job):
                png, at = job
                try:
                    core.grab_crop(vod, at, png, crop, 260)
                except Exception:
                    pass
                with lock:
                    cnt[0] += 1
                    d = cnt[0]
                cb(d / len(jobs), f"Revisión: fotograma {d} de {len(jobs)}")
            with ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(one, jobs))
            return rows

        def done(rows_):
            self.set_step(None, "Revisión lista")
            self._show_review(rows_, tmp)
        self.run_bg(work, done, "No se pudo preparar la revisión")

    def _show_review(self, rows, tmp):
        import shutil
        win = tk.Toplevel(self)
        win.title("Revisar sincronización")
        win.configure(bg=PANEL)
        win.geometry("1380x780")
        win.transient(self)
        ttk.Label(win, text="En cada kill tuya: «antes» (1 s antes) y «después» (1,5 s después). Si la partida está "
                            "bien, en «después» aparece una línea nueva en el killfeed que en «antes» no está.",
                  style="H.TLabel", wraplength=1320).pack(anchor="w", padx=12, pady=(10, 2))
        ttk.Label(win, text="Si las dos imágenes son iguales, o la kill ya sale en «antes», esa partida está desfasada: "
                            "pulsa «Sincronizar a mano…» en su fila.",
                  style="Muted.TLabel", wraplength=1320).pack(anchor="w", padx=12, pady=(0, 8))
        outer = ttk.Frame(win, style="Panel.TFrame")
        outer.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        cv = tk.Canvas(outer, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        inner = ttk.Frame(cv, style="Panel.TFrame")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        win.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))
        win.bind("<Button-4>", lambda e: cv.yview_scroll(-1, "units"))
        win.bind("<Button-5>", lambda e: cv.yview_scroll(1, "units"))
        imgs = []

        def manual(mid):
            if mid in self.matches:
                self.tv_m.selection_set(mid)
                self.tv_m.see(mid)
                self.calibrate()

        for m, ks in rows:
            c = self.calib.get(m.match_id) or {}
            if c.get("check") == "dudosa":
                stt, col = f"⚠ Dudosa: parece {c.get('check_shift', 0):+.1f} s desplazada", YELLOW
            elif c.get("auto") and c.get("approx"):
                stt, col = "Auto ≈ (aprox.) — revisa", YELLOW
            elif c.get("auto"):
                stt, col = f"Auto ✔ ({int(c.get('conf', 0) * 100)}%)", "#7ee081"
            elif c.get("first_kill_vod_s") is not None:
                stt, col = "Manual ✔", "#7ee081"
            elif c.get("failed"):
                stt, col = "✖ No se encontró (por hora)", SYNC_COLORS["s_fail"]
            else:
                stt, col = "Sin sincronizar (por hora)", SYNC_COLORS["s_hora"]
            row = ttk.Frame(inner, style="Panel.TFrame")
            row.pack(fill="x", pady=6)
            info = ttk.Frame(row, style="Panel.TFrame", width=250, height=150)
            info.pack(side="left", fill="y", padx=(0, 10))
            info.pack_propagate(False)
            ttk.Label(info, text=f"{m.map_name} · {m.agent}", style="H.TLabel").pack(anchor="w")
            ttk.Label(info, text=f"{m.started_at.astimezone():%d/%m %H:%M} · {m.score}",
                      style="Muted.TLabel").pack(anchor="w")
            tk.Label(info, text=stt, bg=PANEL, fg=col, font=("Segoe UI Semibold", 9), wraplength=240,
                     justify="left").pack(anchor="w", pady=(2, 6))
            ui.Button(info, text="Sincronizar a mano…",
                       command=lambda mid=m.match_id: manual(mid)).pack(anchor="w")
            if not ks:
                ttk.Label(row, text="No hay kills tuyas dentro del VOD en esta partida.",
                          style="Muted.TLabel").pack(side="left")
            for i, (k, t, a, b) in enumerate(ks):
                box = ttk.Frame(row, style="Panel.TFrame")
                box.pack(side="left", padx=8)
                ttk.Label(box, text=f"Kill {i + 1} · ronda {k.round + 1} · {core.fmt_ts(t)}",
                          style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
                for col_, (png, cap) in enumerate(((a, "antes"), (b, "después"))):
                    try:
                        im = tk.PhotoImage(file=png)
                        imgs.append(im)
                        tk.Label(box, image=im, bg=BG, bd=0).grid(row=1, column=col_, padx=2)
                    except Exception:
                        tk.Label(box, text="(sin imagen)", bg=BG, fg=MUTED, width=36, height=6).grid(
                            row=1, column=col_, padx=2)
                    ttk.Label(box, text=cap, style="Muted.TLabel").grid(row=2, column=col_)
        win._imgs = imgs

        def close():
            shutil.rmtree(tmp, ignore_errors=True)
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)
        win._close = close
        return win

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
        ui.Button(bf, text="Guardar", style="Accent.TButton", command=save).pack(side="right")
        ui.Button(bf, text="Sin cámara (solo juego)", command=none).pack(side="right", padx=6)
        self.wait_window(dlg)

    def play(self, start, dur=None, clock=False):
        """Reproduce el VOD desde 'start'. clock=True muestra encima el minuto del VOD."""
        ffplay = core.find_tool("ffplay")
        if not ffplay or not self.v_vod.get():
            messagebox.showinfo(APP_NAME, f"Abre el VOD en tu reproductor y ve a {core.fmt_ts(start)}.")
            return
        vfile, lstart = core.vod_resolve(self.v_vod.get(), start)
        shift = start - lstart
        cmd = [ffplay, "-hide_banner", "-loglevel", "error", "-ss", f"{lstart:.2f}",
               "-window_title", "Vista previa  (flechas: ←/→ 10 s, ↑/↓ 1 min, espacio: pausa)",
               "-x", "1280", "-y", "720"]
        if dur:
            cmd += ["-t", f"{dur:.2f}", "-autoexit"]
        font = core._font_for_drawtext()
        if clock and font:
            cmd += ["-vf", f"drawtext=fontfile='{core._esc_filter_path(font)}'"
                           f":text='%{{pts\\:hms\\:{shift:.3f}}}':fontsize=h/14:fontcolor=0xFFD21F"
                           ":box=1:boxcolor=0x000000AA:boxborderw=12:x=(w-tw)/2:y=h/30"]
        cmd.append(vfile)
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
        try:                    # cada corte con un número exacto de fotogramas (clips sueltos exactos)
            core.snap_clips(self.clips, core._fps_of(self.v_vod.get()))
        except Exception:
            pass
        self.fill_clips()
        total = sum(c.vod_end - c.vod_start for c in self.clips)
        self.status(f"{len(self.clips)} rondas · {core.fmt_ts(total).split('.')[0]} de vídeo. "
                    f"Doble clic en una ronda para comprobarla.")

    def fill_clips(self):
        self.after_idle(self.update_kpis)
        self.tv_c.delete(*self.tv_c.get_children())
        for i, c in enumerate(self.clips):
            self.tv_c.insert("", "end", iid=str(i), tags=(("alt",) if i % 2 else ()), values=(
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
    def stream_folder(self) -> str:
        """Carpeta de este directo, con su fecha como en el Drive (dd-mm-aaaa): el vídeo completo, la
        vertical y los clips (3K, 4K, ACE…) juntos. Si ese día hubo otro directo, «dd-mm-aaaa (2)»."""
        vod = self.v_vod.get()
        root = os.path.dirname(os.path.abspath(vod))
        try:
            day = self.vod_start_utc().astimezone().strftime("%d-%m-%Y")
        except Exception:
            day = datetime.fromtimestamp(os.path.getmtime(vod)).strftime("%d-%m-%Y") if os.path.exists(vod) \
                else datetime.now().strftime("%d-%m-%Y")
        me = os.path.basename(vod)
        n = 1
        while True:
            d = os.path.join(root, day if n == 1 else f"{day} ({n})")
            mark = os.path.join(d, ".vod")
            try:
                with open(mark, encoding="utf-8") as f:
                    owner = f.read().strip()
            except OSError:
                owner = None
            if owner in (None, me):
                if owner is None and os.path.isdir(d) and any(
                        x.lower().endswith(".mp4") for x in os.listdir(d)):
                    n += 1               # carpeta de ese día ya usada por otro directo (sin marca)
                    continue
                return d
            n += 1

    def _mark_stream_folder(self, d: str) -> None:
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, ".vod"), "w", encoding="utf-8") as f:
                f.write(os.path.basename(self.v_vod.get()))
            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(os.path.join(d, ".vod"), 0x02)   # oculto
        except Exception:
            pass

    def default_out(self) -> str:
        d = self.stream_folder()
        self._mark_stream_folder(d)
        return os.path.join(d, os.path.basename(d) + ".mp4")

    def render(self, out=None):
        if not self.clips:
            self.compute()
            if not self.clips:
                return
        vod = self.v_vod.get()
        if not out:
            d = self.stream_folder()
            made_dir = not os.path.isdir(d)
            os.makedirs(d, exist_ok=True)
            self._mark_stream_folder(d)
            out = filedialog.asksaveasfilename(title="Guardar vídeo", defaultextension=".mp4",
                                               initialdir=d, initialfile=os.path.basename(self.default_out()),
                                               filetypes=[("MP4", "*.mp4")])
            if not out and made_dir:
                try:
                    os.rmdir(d)
                except OSError:
                    pass
        if not out:
            return
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
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
        sub_hl = self.v_subhl.get()
        capcut = subs and self.v_capcut.get()
        kfeed = self.v_kfeed.get()
        # con CapCut los subtítulos se editan allí: no se pegan en el vídeo ni se abre el editor
        sub_edit = subs and self.v_subedit.get() and not self.unattended() and not capcut
        warnings = []
        ranking = []

        def sub(title):
            self.after(0, lambda: self.lbl_step.configure(text=title))

        def work():
            try:
                stop = lambda: self.stop_flag
                # ¿cabe? vídeo + vertical + clips sueltos (y sus verticales)
                copies = 1 + (1 if vertical else 0) + ((2 if vertical else 1) if singles else 0)
                twitch.check_space(os.path.dirname(os.path.abspath(out)) or ".",
                                   core.estimate_bytes(vod, total_s) * copies, "generar los vídeos")
                base = os.path.splitext(out)[0]
                words = None
                if subs:
                    sub("Transcribiendo lo que dices para los subtítulos…")
                    try:
                        # lo transcrito en segundo plano mientras se sincronizaba se reaprovecha
                        jobs = [j for j in getattr(self, "sub_jobs", []) if not j.done()]
                        if jobs:
                            cb(None, f"Terminando los subtítulos que se hacían mientras sincronizaba…")
                        for j in getattr(self, "sub_jobs", []):
                            try:
                                j.result()
                            except Exception:
                                pass
                        self.sub_jobs = []
                        miss = subtitles.missing_ranges(vod, clips)
                        if miss:
                            cb(None, f"Transcribiendo {len(miss)} ronda(s)…")
                            subtitles.transcribe_ranges(vod, miss, cb, stop)
                        else:
                            cb(1, "Los subtítulos ya estaban hechos")
                        words = subtitles.words_for_clips(vod, clips)
                        if words is None:
                            raise RuntimeError("no se pudieron transcribir algunas rondas")
                        words = self.prepare_words(words)
                    except Exception as e:
                        if "Cancelado" in str(e):
                            raise
                        warnings.append(f"Subtítulos: {e}")
                v = base + " (vertical).mp4" if vertical else None
                kf_lines = None
                if vertical and kfeed:
                    sub("Recortando tus kills del killfeed para la vertical…")
                    try:
                        import hashlib
                        kdir = os.path.join(core.data_dir(), "killfeed",
                                            hashlib.sha1(os.path.abspath(out).encode("utf-8")).hexdigest()[:12])
                        kf_lines = killfeed.prepare(vod, clips, cam, kdir, cb, stop,
                                                    log_path=os.path.join(os.path.dirname(os.path.abspath(out)),
                                                                          "registro killfeed.txt"))
                    except Exception as e:
                        if "Cancelado" in str(e):
                            raise
                        warnings.append(f"Killfeed: {e}")
                        kf_lines = None
                sub("Generando el vídeo horizontal" + (" y el vertical a la vez…" if vertical else "…"))
                core.render(vod, clips, out, encoder=enc, show_label=False, progress=prog, should_stop=stop,
                            vout_path=v, cam=cam, words=None if capcut else words, highlight=sub_hl,
                            kf_lines=kf_lines)
                made = [out] + ([v] if v else [])
                if capcut and v and words:
                    subtitles.write_srt(words, os.path.splitext(v)[0], highlight=sub_hl)
                self.cfg.setdefault("generated_vods", {})[os.path.abspath(vod)] = time.time()
                outs_ = self.cfg.setdefault("generated_outputs", {}).setdefault(os.path.abspath(vod), [])
                for p_ in made:
                    if os.path.abspath(p_) not in outs_:
                        outs_.append(os.path.abspath(p_))
                self.after(0, self.persist)
                # todo junto en la carpeta del directo (si el vídeo se guardó en ella); si se eligió
                # otra carpeta a mano, los clips van en su propia subcarpeta para no desordenarla
                od_ = os.path.dirname(os.path.abspath(out))
                in_own = os.path.basename(od_) == os.path.basename(base)
                folder = (od_ if in_own else base + " - clips sueltos") if singles else None
                import publish
                chan = (self.v_channel.get() or "").strip().lstrip("@")
                try:
                    vs_ = self.vod_start_utc().strftime("%d/%m/%Y")
                except Exception:
                    vs_ = ""
                vtx = publish.video_texts(clips, vs_, chan)
                try:
                    publish.write_clip_txt(base + " - publicar.mp4", vtx)
                except OSError:
                    pass
                if singles:
                    sub("Sacando un clip por ronda…")
                    core.export_singles(out, clips, folder, vertical_cam=cam, vertical=vertical,
                                        encoder=enc, progress=cb, should_stop=stop, words=words,
                                        highlight=sub_hl, ranking=ranking, vsrc=v, channel=chan,
                                        video_texts=vtx, srt=capcut)
                    made.append(folder)
                    try:                       # para el top de la semana
                        semana.record(folder, self.vod_start_utc())
                    except Exception:
                        pass
                try:
                    sdate = self.vod_start_utc().strftime("%d-%m-%Y")
                except Exception:
                    sdate = datetime.now().strftime("%d-%m-%Y")
                proj = {"vod": vod, "clips": clips, "out": out, "vertical": v, "cam": cam, "words": words,
                        "date": sdate,
                        "capcut": capcut, "kf_lines": kf_lines,
                        "highlight": sub_hl, "encoder": enc, "singles": folder}
                self.save_project(proj)
                if words and sub_edit and v:
                    self.review_subs_blocking(proj, cb, sub, stop, ranking)
                return made
            finally:
                self.after(0, lambda: self.set_running(False))

        def finish(made):
            path = made[0]
            self.finish_steps("¡Vídeos listos!")
            st_ = dict(getattr(killfeed, "LAST_STATS", {}) or {})
            killfeed.LAST_STATS = {}
            if st_.get("total") and kfeed:
                if st_.get("missing"):
                    lst = "\n".join(f"  · {core.fmt_ts(t)}  ronda {r}" for t, r, _p in st_["missing"][:8])
                    more = "\n  …" if len(st_["missing"]) > 8 else ""
                    if not self.unattended() and messagebox.askyesno(
                            APP_NAME, f"Killfeed de la vertical: {st_['ok']} de {st_['total']} kills con su línea.\n\n"
                                      f"Sin línea ({len(st_['missing'])}):\n{lst}{more}\n\nHe guardado una foto del "
                                      "killfeed de cada una en la carpeta «killfeed sin línea». Si me la pasas, "
                                      "lo ajusto.\n\n¿Abrir esa carpeta?"):
                        try:
                            os.startfile(st_["pics"])
                        except Exception:
                            pass
                else:
                    self.status(f"Killfeed: las {st_['total']} kills con su línea ✔")
            self.status(path)
            if getattr(self, "sync_warning", ""):
                warnings.insert(0, self.sync_warning)
            dud = sorted({c.match.map_name for c in clips
                          if (self.calib.get(c.match.match_id) or {}).get("check") == "dudosa"})
            if dud and not getattr(self, "sync_warning", ""):
                warnings.insert(0, f"Ojo: estas partidas salieron dudosas en la comprobación y sus clips pueden "
                                   f"estar desfasados: {', '.join(dud)}.")
            if warnings:
                self.status(path + "   ⚠ " + " · ".join(warnings)[:200])
            if self.unattended():
                self.flow_done(f"Listo: {os.path.basename(path)}")
                return
            extra = ""
            if vertical:
                extra += "\n• Versión vertical 9:16"
            if singles:
                extra += "\n• Clips por ronda en carpetas 3K / 4K / ACE / Clutch"
            if ranking:
                extra += "\n\nLas mejores para subir (copiadas en la carpeta TOP):"
                for j, (sc, lab, _p, why) in enumerate(ranking[:3]):
                    extra += f"\n  {j + 1}. {lab} — {', '.join(why) or 'nota ' + format(sc, 'g')}"
            if self.flow_active:
                self.after(2000, lambda: self.flow_done(f"Listo: {os.path.basename(path)}"))
            if warnings:
                self.show_warnings(warnings, ask_report=bool(getattr(self, "sync_warning", "")))
            if messagebox.askyesno(APP_NAME, f"¡Listo! {len(clips)} rondas.\n\n• Vídeo horizontal{extra}\n\n"
                                             f"Todo está en:\n{os.path.dirname(path)}\n\n¿Abrir la carpeta?"):
                if os.name == "nt":
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        self.run_bg(work, finish, "No se pudo generar el vídeo")

    # ------------------------------------------------------------ corregir subtítulos después
    def project_path(self):
        return os.path.join(core.data_dir(), "ultimo_video.pkl")

    def save_project(self, proj):
        import pickle
        try:
            with open(self.project_path() + ".tmp", "wb") as f:
                pickle.dump(proj, f)
            os.replace(self.project_path() + ".tmp", self.project_path())
        except Exception:
            pass

    def load_project(self):
        import pickle
        try:
            with open(self.project_path(), "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def review_subs_blocking(self, proj, cb, sub, stop, ranking=None):
        """(en segundo plano) Abre el editor con el vídeo ya generado y espera. Si se cambia algo,
        rehace SOLO la vertical (una codificación desde el VOD) y vuelve a cortar sus clips."""
        sub("Revisa los subtítulos viendo el vídeo (ventana abierta)…")
        cb(None, "Esperando a que revises los subtítulos")
        ev, box = threading.Event(), {}

        def got(res):
            box["r"] = res
            ev.set()
        self.after(0, lambda: self.edit_subtitles(proj["words"], got, video=proj["out"],
                                                  highlight=proj["highlight"]))
        while not ev.wait(0.3):
            if self.stop_flag:
                raise RuntimeError("Cancelado.")
        new = box.get("r")
        if new == proj["words"]:
            return False
        proj["words"] = new
        sub("Rehaciendo la vertical con tus subtítulos…")
        core.render(proj["vod"], proj["clips"], proj["vertical"], encoder=proj["encoder"],
                    progress=lambda f, t: cb(f, "Vertical con los subtítulos corregidos…"), should_stop=stop,
                    cam=proj["cam"], words=new, highlight=proj["highlight"], vertical_only=True,
                    kf_lines=proj.get("kf_lines"))
        if proj.get("singles"):
            sub("Actualizando los clips verticales…")
            core.export_singles(proj["out"], proj["clips"], proj["singles"], vertical_cam=proj["cam"],
                                vertical=True, encoder=proj["encoder"], progress=cb, should_stop=stop,
                                words=new, highlight=proj["highlight"],
                                ranking=ranking if ranking is not None else [], vsrc=proj["vertical"])
        self.save_project(proj)
        return True

    def fix_subtitles_later(self):
        """Botón «Corregir subtítulos…»: reabre el último vídeo generado."""
        proj = self.load_project()
        if proj and proj.get("capcut"):
            messagebox.showinfo(APP_NAME, "El último vídeo se hizo con «Subtítulos para CapCut»: sus subtítulos "
                                          "se corrigen en CapCut, importando el archivo .srt que hay al lado de "
                                          "la vertical (Subtítulos > Añadir subtítulos > Importar archivo).")
            return
        if not proj or not proj.get("words") or not proj.get("vertical"):
            messagebox.showinfo(APP_NAME, "Todavía no hay un vídeo con subtítulos para corregir.\n\n"
                                          "Genera uno con «Versión vertical» y «Subtítulos» activados.")
            return
        if not all(os.path.exists(p) for p in (proj["out"], proj["vertical"])) or \
                not (os.path.isfile(proj["vod"]) or core.is_parts(proj["vod"]) and os.path.isfile(proj["vod"])):
            messagebox.showinfo(APP_NAME, "No encuentro el vídeo o el VOD de la última vez (¿se movió o se "
                                          "borró?). Para rehacer la vertical hace falta el VOD.")
            return
        self.stop_flag = False
        self.set_running(True)
        self.set_step(4, "Corrigiendo subtítulos…")
        cb = self.progress_cb()

        def sub(title):
            self.after(0, lambda: self.lbl_step.configure(text=title))

        def work():
            try:
                return self.review_subs_blocking(proj, cb, sub, lambda: self.stop_flag)
            finally:
                self.after(0, lambda: self.set_running(False))

        def done(changed):
            self.finish_steps("Subtítulos actualizados" if changed else "Sin cambios")
            self.status(proj["vertical"] if changed else "No cambiaste nada: la vertical se queda igual.")
        self.run_bg(work, done, "No se pudieron corregir los subtítulos")

    # ------------------------------------------------------------ Google Drive
    def _drive_status(self):
        ok = drive.is_connected()
        try:
            self.lbl_drive.configure(text="✔ Conectado" if ok else "Sin conectar: se abre el navegador para "
                                                                  "iniciar sesión en Google (solo la primera vez)")
            self.btn_drive_conn.configure(text="Cambiar de cuenta" if ok else "Conectar con Google Drive")
        except AttributeError:
            pass

    def drive_connect(self):
        if self.busy:
            return
        if drive.is_connected() and not messagebox.askyesno(
                APP_NAME, "Ya está conectado con Google Drive. ¿Conectar otra cuenta?"):
            return
        self.set_step(None, "Conectando con Google Drive…")
        self.set_progress(0, "Inicia sesión en Google en la ventana del navegador que se abre…")
        cb = self.progress_cb()

        def work():
            drive.ensure_rclone(cb)
            cb(0, "Inicia sesión en Google en la ventana del navegador que se abre y pulsa «Permitir»…")
            drive.connect()
            return True

        def done(_r):
            self.finish_steps("Google Drive conectado")
            self._drive_status()
        self.run_bg(work, done, "No se pudo conectar con Google Drive")

    def drive_upload_last(self, auto=False):
        """Sube la carpeta del último vídeo generado a Drive, en <subcarpeta>/<fecha del directo>/."""
        if self.busy:
            if auto:
                self.after(3000, lambda: self.drive_upload_last(auto=True))
            return
        proj = self.load_project()
        folder = os.path.dirname(os.path.abspath(proj["out"])) if proj and proj.get("out") else None
        if not folder or not os.path.isdir(folder):
            if not auto:
                messagebox.showinfo(APP_NAME, "Todavía no hay vídeos generados para subir.")
            return
        if not drive.is_connected():
            if auto:
                self.status("No se subió a Drive: falta conectar la cuenta (pestaña Automático).")
                return
            if not messagebox.askyesno(APP_NAME, "Para subir a Drive primero hay que conectar tu cuenta de Google "
                                                 "(se abre el navegador para iniciar sesión, solo la primera vez)."
                                                 "\n\n¿Conectar ahora?"):
                return
            self.drive_connect()
            return
        try:
            fid = drive.folder_id(self.v_drive_link.get())
        except ValueError:
            fid = None
        if not fid or fid == drive.EXAMPLE_DAY_ID:
            messagebox.showwarning(APP_NAME, "Falta el enlace de tu carpeta «Vortexs Clips» de Drive (donde van las "
                                             "carpetas de cada día).\n\nEn Drive, abre «Vortexs Clips» → Compartir → "
                                             "Copiar enlace, y pégalo en la pestaña Automático.")
            return
        # en Drive, la carpeta se llama igual que en tu PC (dd-mm-aaaa, o «dd-mm-aaaa (2)»)
        fname = os.path.basename(folder)
        date_txt = fname if re.match(r"^\d{2}-\d{2}-\d{4}( \(\d+\))?$", fname) else (
            proj.get("date") or datetime.fromtimestamp(os.path.getmtime(proj["out"])).strftime("%d-%m-%Y"))
        sub = self.v_drive_sub.get().strip()
        n_files, n_bytes = 0, 0
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if "en curso)" not in d]
            for fn in files:
                if fn.endswith((".tmp.mp4", ".part", ".pkl")):
                    continue
                n_files += 1
                try:
                    n_bytes += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
        if not auto and not messagebox.askyesno(
                APP_NAME, f"¿Subir a Google Drive los vídeos del último directo?\n\nCarpeta: {os.path.basename(folder)}\n"
                          f"{n_files} archivos · {n_bytes / 2**30:.1f} GB\n\nSe guardan en Drive en:\n"
                          f"{(sub + ' / ') if sub else ''}{date_txt} /\n\nRevisa antes que los vídeos estén bien: "
                          f"lo que subas queda en la carpeta compartida."):
            return
        self.persist()
        self.stop_flag = False
        self.set_running(True)
        self.set_step(None, "Subiendo a Google Drive…")
        cb = self.progress_cb()
        link = self.v_drive_link.get()

        def work():
            try:
                return drive.upload(folder, date_txt, link, sub, cb, lambda: self.stop_flag)
            finally:
                self.after(0, lambda: self.set_running(False))

        def done(dst):
            self.finish_steps("Subido a Google Drive")
            self.status(f"Subido a Drive: {dst}")
            if not auto and not self.unattended():
                messagebox.showinfo(APP_NAME, f"¡Subido a Google Drive!\n\n{dst}")
        self.run_bg(work, done, "No se pudo subir a Google Drive")

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

    def _is_working(self):
        return bool(self.busy or self.flow_active or str(self.btn_cancel.cget("state")) == "normal")

    def offer_update(self, info, silent):
        newer = info and info["version"] and updater._ver(info["version"]) > updater._ver(VERSION)
        working = self._is_working()
        if silent and newer and info["version"] in getattr(self, "_upd_declined", ()):
            return                      # ya dijiste que ahora no a esta versión
        if getattr(self, "_upd_asking", False):
            return
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
            self._upd_pending = info    # se avisa en cuanto termine
            if not silent:
                messagebox.showinfo(APP_NAME, "Hay una versión nueva. Te aviso para actualizar en cuanto "
                                              "termine lo que está haciendo el programa.")
            return
        notes = info["notes"].strip()
        notes = ("\n\nNovedades:\n" + notes[:600]) if notes else ""
        self._upd_asking = True
        try:
            ok = messagebox.askyesno(APP_NAME, f"Hay una versión nueva: {info['version']} "
                                               f"(tienes la {VERSION}).{notes}\n\n¿Actualizar ahora? El programa "
                                               "se actualiza y se vuelve a abrir solo en unos segundos "
                                               "(tus ajustes se guardan).")
        finally:
            self._upd_asking = False
        if not ok:
            getattr(self, "_upd_declined", set()).add(info["version"])
            return
        if updater.FROZEN and info.get("setup"):
            # primero la rápida (solo el programa, unos MB); si no se puede, el instalador completo
            self._run_update(lambda prog: updater.fast_update(info, prog) or
                             updater.run_installer(info["setup"], prog, silent=True))
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
                 {"start": 1.5, "end": 2.0, "text": "esto"}, {"start": 2.1, "end": 2.6, "text": "ACE!"}]
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
        # varias partidas a la vez + caché (al repetir no se vuelve a decodificar el vídeo)
        import copy
        sync.prune_cache(0)
        m2 = copy.deepcopy(m)
        m2.match_id = "t2"
        its = [(m, true_off - 37.5), (m2, true_off - 30.0)]
        a_ = time.time()
        res1 = sync.sync_many(svod, its, total_, 2)
        t1_ = time.time() - a_
        a_ = time.time()
        res2 = sync.sync_many(svod, its, total_, 2)
        t2_ = time.time() - a_
        allr = list(res1.values()) + list(res2.values())
        log(len(allr) == 4 and all(x.ok and abs(x.offset_s - true_off - 0.4) <= 0.5 for x in allr),
            f"sincronización en paralelo (2 partidas a la vez: {t1_:.1f} s; repetida desde caché: {t2_:.1f} s)")
        log(t2_ < t1_, "la caché acelera la repetición")
        log(core.kills_folder(3) == "3K" and core.kills_folder(4) == "4K" and core.kills_folder(5) == "ACE",
            "carpetas 3K / 4K / ACE")
        st2 = core.CutSettings(mode="kills", merge_gap_s=6.0, kill_pre_s=1.0, kill_post_s=1.0)
        mk = core.Match("k", "Prueba", datetime.now(timezone.utc), 0, "Jett", "me", "13-0")
        rk = core.Round(0, start_match_ms=0)
        mk.rounds = [rk]
        mk.kills = [core.Kill(0, t_ * 1000, t_ * 1000, "me", "b") for t_ in (10, 15, 27)]
        log(len(core._kill_segments(mk, rk, 100.0, st2, 0)) == 2, "juntar kills seguidas")
        # clutch 1 contra 3 ganado
        mc = core.Match("c", "Prueba", datetime.now(timezone.utc), 0, "Jett", "me", "13-0")
        mc.allies, mc.enemies = {"me", "a1", "a2", "a3", "a4"}, {"e1", "e2", "e3", "e4", "e5"}
        rc = core.Round(0, start_match_ms=0, won=True)
        mc.rounds = [rc]
        mc.kills = [core.Kill(0, i * 1000, i * 1000, kk, vv) for i, (kk, vv) in enumerate(
            [("e1", "a1"), ("a2", "e1"), ("a2", "e5"), ("e2", "a2"), ("e3", "a3"), ("e2", "a4"), ("me", "e2"),
             ("me", "e3"), ("me", "e4")])]
        core.compute_clutches(mc)
        log(rc.clutch_vs == 3 and core.is_clutch(rc, 2) and core.clip_folder(rc) == "Clutch", "clutch 1v3 detectado")
        # nota, ranking y carpeta TOP
        m.rounds[0].my_kills, m.rounds[1].my_kills = 4, 3
        sfold = os.path.join(tmp, "sueltos")
        rk_ = []
        core.export_singles(vid, [core.Clip(m, m.rounds[0], 0, 3), core.Clip(m, m.rounds[1], 3, 6)], sfold,
                            encoder="auto", ranking=rk_)
        log(len(rk_) == 2 and rk_[0][1].startswith("4K") and os.path.isfile(os.path.join(sfold, "ranking.txt"))
            and len(os.listdir(os.path.join(sfold, "TOP"))) == 2, "nota de jugadas, ranking y carpeta TOP")
        # fiabilidad: API guardada, espacio en disco, retomar generación, cortes del directo, aprendizaje
        real_net = core._get_net
        try:
            core._get_net = lambda u, k: {"data": [1]}
            core._get("/selftest", "k")
            core._get_net = lambda u, k: (_ for _ in ()).throw(core.ApiError("sin red", None))
            real_sleep = time.sleep
            time.sleep = lambda s_: None
            try:
                log(core._get("/selftest", "k") == {"data": [1]}, "sin conexión usa las partidas guardadas")
            finally:
                time.sleep = real_sleep
        finally:
            core._get_net = real_net
        try:
            twitch.check_space(tmp, 10 ** 16, "prueba")
            log(False, "aviso de espacio en disco")
        except RuntimeError:
            log(True, "aviso de espacio en disco")
        rclips = [core.Clip(m, m.rounds[0], 0.5, 2.5), core.Clip(m, m.rounds[1], 3.0, 5.0)]
        rout = os.path.join(tmp, "r.mp4")
        core.render(vid, rclips, rout, encoder="auto")
        msgs_ = []
        core.render(vid, rclips, rout, encoder="auto", progress=lambda f_, t_: msgs_.append(t_))
        log(any("ya estaba" in x for x in msgs_), "retomar: no se rehace lo ya generado")
        # 1.14: horizontal + vertical en UNA pasada, clips copiados sin recodificar, textos para publicar
        dvo, dvv = os.path.join(tmp, "d.mp4"), os.path.join(tmp, "d (vertical).mp4")
        core.render(vid, rclips, dvo, encoder="auto", vout_path=dvv, cam=cam, words=words)
        ih_, iv_ = core.probe_vod(dvo), core.probe_vod(dvv)
        log(ih_["width"] == 1920 and iv_["height"] == 1920 and abs(ih_["duration"] - iv_["duration"]) < 0.3,
            f"horizontal y vertical en la misma pasada ({ih_['duration']:.2f} s / {iv_['duration']:.2f} s)")
        dfold = os.path.join(tmp, "sueltos2")
        a_ = time.time()
        core.export_singles(dvo, rclips, dfold, vertical_cam=cam, vertical=True, encoder="auto", words=words,
                            ranking=[], vsrc=dvv, channel="vortexs")
        mp4s_ = [os.path.join(d_, f_) for d_, _, fs_ in os.walk(dfold) for f_ in fs_
                 if f_.endswith(".mp4") and "TOP" not in d_]
        log(len(mp4s_) == 4 and all(abs(core.probe_vod(p_)["duration"] - 2.0) < 0.25 for p_ in mp4s_),
            f"clips sueltos copiados sin recodificar ({time.time() - a_:.1f} s)")
        log(os.path.isfile(os.path.join(dfold, "publicar.txt")) and os.path.isfile(os.path.join(dfold, "publicar.json"))
            and any(f_.endswith(".txt") for _, _, fs_ in os.walk(dfold) for f_ in fs_ if f_ != "publicar.txt"),
            "títulos, descripción y hashtags por clip")
        # top de la semana con esas jugadas
        from datetime import timedelta as _td
        mon_, sun_ = semana.last_week()
        semana.record(dfold, mon_ + _td(days=1))
        it_ = semana.pick(mon_, sun_)
        if len(it_) >= 2:
            th_ = semana.render_top(it_, os.path.join(tmp, "top_h.mp4"), False, "auto")
            tv_ = semana.render_top(semana.pick(mon_, sun_, vertical=True), os.path.join(tmp, "top_v.mp4"), True,
                                    "auto")
            ih2, iv2 = core.probe_vod(th_), core.probe_vod(tv_)
            log(ih2["width"] == 1920 and iv2["height"] == 1920 and ih2["duration"] > 3,
                f"top de la semana ({len(it_)} jugadas, {ih2['duration']:.1f} s)")
        else:
            log(False, f"top de la semana: solo {len(it_)} jugadas en el historial")
        # subtítulos para CapCut: .srt palabra a palabra (con y sin colores) y lista de palabras clave
        srts_ = subtitles.write_srt(words, os.path.join(tmp, "capcut"))
        ok_srt = len(srts_) == 3 and "00:00:00,500 --> " in open(srts_[1], encoding="utf-8-sig").read()
        log(ok_srt, "subtítulos para CapCut (.srt con y sin colores + palabras clave)")
        # Google Drive: enlace, permiso de rclone (en los dos formatos) y el programa de subida
        try:
            import base64
            tok = {"access_token": "x", "token_type": "Bearer", "refresh_token": "y", "expiry": "2030-01-01T00:00:00Z"}
            old = "Paste the following into your remote machine --->\n" + json.dumps(tok) + "\n<---End paste"
            new = "Paste the following into your remote machine --->\n" + base64.b64encode(
                json.dumps({"token": json.dumps(tok)}).encode()).decode() + "\n<---End paste"
            ok_d = drive.folder_id("https://drive.google.com/drive/folders/" + drive.EXAMPLE_DAY_ID + "?usp=drive_link") == drive.EXAMPLE_DAY_ID and \
                drive._parse_token(old) == tok and drive._parse_token(new) == tok and \
                drive.dest_path("", "25-11-2026") == "vcdrive:25-11-2026"
            exe = drive.ensure_rclone()
            ver = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=60).stdout
            log(ok_d and "rclone v" in ver, f"Google Drive (enlace, permiso y {ver.splitlines()[0] if ver else 'rclone'})")
        except Exception as e:
            log(False, f"Google Drive: {e}")
        kpng = os.path.join(tmp, "kf.png")
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x3ec1a5@1.0:s=420x36,format=rgba", "-frames:v", "1", kpng], check=True,
                       **core._no_window())
        kvo = os.path.join(tmp, "k (vertical).mp4")
        core.render(vid, rclips, os.path.join(tmp, "k.mp4"), encoder="auto", vout_path=kvo, cam=cam,
                    kf_lines=[{"png": kpng, "w": 746, "h": 64, "x": 320, "y": 900, "start": 0.5, "end": 2.0},
                              {"png": kpng, "w": 746, "h": 64, "x": 320, "y": 972, "start": 2.5, "end": 4.0}])
        fr_ = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-ss", "1.5", "-i", kvo, "-frames:v", "1",
                              "-vf", "crop=10:10:600:927", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, **core._no_window()).stdout
        g_ = sum(fr_[1::3]) / max(1, len(fr_) // 3)
        log(core.probe_vod(kvo)["height"] == 1920 and g_ > 150,
            f"killfeed en la vertical (línea encima a 1,5 s: verde {g_:.0f})")
        vsolo = os.path.join(tmp, "solo (vertical).mp4")
        core.render(vid, rclips, vsolo, encoder="auto", cam=cam, words=words[:2], vertical_only=True)
        log(core.probe_vod(vsolo)["height"] == 1920, "rehacer solo la vertical (subtítulos corregidos)")
        # VOD descargado solo por partidas (lista HLS local con trozos de 1 s)
        hdir = os.path.join(tmp, "hls")
        os.makedirs(hdir, exist_ok=True)
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", vid, "-c:v", "libx264",
                        "-preset", "ultrafast", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", "-c:a", "aac",
                        "-f", "hls",
                        "-hls_time", "1", "-hls_list_size", "0", "-hls_segment_filename",
                        os.path.join(hdir, "s%03d.ts"), os.path.join(hdir, "i.m3u8")], check=True, **core._no_window())
        import pathlib
        _mg = twitch.merge_sections
        twitch.merge_sections = lambda s_, d_, join_gap=0: _mg(s_, d_, 0)
        try:
            pinfo = {"id": "st", "title": "t", "duration": 6.0, "start": datetime.now(timezone.utc), "size_est": 0}
            man = twitch.download_sections("x", os.path.join(tmp, "dl"), [(0.2, 1.8), (3.5, 5.5)], info=pinfo,
                                           m3u8=pathlib.Path(os.path.join(hdir, "i.m3u8")).as_uri())
        finally:
            twitch.merge_sections = _mg
        pm = core.load_parts(man)
        log(len(pm["parts"]) == 2 and core.probe_vod(man)["duration"] == 6.0 and not core.vod_covers(man, 2.2, 2.8),
            f"descargar solo las partidas ({len(pm['parts'])} tramos)")
        pr_ = os.path.join(tmp, "p.mp4")
        core.render(man, [core.Clip(m, m.rounds[0], 0.5, 1.5), core.Clip(m, m.rounds[1], 4.0, 5.0)], pr_,
                    encoder="auto")
        log(abs(core.probe_vod(pr_)["duration"] - 2.0) < 0.2, "generar desde el VOD por partidas")
        SR = sync.SyncResult
        gd = {"a": SR(1, 0.0, .8, True), "b": SR(1, 0.5, .8, True), "c": SR(1, 26.0, .8, True)}
        log(len(sync.stream_jumps(gd, {"a": 0, "b": 100, "c": 300})) == 1, "detectar cortes del directo")
        smp = [{"mode": "killfeed", "true": True, "halves_ok": True, "z": 9.0, "ratio": 1.9}] * 4 + \
              [{"mode": "killfeed", "true": False, "halves_ok": False, "z": 8.5, "ratio": 1.6}]
        th = sync.compute_thresholds(smp).get("killfeed")
        log(bool(th) and th[0] > 8.5 and th[0] < 13, f"aprender umbrales de tus correcciones ({th})")
        app = App()
        log(True, "ventana creada")
        # antes de borrar un VOD a los 5 días se comprueba que sus vídeos están y se abren
        fake_vod = os.path.join(tmp, "viejo.mp4")
        shutil.copy2(vid, fake_vod)
        app.cfg["generated_vods"] = {fake_vod: time.time() - 6 * 86400}
        app.cfg["downloaded_vods"] = {fake_vod: time.time() - 7 * 86400}
        app.cfg["generated_outputs"] = {fake_vod: [os.path.join(tmp, "no_existe.mp4")]}
        app.v_autodel.set(True)
        app.v_vod.set("")
        _sw = messagebox.showwarning
        messagebox.showwarning = lambda *a_, **k_: None
        try:
            app.purge_old_vods()
            kept_ = os.path.isfile(fake_vod)
            app.cfg["generated_outputs"] = {fake_vod: [dvo]}
            app.purge_old_vods()
            gone_ = not os.path.isfile(fake_vod)
        finally:
            messagebox.showwarning = _sw
        log(kept_ and gone_, f"borrado a los 5 días solo si los vídeos están bien (guarda={kept_}, borra={gone_})")
        # editor de subtítulos
        got = []
        dlg = app.edit_subtitles(words, got.append)
        api = dlg._api
        api["tv"].selection_set(["0"])
        api["set_color"]("p")
        api["tv"].selection_set(["1"])
        api["delete"]()
        api["finish"](api["words"])
        log(bool(got) and got[0] and got[0][0].get("hl") == "p" and len(got[0]) == 3, "editor de subtítulos")
        got2 = []
        dlg2 = app.edit_subtitles(words, got2.append, video=dvo)
        api2 = dlg2._api
        api2["on_time"](words[0]["start"] + 0.05)
        ok_ov = len(api2["overlay"]["items"]) > 0
        api2["tv"].selection_set(["0"])
        api2["text"].set("VAMOS")
        api2["apply"]()
        api2["on_time"](words[0]["start"] + 0.06)
        api2["finish"](api2["words"])
        log(ok_ov and got2 and got2[0][0]["text"] == "VAMOS", "editor de subtítulos viendo el vídeo")
        got3 = []
        dlg3 = app.edit_subtitles(words, got3.append)
        api3 = dlg3._api
        api3["tv"].selection_set(["0"])
        api3["select_phrase"]()
        n_phrase = len(api3["tv"].selection())
        api3["select_all"]()
        api3["set_color"]("y")
        all_y = all(w.get("hl") == "y" for w in api3["words"])
        api3["tv"].selection_set(["0", "1"])
        api3["text"].set("vale mira")
        api3["apply"]()
        merged = len(api3["words"]) == 3 and api3["words"][0]["text"] == "vale mira"
        api3["tv"].selection_set(["1", "2"])
        api3["delete"]()
        api3["finish"](api3["words"])
        # diccionario (aprende lo corregido) y ajuste de tiempo
        app.cfg["sub_dict"] = {}
        got4 = []
        dlg4 = app.edit_subtitles([{"start": 0.5, "end": 0.9, "text": "vortex", "raw": "vortex"},
                                   {"start": 1.0, "end": 1.4, "text": "clips", "raw": "clips"}], got4.append)
        api4 = dlg4._api
        api4["tv"].selection_set(["0"])
        api4["text"].set("Vortexs")
        api4["apply"]()
        api4["move"](0.1)
        api4["move"](0.1)
        api4["keep"].set(True)
        api4["finish"](api4["words"])
        learned = app.cfg.get("sub_dict", {}).get("vortex") == "Vortexs"
        moved_ok = got4 and abs(got4[0][0]["start"] - 0.7) < 0.01 and abs(app.sub_offset() - 0.2) < 0.01
        pw = app.prepare_words([{"start": 1.0, "end": 1.3, "text": "Vortex,"}])
        log(learned and moved_ok and pw[0]["text"] == "Vortexs," and abs(pw[0]["start"] - 1.2) < 0.01,
            f"subtítulos: diccionario y ajuste de tiempo (aprende={learned}, mueve={bool(moved_ok)})")
        app.v_suboff.set("0.0")
        one = subtitles.chunk_words_rich(words)
        log(len(one) == len(words) and all(len(it) == 1 for _a, _b, it in one)
            and all(one[i][1] <= one[i + 1][0] for i in range(len(one) - 1)),
            "subtítulos palabra a palabra sin solaparse")
        dd = app.edit_sub_dict()
        dd._api["a"].set("tres kills")
        dd._api["b"].set("3K")
        dd._api["add"]()
        ok_dd = app.cfg["sub_dict"].get("tres kills") == "3K"
        dd.destroy()
        log(ok_dd, "ventana del diccionario")
        log(n_phrase >= 2 and all_y and merged and got3 and len(got3[0]) == 1,
            f"subtítulos: varias a la vez (frase={n_phrase}, color={all_y}, juntar={merged})")
        # revisión rápida de la sincro
        rtmp = tempfile.mkdtemp(prefix="vc_rev_")
        a_png, b_png = os.path.join(rtmp, "a.png"), os.path.join(rtmp, "b.png")
        core.grab_crop(vid, 1.0, a_png, sync.REGIONS["killfeed"][1], 300)
        core.grab_crop(vid, 2.0, b_png, sync.REGIONS["killfeed"][1], 300)
        mm = core.Match("rv", "Prueba", datetime.now(timezone.utc), 0, "Jett", "me", "13-0")
        win = app._show_review([(mm, [(core.Kill(0, 1000, 1000, "me", "b"), 1.5, a_png, b_png)])], rtmp)
        log(len(win._imgs) == 2, "ventana de revisión de la sincro")
        win._close()
        log(sync_tag({"auto": True, "conf": 0.75}) == "s_ok" and sync_tag({"failed": True}) == "s_fail"
            and sync_tag({"auto": True, "approx": True}) == "s_approx" and sync_tag({}) == "s_hora",
            "colores de la tabla de partidas")
        app.matches = {"c": mc}
        app.calib = {"c": {"failed": True}}
        app.refresh_matches()
        log(app.tv_m.exists("c"), "tabla de partidas con colores")
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
        dur = core.probe_vod(wav)["duration"]
        mono = all(b["start"] >= a["start"] for a, b in zip(ws, ws[1:]))
        inside = bool(ws) and ws[0]["start"] >= 0 and ws[-1]["end"] <= dur + 0.5
        # la voz de prueba empieza tras 2 s de silencio: el primer subtítulo no puede salir antes
        early = bool(ws) and ws[0]["start"] < 1.6
        # tras la pausa de 4 s («…Vortexs Clips.» | «Mira esta jugada…») no puede haber palabras metidas
        # en el silencio: el hueco más largo entre palabras tiene que ser de al menos 2,5 s
        gaps = [b["start"] - a["end"] for a, b in zip(ws, ws[1:])]
        pause_ok = bool(gaps) and max(gaps) >= 2.5
        ok = len(ws) >= 2 and mono and inside and not early and pause_ok
        res = ("OK  " if ok else "FAIL") + f" transcripción ({len(ws)} palabras, modo «{subtitles.LAST_MODE}», " \
              f"en orden={mono}, dentro={inside}, pausa respetada={pause_ok} ({max(gaps) if gaps else 0:.1f} s), " \
              f"primera a {ws[0]['start'] if ws else -1:.2f} s): {txt[:200]}\n"
        res += "\n".join(f"  {w['start']:6.2f}-{w['end']:6.2f} {w['text']}" for w in ws[:40])
        # subtítulos por tramos del VOD (los que se hacen mientras se sincroniza) y su reaprovechamiento
        from types import SimpleNamespace as _NS
        n_ = subtitles.transcribe_ranges(wav, [(1.5, 7.8), (10.5, min(dur, 15.0))])
        cl_ = [_NS(vod_start=2.0, vod_end=7.5), _NS(vod_start=11.0, vod_end=min(dur, 14.8))]
        w2 = subtitles.words_for_clips(wav, cl_)
        tot_ = sum(c.vod_end - c.vod_start for c in cl_)
        ok_r = (n_ >= 4 and w2 is not None and len(w2) >= 4 and not subtitles.missing_ranges(wav, cl_)
                and all(0 <= w["start"] < tot_ for w in w2))
        res += ("\n" + ("OK  " if ok_r else "FAIL") + f" subtítulos por tramos del VOD ({n_} palabras, "
                f"{len(w2 or [])} en los clips): " + " ".join(w["text"] for w in (w2 or []))[:160])
        ok = ok and ok_r
        # versión para NVIDIA: se baja y, si este PC no la puede usar, se sigue con el procesador
        if "--gpu-test" in sys.argv:
            subtitles._NVIDIA["v"] = True
            g_ = subtitles.ensure_gpu_whisper()
            ws_g = subtitles.transcribe(wav)
            ok_g = bool(g_) and len(ws_g) >= 2
            res += ("\n" + ("OK  " if ok_g else "FAIL") + f" transcriptor NVIDIA: descargado={bool(g_)}, "
                    f"modo «{subtitles.LAST_MODE}», {len(ws_g)} palabras "
                    f"(sin gráfica en GitHub: {'falla y sigue con el procesador' if 'GPU' not in subtitles.LAST_MODE else 'funciona'})")
            ok = ok and ok_g
        res += "\nEN BRUTO (from-to | tokens texto@t_dtw):"
        for sg in (subtitles.LAST_RAW.get("transcription") or [])[:40]:
            o_ = sg.get("offsets") or {}
            tk_ = " ".join(f"{(t.get('text') or '').strip()}@{t.get('t_dtw')}" for t in sg.get("tokens") or [])
            res += f"\n  {o_.get('from')}-{o_.get('to')} | {tk_}"
    except Exception:
        ok, res = False, "FAIL " + traceback.format_exc()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(res + "\n")
    sys.exit(0 if ok else 1)


def demo_screenshot(out_png):
    """Abre la ventana con datos de ejemplo y guarda una captura (para revisar el diseño en GitHub)."""
    from datetime import timezone as _tz
    os.environ.setdefault("APPDATA", tempfile.mkdtemp())
    App.check_update = lambda self, silent=True: None
    App.restore_last_vod = lambda self: None
    App.mirror_delay_log = lambda self, *a: None
    messagebox.showinfo = messagebox.showwarning = messagebox.showerror = lambda *a, **k: None
    messagebox.askyesno = lambda *a, **k: False
    app = App()
    app.vod_info = {"duration": 25386.0, "width": 1920, "height": 1080, "start_guess": None}
    app.v_vod.set(r"C:\Users\Vortexs\Videos\Vortexs Clips\2026-11-21 Twitch v2936050309.mp4")
    app.v_start.set("21/11/2026 13:14:53")
    try:
        app.state("zoomed")
    except Exception:
        app.geometry("1600x900+0+0")
    base = datetime(2026, 11, 21, 12, 40, tzinfo=_tz.utc)
    maps = [("Corrode", "Hush", "3-13"), ("Summit", "Phoenix", "13-8"), ("Abyss", "Sova", "14-12"),
            ("Abyss", "Hush", "13-11"), ("Corrode", "Phoenix", "9-13"), ("Haven", "Phoenix", "13-11"),
            ("Bind", "Chamber", "13-6"), ("Sunset", "Sova", "13-10"), ("Abyss", "Hush", "11-2"),
            ("Summit", "Sova", "13-3")]
    calibs = [{"auto": True, "conf": .75, "offset_s": 1}, {"auto": True, "conf": .65, "offset_s": 1},
              {"auto": True, "conf": .75, "offset_s": 1}, {"auto": True, "conf": .65, "offset_s": 1},
              {"auto": True, "conf": .35, "approx": True, "offset_s": 1},
              {"auto": True, "conf": .55, "offset_s": 1, "check": "dudosa", "check_shift": 26.3},
              {"auto": True, "conf": .55, "offset_s": 1}, {"failed": True}, {"auto": True, "conf": .75, "offset_s": 1},
              {}]
    app.matches, app.calib = {}, {}
    for i, (mp, ag, sc) in enumerate(maps):
        m = core.Match(f"demo{i}", mp, base + timedelta(minutes=35 * i), 2_000_000, ag, "me", sc)
        m.rounds = [core.Round(j, my_kills=(3 + (i + j) % 3) if j % 4 == 0 else 1) for j in range(20)]
        app.matches[m.match_id] = m
        app.calib[m.match_id] = calibs[i]
    app.checked = set(app.matches)
    app.refresh_matches()
    for i, (lab, a, b, d) in enumerate([("4K 1v3 · Haven · Ronda 7", "4:21:05", "4:21:48", "43 s"),
                                        ("ACE · Abyss · Ronda 12", "5:48:10", "5:49:02", "52 s"),
                                        ("3K · Summit · Ronda 4", "1:02:33", "1:03:01", "28 s"),
                                        ("1v2 · Bind · Ronda 18", "5:20:40", "5:21:30", "50 s")]):
        try:
            app.tv_c.insert("", "end", values=(lab, a, b, d))
        except Exception:
            pass
    app.update_kpis()
    app.kpi["plays"].configure(text="4")
    app.set_step(2, "Sincronizando 10 partidas con el VOD…")
    app.set_progress(0.62, "6 de 10 listas (2 a la vez) · Haven 17:30: Buscando el patrón de Haven…")

    os.makedirs(out_png, exist_ok=True)

    def grab(win, name):
        from PIL import ImageGrab
        win.update()
        x, y = win.winfo_rootx(), win.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(os.path.join(out_png, name))

    steps = []

    def page(k, name):
        def f():
            app.show_page(k)
            app.after(700, lambda: grab(app, name))
        return f

    def sett(k, name):
        def f():
            app.open_settings(k)
            app.after(900, lambda: grab(app, name))
        return f
    steps.append(page("partidas", "1_principal.png"))
    steps.append(page("vod", "2_vod.png"))
    steps.append(page("videos", "3_videos.png"))
    for k, nm in (("cuenta", "4a_ajustes_cuenta.png"), ("cortar", "4b_ajustes_cortes.png"),
                  ("videos", "4c_ajustes_videos.png"), ("carpeta", "4d_ajustes_carpeta.png"),
                  ("auto", "4e_ajustes_auto.png")):
        steps.append(sett(k, nm))


    def review():
        from PIL import Image, ImageDraw
        app.show_page("partidas")
        tmpd = tempfile.mkdtemp()
        rows = []
        for j, mid in enumerate(list(app.matches)[4:7]):
            ks = []
            for k in range(2):
                pa, pb = os.path.join(tmpd, f"{j}{k}a.png"), os.path.join(tmpd, f"{j}{k}b.png")
                for pth, lines in ((pa, 2), (pb, 3)):
                    im = Image.new("RGB", (260, 84), (30, 34, 40))
                    d = ImageDraw.Draw(im)
                    for ln in range(lines):
                        d.rectangle((52, 6 + ln * 25, 254, 24 + ln * 25), fill=(70, 70, 78) if ln < 2 else (190, 60, 60))
                    im.save(pth)
                ks.append((core.Kill(k, 1000, 1000, "me", "b"), 15000 + 300 * j + 60 * k, pa, pb))
            rows.append((app.matches[mid], ks))
        w = app._show_review(rows, tmpd)
        w.geometry("1380x620+60+60")
        app.after(900, lambda: (grab(w, "5_revision.png"), w._close()))
    steps.append(review)

    def subs():
        words = [{"start": 0.4 + i * 0.45, "end": 0.8 + i * 0.45, "text": t}
                 for i, t in enumerate("madre mía qué ACE con la sheriff tío esto es una locura".split())]
        words[1]["hl"] = "p"
        d = app.edit_subtitles(words, lambda r: None)
        d.geometry("760x640+80+80")
        app.after(900, lambda: (grab(d, "6_subtitulos.png"), d.destroy()))
    steps.append(subs)


    def small():
        try:
            app.state("normal")
        except Exception:
            pass
        app.geometry("1165x832+0+0")
        app.show_page("partidas")
        app.after(1500, lambda: grab(app, "8_ventana_pequena.png"))
    steps.append(small)
    steps.append(app.destroy)

    def run(i=0):
        try:
            steps[i]()
        except Exception as e:
            with open(os.path.join(out_png, f"error_{i}.txt"), "w", encoding="utf-8") as f:
                f.write(repr(e))
        if i + 1 < len(steps):
            app.after(1800, lambda: run(i + 1))
    app.after(3500, run)
    app.mainloop()


if __name__ == "__main__":
    if "--demo-screenshot" in sys.argv:
        demo_screenshot(sys.argv[sys.argv.index("--demo-screenshot") + 1])
        sys.exit(0)
    if "--selftest-subs" in sys.argv:
        i = sys.argv.index("--selftest-subs")
        selftest_subs(sys.argv[i + 1], sys.argv[i + 2])
    elif "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
    else:
        App().mainloop()
