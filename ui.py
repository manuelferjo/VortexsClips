"""
Aspecto de Vortexs Clips, el mismo que Vortexs Live: colores, fuentes (Outfit, Plus Jakarta Sans y
JetBrains Mono, incluidas), tarjetas redondeadas, interruptores con el degradado de la marca, pestañas
tipo píldora y botón principal en degradado morado -> amarillo.
Todo se dibuja con imágenes generadas al arrancar (con bordes suavizados), sin dependencias.
"""
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- colores (los de Vortexs Live)
BG = "#09090c"
SURFACE = "#111116"
ELEVATED = "#171720"
INPUT = "#0e0e13"
BORDER = "#26262e"
BORDER_HI = "#383646"
TEXT = "#f4f4f5"
MUTED = "#b2b2bc"
DIM = "#868692"
VIOLET = "#c09afd"
VIOLET_L = "#d6bfff"
YELLOW = "#ede103"
RED = "#ff4655"
GREEN = "#34d399"
SEL = "#2a2340"          # fila / pestaña seleccionada (morado muy oscuro)
HOVER = "#1f1f2a"


def res_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- fuentes
_FAM = {"display": "Segoe UI Black", "body": "Segoe UI", "semi": "Segoe UI Semibold", "mono": "Consolas"}
_SEMI_WEIGHT = "normal"


def load_fonts():
    """Antes de crear la ventana: registra las fuentes incluidas solo para este programa."""
    if os.name != "nt":
        return
    try:
        import ctypes
        d = os.path.join(res_dir(), "fonts")
        for f in os.listdir(d):
            if f.lower().endswith(".ttf"):
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(d, f), 0x10, 0)   # FR_PRIVATE
    except Exception:
        pass


def init_fonts(root):
    """Tras crear la ventana: elige las familias que de verdad hay."""
    global _SEMI_WEIGHT
    try:
        fams = {f.lower(): f for f in tkfont.families(root)}
    except Exception:
        fams = {}

    def pick(*names):
        for n in names:
            if n.lower() in fams:
                return fams[n.lower()]
        return None
    _FAM["display"] = pick("Outfit", "Outfit Bold") or _FAM["display"]
    body = pick("Plus Jakarta Sans")
    if body:
        _FAM["body"] = body
        semi = pick("Plus Jakarta Sans SemiBold")
        if semi:
            _FAM["semi"] = semi
        else:
            _FAM["semi"], _SEMI_WEIGHT = body, "bold"
    _FAM["mono"] = pick("JetBrains Mono Medium", "JetBrains Mono") or _FAM["mono"]


def display(size):
    return (_FAM["display"], size, "bold")


def body(size):
    return (_FAM["body"], size)


def semi(size):
    return (_FAM["semi"], size, _SEMI_WEIGHT) if _SEMI_WEIGHT != "normal" else (_FAM["semi"], size)


def bold(size):
    return (_FAM["body"], size, "bold")


def mono(size):
    return (_FAM["mono"], size)


# ---------------------------------------------------------------- imágenes con bordes suaves
def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _hex(t):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in t)


def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def grad_at(t):
    """Degradado de la marca: morado -> amarillo."""
    return _lerp(_rgb(VIOLET), _rgb(YELLOW), max(0.0, min(1.0, t)))


def _in_rrect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    dx = max(x0 + r - x, 0, x - (x1 - r))
    dy = max(y0 + r - y, 0, y - (y1 - r))
    return dx * dx + dy * dy <= r * r


def compose(w, h, layers, under=SURFACE, ss=3):
    """
    Imagen w x h: capas [(dentro(x,y)->bool, color(x,y)->rgb)] una encima de otra, con bordes suavizados.
    Lo que queda fuera de todas es transparente; los bordes se mezclan con «under».
    """
    img = tk.PhotoImage(width=w, height=h)
    und = _rgb(under)
    offs = [(i + 0.5) / ss for i in range(ss)]
    n = ss * ss
    clear = []
    for y in range(h):
        row = []
        for x in range(w):
            acc = [0.0, 0.0, 0.0]
            cov = 0
            for oy in offs:
                for ox in offs:
                    px, py = x + ox, y + oy
                    col = None
                    for inside, color in layers:
                        if inside(px, py):
                            col = color(px, py)
                    if col is not None:
                        cov += 1
                        acc[0] += col[0]
                        acc[1] += col[1]
                        acc[2] += col[2]
            if cov == 0:
                row.append(_hex(und))
                clear.append((x, y))
                continue
            c = (acc[0] / cov, acc[1] / cov, acc[2] / cov)
            a = cov / n
            row.append(_hex(_lerp(und, c, a)))
        img.put("{" + " ".join(row) + "}", to=(0, y))
    for x, y in clear:
        img.transparency_set(x, y, True)
    return img


def rrect_img(w, h, r, fill, border=None, bw=1, gradient=False, under=SURFACE):
    fr = (lambda x, y: grad_at(x / w)) if gradient else (lambda x, y, c=_rgb(fill): c)
    layers = []
    if border:
        bc = _rgb(border)
        layers.append((lambda x, y: _in_rrect(x, y, 0, 0, w, h, r), lambda x, y: bc))
        layers.append((lambda x, y: _in_rrect(x, y, bw, bw, w - bw, h - bw, max(0, r - bw)), fr))
    else:
        layers.append((lambda x, y: _in_rrect(x, y, 0, 0, w, h, r), fr))
    return compose(w, h, layers, under)


_CACHE = {}


def cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


# ---------------------------------------------------------------- estilo ttk
def apply_style(root):
    init_fonts(root)
    s = ttk.Style(root)
    s.theme_use("clam")
    base = body(10)
    s.configure(".", background=SURFACE, foreground=TEXT, fieldbackground=INPUT, bordercolor=BORDER,
                lightcolor=BORDER, darkcolor=BORDER, troughcolor=INPUT, focuscolor=VIOLET, font=base)
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=SURFACE)
    s.configure("Head.TFrame", background=BG)
    s.configure("Bg.TFrame", background=BG)
    s.configure("TLabel", background=SURFACE, foreground=TEXT, font=base)
    s.configure("Muted.TLabel", background=SURFACE, foreground=DIM, font=body(9))
    s.configure("Cap.TLabel", background=SURFACE, foreground=DIM, font=mono(8))
    s.configure("H.TLabel", background=SURFACE, foreground=TEXT, font=display(14))
    s.configure("HSub.TLabel", background=SURFACE, foreground=DIM, font=body(9))
    s.configure("Status.TLabel", background=BG, foreground=MUTED)

    # campos redondeados
    fn = cached("field_n", lambda: rrect_img(40, 34, 9, INPUT, BORDER))
    ff = cached("field_f", lambda: rrect_img(40, 34, 9, INPUT, VIOLET))
    fh = cached("field_h", lambda: rrect_img(40, 34, 9, INPUT, BORDER_HI))
    fd = cached("field_d", lambda: rrect_img(40, 34, 9, SURFACE, BORDER))
    try:
        s.element_create("VField.field", "image", fn, ("disabled", fd), ("focus", ff), ("hover", fh),
                         border=10, sticky="nsew")
    except tk.TclError:
        pass
    s.layout("TEntry", [("VField.field", {"sticky": "nswe", "children": [
        ("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    s.layout("TCombobox", [("VField.field", {"sticky": "nswe", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
            ("Combobox.textarea", {"sticky": "nswe"})]})]})])
    s.layout("TSpinbox", [("VField.field", {"sticky": "nswe", "children": [
        ("null", {"side": "right", "sticky": "", "children": [
            ("Spinbox.uparrow", {"side": "top", "sticky": "e"}),
            ("Spinbox.downarrow", {"side": "bottom", "sticky": "e"})]}),
        ("Spinbox.padding", {"sticky": "nswe", "children": [("Spinbox.textarea", {"sticky": "nswe"})]})]})])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=INPUT, background=INPUT, foreground=TEXT, insertcolor=VIOLET,
                    padding=(10, 6), arrowcolor=VIOLET, arrowsize=12, selectbackground=SEL,
                    selectforeground=TEXT, font=base)
        s.map(w, fieldbackground=[("readonly", INPUT), ("disabled", SURFACE)],
              foreground=[("readonly", TEXT), ("disabled", DIM)],
              background=[("active", INPUT), ("pressed", INPUT)], arrowcolor=[("disabled", DIM)])
    root.option_add("*TCombobox*Listbox.background", ELEVATED)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", base)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # botones redondeados: normal (elevado con borde), principal (degradado), secundario (morado suave)
    imgs = {
        "n": cached("b_n", lambda: rrect_img(60, 36, 11, ELEVATED, BORDER_HI)),
        "h": cached("b_h", lambda: rrect_img(60, 36, 11, "#20202c", "#4a4760")),
        "p": cached("b_p", lambda: rrect_img(60, 36, 11, "#262434", VIOLET)),
        "d": cached("b_d", lambda: rrect_img(60, 36, 11, SURFACE, BORDER)),
        "an": cached("a_n", lambda: rrect_img(120, 40, 12, VIOLET, gradient=True)),
        "ah": cached("a_h", lambda: compose(120, 40, [(lambda x, y: _in_rrect(x, y, 0, 0, 120, 40, 12),
                                                       lambda x, y: _lerp(grad_at(x / 120), (255, 255, 255),
                                                                          0.18))])),
        "ad": cached("a_d", lambda: rrect_img(120, 40, 12, "#2a2833", BORDER_HI)),
        "vn": cached("v_n", lambda: rrect_img(60, 36, 11, "#211b33", "#6b569a")),
        "vh": cached("v_h", lambda: rrect_img(60, 36, 11, "#2b2244", VIOLET)),
    }
    root._ui_imgs = imgs
    for name, n_, h_, p_, d_ in (("VBtn", "n", "h", "p", "d"), ("VAcc", "an", "ah", "ah", "ad"),
                                 ("VSec", "vn", "vh", "p", "d"), ("VGhost", None, "h", "p", None)):
        try:
            ghost = cached("ghost", lambda: rrect_img(60, 36, 11, SURFACE))
            s.element_create(f"{name}.bg", "image", imgs[n_] if n_ else ghost,
                             ("disabled", imgs[d_] if d_ else ghost), ("pressed", imgs[p_]),
                             ("active", imgs[h_]), border=12, sticky="nsew")
        except tk.TclError:
            pass
    lay = lambda el: [(el, {"sticky": "nswe", "children": [("Button.padding", {"sticky": "nswe", "children": [
        ("Button.label", {"sticky": "nswe"})]})]})]
    s.layout("TButton", lay("VBtn.bg"))
    s.layout("Accent.TButton", lay("VAcc.bg"))
    s.layout("Purple.TButton", lay("VSec.bg"))
    s.layout("Ghost.TButton", lay("VGhost.bg"))
    s.configure("TButton", background=SURFACE, foreground=TEXT, padding=(16, 8), font=semi(9), anchor="center")
    s.map("TButton", foreground=[("disabled", DIM)], background=[("active", SURFACE)])
    s.configure("Accent.TButton", foreground="#0b0a10", padding=(24, 10), font=display(12))
    s.map("Accent.TButton", foreground=[("disabled", DIM)])
    s.configure("Purple.TButton", foreground=VIOLET_L, font=semi(9))
    s.configure("Ghost.TButton", foreground=DIM, padding=(12, 8), font=body(9))
    s.map("Ghost.TButton", foreground=[("active", TEXT), ("disabled", BORDER_HI)])

    # tablas
    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=30,
                borderwidth=0, bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE, font=base)
    s.configure("Treeview.Heading", background=SURFACE, foreground=DIM, font=mono(8), borderwidth=0,
                bordercolor=BORDER, lightcolor=SURFACE, darkcolor=BORDER, padding=(6, 8), relief="flat")
    s.map("Treeview.Heading", background=[("active", ELEVATED)], foreground=[("active", TEXT)])
    s.map("Treeview", background=[("selected", SEL)], foreground=[("selected", TEXT)])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    for o in ("Vertical", "Horizontal"):
        s.configure(f"{o}.TScrollbar", background=BORDER_HI, troughcolor=SURFACE, bordercolor=SURFACE,
                    lightcolor=BORDER_HI, darkcolor=BORDER_HI, arrowcolor=DIM, gripcount=0, arrowsize=10)
        s.map(f"{o}.TScrollbar", background=[("active", "#4a4760")])

    # barra de progreso con el degradado
    tr = cached("pb_t", lambda: rrect_img(40, 12, 6, INPUT, BORDER))
    pb = cached("pb_b", lambda: rrect_img(200, 12, 6, VIOLET, gradient=True))
    try:
        s.element_create("VPb.trough", "image", tr, border=6, sticky="nsew")
        s.element_create("VPb.pbar", "image", pb, border=6, sticky="nsew")
    except tk.TclError:
        pass
    s.layout("Horizontal.TProgressbar", [("VPb.trough", {"sticky": "nswe", "children": [
        ("VPb.pbar", {"side": "left", "sticky": "ns"})]})])
    s.configure("Horizontal.TProgressbar", thickness=12, background=VIOLET, troughcolor=INPUT)

    s.configure("TSeparator", background=BORDER)
    s.configure("TCheckbutton", background=SURFACE, foreground=TEXT, indicatorbackground=INPUT,
                indicatorforeground=VIOLET, font=base)
    s.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", VIOLET_L)])
    s.configure("TScale", background=VIOLET, troughcolor=INPUT, bordercolor=BORDER, lightcolor=VIOLET,
                darkcolor=VIOLET, sliderthickness=14)
    return s


# ---------------------------------------------------------------- tarjeta redondeada
_CARDS = []


class Card(ttk.Frame):
    """
    Tarjeta con esquinas redondeadas y borde fino. Se usa como un Frame normal (los hijos van dentro)
    y se coloca con .grid()/.pack() como siempre (se coloca el lienzo que la dibuja).
    glow=True: borde con el degradado de la marca (como la tarjeta del directo en Vortexs Live).
    """

    def __init__(self, parent, pad=18, radius=16, bg=SURFACE, outer=BG, border=BORDER, glow=False,
                 style="Panel.TFrame"):
        self._cv = tk.Canvas(parent, bg=outer, highlightthickness=0, bd=0, width=10, height=10)
        super().__init__(self._cv, style=style)
        self._pad, self._r, self._bg, self._outer, self._border, self._glow = pad, radius, bg, outer, border, glow
        self._win = self._cv.create_window(pad, pad, window=self, anchor="nw")
        self._last = None
        self._cv.bind("<Configure>", self._on_cv)
        self.bind("<Configure>", lambda e: self.sync())
        _CARDS.append(self)

    # colocar = colocar el lienzo
    def grid(self, *a, **k):
        return self._cv.grid(*a, **k)

    grid_configure = grid

    def pack(self, *a, **k):
        return self._cv.pack(*a, **k)

    pack_configure = pack

    def grid_remove(self):
        return self._cv.grid_remove()

    def pack_forget(self):
        return self._cv.pack_forget()

    def destroy(self):
        try:
            _CARDS.remove(self)
        except ValueError:
            pass
        cv = self._cv
        super().destroy()
        cv.destroy()

    def sync(self):
        try:
            rw, rh = self.winfo_reqwidth() + 2 * self._pad, self.winfo_reqheight() + 2 * self._pad
            if int(self._cv.cget("width")) != rw or int(self._cv.cget("height")) != rh:
                self._cv.configure(width=rw, height=rh)
        except tk.TclError:
            pass

    def _on_cv(self, e):
        w, h = e.width, e.height
        self._cv.itemconfigure(self._win, width=max(1, w - 2 * self._pad), height=max(1, h - 2 * self._pad))
        if self._last == (w, h):
            return
        self._last = (w, h)
        self._draw(w, h)

    def _corner(self, which, color):
        r, bg, outer, bc = self._r, self._bg, self._outer, color

        def mk():
            cx, cy = {"nw": (r, r), "ne": (0, r), "sw": (r, 0), "se": (0, 0)}[which]
            b_, f_ = _rgb(bc), _rgb(bg)
            return compose(r, r, [
                (lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r, lambda x, y: b_),
                (lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= (r - 1) ** 2, lambda x, y: f_)], under=outer)
        return cached(("corner", which, r, bg, outer, bc), mk)

    def _draw(self, w, h):
        cv, r = self._cv, self._r
        cv.delete("bgd")
        if w < 2 * r + 2 or h < 2 * r + 2:
            return
        cv.create_rectangle(r, 0, w - r, h, fill=self._bg, width=0, tags="bgd")
        cv.create_rectangle(0, r, w, h - r, fill=self._bg, width=0, tags="bgd")
        if self._glow:
            # borde en degradado (izquierda morado -> derecha amarillo), suave como en Vortexs Live
            steps = 48
            for i in range(steps):
                x0, x1 = i * w / steps, (i + 1) * w / steps + 1
                col = _hex(_lerp(_rgb(BORDER_HI), grad_at(i / steps), 0.55))
                cv.create_line(max(x0, r), 0, min(x1, w - r), 0, fill=col, tags="bgd")
                cv.create_line(max(x0, r), h - 1, min(x1, w - r), h - 1, fill=col, tags="bgd")
            lc = _hex(_lerp(_rgb(BORDER_HI), grad_at(0), 0.55))
            rc = _hex(_lerp(_rgb(BORDER_HI), grad_at(1), 0.55))
            cv.create_line(0, r, 0, h - r, fill=lc, tags="bgd")
            cv.create_line(w - 1, r, w - 1, h - r, fill=rc, tags="bgd")
            cols = {"nw": lc, "sw": lc, "ne": rc, "se": rc}
        else:
            bc = self._border
            cv.create_line(r, 0, w - r, 0, fill=bc, tags="bgd")
            cv.create_line(r, h - 1, w - r, h - 1, fill=bc, tags="bgd")
            cv.create_line(0, r, 0, h - r, fill=bc, tags="bgd")
            cv.create_line(w - 1, r, w - 1, h - r, fill=bc, tags="bgd")
            cols = {k: bc for k in ("nw", "ne", "sw", "se")}
        for which, x, y in (("nw", 0, 0), ("ne", w - r, 0), ("sw", 0, h - r), ("se", w - r, h - r)):
            cv.create_image(x, y, image=self._corner(which, cols[which]), anchor="nw", tags="bgd")
        cv.tag_lower("bgd")


def keep_cards_in_sync(root):
    """Las tarjetas ajustan su tamaño cuando cambia lo de dentro."""
    for c in list(_CARDS):
        c.sync()
    root.after(400, lambda: keep_cards_in_sync(root))


# ---------------------------------------------------------------- interruptor
class Toggle(tk.Frame):
    """Interruptor como los de Vortexs Live (sustituye a una casilla): Toggle(padre, text=, variable=, command=)."""

    W, H = 38, 22

    def __init__(self, parent, text="", variable=None, command=None, bg=SURFACE, **_kw):
        super().__init__(parent, bg=bg)
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        lead = len(text) - len(text.lstrip(" "))
        self.cv = tk.Canvas(self, width=self.W, height=self.H, bg=bg, highlightthickness=0, bd=0,
                            cursor="hand2")
        self.cv.pack(side="left", padx=(lead * 4, 10), pady=3)
        self.lbl = tk.Label(self, text=text.strip(), bg=bg, fg=TEXT, font=body(10), cursor="hand2",
                            anchor="w", justify="left")
        self.lbl.pack(side="left")
        self._bg = bg
        self._img = self.cv.create_image(0, 0, anchor="nw")
        for w in (self.cv, self.lbl):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self.lbl.configure(fg=VIOLET_L))
            w.bind("<Leave>", lambda e: self.lbl.configure(fg=TEXT))
        try:
            self._tr = self.var.trace_add("write", lambda *a: self._paint())
        except Exception:
            self._tr = None
        self._paint()

    def _imgs(self):
        W, H, bg = self.W, self.H, self._bg
        r = H / 2

        def on():
            return compose(W, H, [
                (lambda x, y: _in_rrect(x, y, 0, 0, W, H, r), lambda x, y: grad_at(x / W)),
                (lambda x, y: (x - (W - r)) ** 2 + (y - r) ** 2 <= (r - 3) ** 2, lambda x, y: (14, 12, 20))],
                under=bg)

        def off():
            return compose(W, H, [
                (lambda x, y: _in_rrect(x, y, 0, 0, W, H, r), lambda x, y: _rgb(BORDER_HI)),
                (lambda x, y: _in_rrect(x, y, 1, 1, W - 1, H - 1, r - 1), lambda x, y: _rgb("#22212c")),
                (lambda x, y: (x - r) ** 2 + (y - r) ** 2 <= (r - 4) ** 2, lambda x, y: _rgb(DIM))], under=bg)
        return cached(("tg_on", bg), on), cached(("tg_off", bg), off)

    def _paint(self):
        try:
            on, off = self._imgs()
            self.cv.itemconfigure(self._img, image=on if self.var.get() else off)
        except tk.TclError:
            pass

    def _click(self, _e=None):
        if str(self.lbl.cget("state")) == "disabled":
            return
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def configure(self, cnf=None, **kw):
        if "text" in kw:
            self.lbl.configure(text=kw.pop("text"))
        if "state" in kw:
            st = kw.pop("state")
            self.lbl.configure(state=st, disabledforeground=DIM)
        if kw or cnf:
            return super().configure(cnf, **kw)

    config = configure


# ---------------------------------------------------------------- pestañas píldora
class Tabs(ttk.Frame):
    """Sustituye a ttk.Notebook: .add(frame, text=), .select(índice o frame). Los frames se crean con
    este objeto como padre, igual que con Notebook."""

    def __init__(self, parent, bg=SURFACE, **_kw):
        super().__init__(parent, style="Panel.TFrame")
        self._bg = bg
        self.bar = tk.Canvas(self, height=40, bg=bg, highlightthickness=0, bd=0)
        self.bar.grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.pages, self.names, self.cur = [], [], None
        self._hover = None

    def add(self, frame, text=""):
        self.pages.append(frame)
        self.names.append(text.strip())
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_remove()
        if self.cur is None:
            self.select(0)
        self._draw()

    def index_of(self, target):
        if isinstance(target, int):
            return target
        for i, p in enumerate(self.pages):
            if str(p) == str(target):
                return i
        return 0

    def select(self, target=None):
        if target is None:
            return str(self.pages[self.cur]) if self.cur is not None else ""
        i = self.index_of(target)
        if self.cur is not None and self.cur < len(self.pages):
            self.pages[self.cur].grid_remove()
        self.cur = i
        self.pages[i].grid()
        self._draw()
        self.event_generate("<<NotebookTabChanged>>")

    def _draw(self):
        cv = self.bar
        cv.delete("all")
        f = semi(10)
        fm = tkfont.Font(root=self, font=f)
        x, pad_x, h, gap = 4, 16, 32, 2
        boxes = []
        for i, n in enumerate(self.names):
            w = fm.measure(n) + 2 * pad_x
            boxes.append((x, w))
            x += w + gap
        total = x + 2
        H = h + 8
        cv.configure(width=total, height=H)
        track = cached(("tabtrack", total, H, self._bg),
                       lambda: rrect_img(total, H, H // 2, "#14141b", BORDER, under=self._bg))
        cv.create_image(0, 0, image=track, anchor="nw")
        for i, ((bx, bw), n) in enumerate(zip(boxes, self.names)):
            sel = i == self.cur
            if sel:
                pill = cached(("tabpill", bw, h), lambda bw=bw: rrect_img(bw, h, h // 2, SEL, VIOLET, under="#14141b"))
                cv.create_image(bx, 4, image=pill, anchor="nw")
            col = TEXT if sel else (VIOLET_L if self._hover == i else MUTED)
            t = cv.create_text(bx + bw / 2, 4 + h / 2, text=n, fill=col, font=f)
            hit = cv.create_rectangle(bx, 4, bx + bw, 4 + h, outline="", fill="")
            for item in (t, hit):
                cv.tag_bind(item, "<Button-1>", lambda e, i=i: self.select(i))
                cv.tag_bind(item, "<Enter>", lambda e, i=i: self._set_hover(i))
                cv.tag_bind(item, "<Leave>", lambda e: self._set_hover(None))
        cv.configure(cursor="hand2")

    def _set_hover(self, i):
        if self._hover != i:
            self._hover = i
            self._draw()


# ---------------------------------------------------------------- piezas sueltas
def status_pill(parent, bg=BG):
    """Pastilla de estado de la cabecera (● LISTO / ● TRABAJANDO)."""
    cv = tk.Canvas(parent, width=150, height=34, bg=bg, highlightthickness=0, bd=0)

    def set_state(text, color):
        cv.delete("all")
        f = mono(8)
        fm = tkfont.Font(root=cv, font=f)
        w = fm.measure(text) + 44
        img = cached(("pill", w, bg), lambda: rrect_img(w, 30, 15, "#121218", BORDER_HI, under=bg))
        cv.configure(width=w + 2)
        cv.create_image(1, 2, image=img, anchor="nw")
        cv.create_oval(14, 13, 22, 21, fill=color, outline="")
        cv.create_text(30, 17, text=text, fill=MUTED, font=f, anchor="w")
    cv.set_state = set_state
    set_state("LISTO", GREEN)
    return cv


def logo_image(root, size=44):
    try:
        img = tk.PhotoImage(master=root, file=os.path.join(res_dir(), "logo.png"))
        f = max(1, img.width() // size)
        return img.subsample(f, f) if f > 1 else img
    except Exception:
        return None
