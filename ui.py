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
    # los textos normales siguen en Segoe UI: Plus Jakarta Sans se ve apelotonada con el dibujo de Tk
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
    Rápida: solo se supermuestrean los píxeles en los que cambia la capa de arriba.
    """
    img = tk.PhotoImage(width=w, height=h)
    und = _rgb(under)
    offs = [(i + 0.5) / ss for i in range(ss)]
    n = ss * ss

    def top(px, py):
        k = -1
        for i, (inside, _c) in enumerate(layers):
            if inside(px, py):
                k = i
        return k
    grid = [[top(x, y) for x in range(w + 1)] for y in range(h + 1)]
    clear = []
    for y in range(h):
        row = []
        g0, g1 = grid[y], grid[y + 1]
        for x in range(w):
            k = g0[x]
            if k == g0[x + 1] == g1[x] == g1[x + 1]:
                if k < 0:
                    row.append(_hex(und))
                    clear.append((x, y))
                else:
                    row.append(_hex(layers[k][1](x + 0.5, y + 0.5)))
                continue
            acc = [0.0, 0.0, 0.0]
            cov = 0
            for oy in offs:
                for ox in offs:
                    px, py = x + ox, y + oy
                    kk = top(px, py)
                    if kk >= 0:
                        col = layers[kk][1](px, py)
                        cov += 1
                        acc[0] += col[0]
                        acc[1] += col[1]
                        acc[2] += col[2]
            if cov == 0:
                row.append(_hex(und))
                clear.append((x, y))
                continue
            c = (acc[0] / cov, acc[1] / cov, acc[2] / cov)
            row.append(_hex(_lerp(und, c, cov / n)))
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
    fn = cached("field_n", lambda: rrect_img(30, 28, 8, INPUT, BORDER))
    ff = cached("field_f", lambda: rrect_img(30, 28, 8, INPUT, VIOLET))
    fh = cached("field_h", lambda: rrect_img(30, 28, 8, INPUT, BORDER_HI))
    fd = cached("field_d", lambda: rrect_img(30, 28, 8, SURFACE, BORDER))
    try:
        s.element_create("VField.field", "image", fn, ("disabled", fd), ("focus", ff), ("hover", fh),
                         border=9, padding=(8, 3), sticky="nsew")
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
                    padding=(2, 1), arrowcolor=VIOLET, arrowsize=12, selectbackground=SEL,
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
        "n": cached("b_n", lambda: rrect_img(30, 30, 9, ELEVATED, BORDER_HI)),
        "h": cached("b_h", lambda: rrect_img(30, 30, 9, "#20202c", "#4a4760")),
        "p": cached("b_p", lambda: rrect_img(30, 30, 9, "#262434", VIOLET)),
        "d": cached("b_d", lambda: rrect_img(30, 30, 9, SURFACE, BORDER)),
        "vn": cached("v_n", lambda: rrect_img(30, 30, 9, "#211b33", "#6b569a")),
        "vh": cached("v_h", lambda: rrect_img(30, 30, 9, "#2b2244", VIOLET)),
    }
    root._ui_imgs = imgs
    for name, n_, h_, p_, d_ in (("VBtn", "n", "h", "p", "d"),
                                 ("VSec", "vn", "vh", "p", "d"), ("VGhost", None, "h", "p", None)):
        try:
            ghost = cached("ghost", lambda: rrect_img(30, 30, 9, SURFACE))
            s.element_create(f"{name}.bg", "image", imgs[n_] if n_ else ghost,
                             ("disabled", imgs[d_] if d_ else ghost), ("pressed", imgs[p_]),
                             ("active", imgs[h_]), border=10, padding=(12, 3), sticky="nsew")
        except tk.TclError:
            pass
    lay = lambda el: [(el, {"sticky": "nswe", "children": [("Button.padding", {"sticky": "nswe", "children": [
        ("Button.label", {"sticky": "nswe"})]})]})]
    s.layout("TButton", lay("VBtn.bg"))
    s.layout("Purple.TButton", lay("VSec.bg"))
    s.layout("Ghost.TButton", lay("VGhost.bg"))
    s.configure("TButton", background=SURFACE, foreground=TEXT, padding=(4, 1), font=semi(9), anchor="center")
    s.map("TButton", foreground=[("disabled", DIM)], background=[("active", SURFACE)])
    s.configure("Purple.TButton", foreground=VIOLET_L, font=semi(9))
    s.configure("Ghost.TButton", foreground=DIM, padding=(2, 1), font=body(9))
    s.map("Ghost.TButton", foreground=[("active", TEXT), ("disabled", BORDER_HI)])

    # tablas
    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=25,
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

    s.configure("TSeparator", background=BORDER)
    s.configure("TCheckbutton", background=SURFACE, foreground=TEXT, indicatorbackground=INPUT,
                indicatorforeground=VIOLET, font=base)
    s.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", VIOLET_L)])
    s.configure("TScale", background=VIOLET, troughcolor=INPUT, bordercolor=BORDER, lightcolor=VIOLET,
                darkcolor=VIOLET, sliderthickness=14)
    return s


# ---------------------------------------------------------------- redibujar sin atascos
def later(widget, key, ms, fn):
    """Ejecuta fn dentro de ms milisegundos; si se vuelve a pedir antes, solo cuenta la última vez.
    Con la ventana minimizada no se dibuja nada (se hará al volver)."""
    old = getattr(widget, key, None)
    if old:
        try:
            widget.after_cancel(old)
        except Exception:
            pass

    def run():
        setattr(widget, key, None)
        try:
            if widget.winfo_toplevel().state() == "iconic":
                setattr(widget, key, widget.after(400, run))
                return
        except Exception:
            pass
        try:
            fn()
        except tk.TclError:
            pass
    try:
        setattr(widget, key, widget.after(ms, run))
    except tk.TclError:
        pass


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
        # redibujar el fondo solo cuando la ventana deja de cambiar de tamaño (al arrastrar o minimizar
        # llegan decenas de cambios por segundo y redibujar cada uno atasca la ventana)
        later(self._cv, "_card_draw", 90, lambda: self._draw(*self._last) if self._last else None)

    def _draw_glow(self, w, h):
        self._gimg = _card_glow_image(self, w, h)
        self._cv.create_image(0, 0, image=self._gimg, anchor="nw", tags="bgd")
        self._cv.tag_lower("bgd")

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
        if self._glow:
            try:
                self._draw_glow(w, h)
                return
            except Exception:
                pass
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


def _card_glow_image(card, w, h):
    """Tarjeta destacada de Vortexs Live: brillo violeta arriba a la izquierda, amarillo abajo a la
    derecha (dentro de la tarjeta) y borde con el degradado de la marca."""
    np = _np()
    r = card._r
    img = np.empty((h, w, 3), np.float32)
    img[:] = _rgbf(card._outer)
    body_ = np.empty((h, w, 3), np.float32)
    body_[:] = _rgbf(card._bg)
    body_ = glow_into(body_, -w * 0.3 + w * 0.45, -h * 0.6 + h * 0.55, w * 0.45, h * 0.55 * 1.6, VIOLET, 40 / 255)
    body_ = glow_into(body_, w * 0.55 + w * 0.35, h * 0.35 + h * 0.5, w * 0.35, h * 0.5 * 1.6, YELLOW, 26 / 255)
    outer = _rr_cov(w, h, 0.5, 0.5, w - 0.5, h - 0.5, r)
    inner = _rr_cov(w, h, 1.7, 1.7, w - 1.7, h - 1.7, r - 1.2)
    border = _grad(w, h, 0, w) * 0.55 + _rgbf(card._bg) * 0.45
    img = _over(img, border, outer)
    img = _over(img, body_, inner)
    return _photo(img, card._cv)


def keep_cards_in_sync(root):
    """Las tarjetas ajustan su tamaño cuando cambia lo de dentro."""
    for c in list(_CARDS):
        c.sync()
    root.after(400, lambda: keep_cards_in_sync(root))


# ---------------------------------------------------------------- interruptor
class Toggle(tk.Frame):
    """Interruptor como los de Vortexs Live (sustituye a una casilla): Toggle(padre, text=, variable=, command=)."""

    W, H = 36, 20

    def __init__(self, parent, text="", variable=None, command=None, bg=SURFACE, **_kw):
        super().__init__(parent, bg=bg)
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        lead = len(text) - len(text.lstrip(" "))
        self.cv = tk.Canvas(self, width=self.W, height=self.H, bg=bg, highlightthickness=0, bd=0,
                            cursor="hand2")
        self.cv.pack(side="left", padx=(lead * 4, 10), pady=1)
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
        self.bar.grid(row=0, column=0, sticky="w", pady=(0, 6))
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
        x, pad_x, h, gap = 4, 16, 28, 2
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


# ---------------------------------------------------------------- botón principal en degradado
class GradButton(tk.Canvas):
    """Botón principal (degradado morado -> amarillo) con la misma forma de uso que ttk.Button."""

    def __init__(self, parent, text="", command=None, bg=None, font=None, padx=26, height=42, **_kw):
        try:
            bg = bg or ttk.Style(parent).lookup(parent.cget("style") or "TFrame", "background") or SURFACE
        except Exception:
            bg = bg or SURFACE
        self._text, self._cmd, self._state, self._hover = text, command, "normal", False
        self._font = font or display(12)
        self._bgc, self._h, self._padx = bg, height, padx
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=height, cursor="hand2")
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def _size(self):
        fm = tkfont.Font(root=self, font=self._font)
        return fm.measure(self._text) + 2 * self._padx, self._h

    def _draw(self):
        w, h = self._size()
        self.configure(width=w, height=h)
        self.delete("all")
        dis = self._state == "disabled"
        r = 12

        def mk(kind):
            if kind == "d":
                return rrect_img(w, h, r, "#24232d", BORDER_HI, under=self._bgc)
            mix = 0.16 if kind == "h" else 0.0
            return compose(w, h, [(lambda x, y: _in_rrect(x, y, 0, 0, w, h, r),
                                   lambda x, y: _lerp(grad_at(x / w), (255, 255, 255), mix))], under=self._bgc)
        kind = "d" if dis else ("h" if self._hover else "n")
        self._img = cached(("gb", w, h, kind, self._bgc), lambda: mk(kind))
        self.create_image(0, 0, image=self._img, anchor="nw")
        self.create_text(w / 2, h / 2, text=self._text, font=self._font, fill=DIM if dis else "#0b0a10")
        self.configure(cursor="arrow" if dis else "hand2")

    def _set_hover(self, v):
        if self._hover != v:
            self._hover = v
            if self._state != "disabled":
                self._draw()

    def _click(self, e):
        if self._state == "disabled":
            return
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height() and self._cmd:
            self._cmd()

    def invoke(self):
        if self._state != "disabled" and self._cmd:
            self._cmd()

    def configure(self, cnf=None, **kw):
        redraw = False
        if "state" in kw:
            self._state = str(kw.pop("state"))
            redraw = True
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "command" in kw:
            self._cmd = kw.pop("command")
        kw.pop("style", None)
        r = super().configure(cnf, **kw) if (kw or cnf) else None
        if redraw:
            self._draw()
        return r

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __getitem__(self, key):
        return self.cget(key)


def Button(parent, **kw):
    """ttk.Button, salvo el estilo principal («Accent.TButton»), que es el botón en degradado."""
    if kw.get("style") == "Accent.TButton":
        kw.pop("style")
        return GradButton(parent, **kw)
    return ttk.Button(parent, **kw)


# ---------------------------------------------------------------- barra de progreso en degradado
class GradBar(tk.Canvas):
    """Como ttk.Progressbar (mode, maximum, value, start, stop) pero con el degradado de la marca."""

    H = 12

    def __init__(self, parent, mode="determinate", maximum=1, bg=SURFACE, **_kw):
        super().__init__(parent, height=self.H, bg=bg, highlightthickness=0, bd=0)
        self._mode, self._max, self._val, self._bgc = mode, float(maximum), 0.0, bg
        self._anim, self._phase = None, 0.0
        self.bind("<Configure>", lambda e: later(self, "_bar_build", 60, self._build))

    def _build(self):
        w, h = self.winfo_width(), self.H
        if w < 30:
            return
        self.delete("all")
        r = h // 2
        cap = lambda side, fill, border: cached(("cap", side, fill, border, self._bgc), lambda: compose(
            r + 1, h, [(lambda x, y: (x - (r if side == "l" else 0)) ** 2 + (y - r) ** 2 <= r * r,
                        lambda x, y: _rgb(border or fill)),
                       (lambda x, y: (x - (r if side == "l" else 0)) ** 2 + (y - r) ** 2 <= (r - 1) ** 2,
                        lambda x, y: _rgb(fill))], under=self._bgc))
        # degradado completo (debajo) y una tapa del color del fondo de la barra que se mueve
        step = 3
        for x in range(r, w - r, step):
            self.create_rectangle(x, 0, x + step, h, fill=_hex(grad_at(x / w)), width=0, tags="g")
        self._lc = self.create_image(0, 0, image=cap("l", VIOLET, None), anchor="nw", tags="g")
        self.create_image(w - r - 1, 0, image=cap("r", YELLOW, None), anchor="nw", tags="tail")
        self._cover = self.create_rectangle(r, 0, w, h, fill=INPUT, outline="", tags="cv")
        self._cover2 = self.create_rectangle(r, 0, r, h, fill=INPUT, outline="", tags="cv")
        self.create_line(r, 0, w - r, 0, fill=BORDER, tags="bd")
        self.create_line(r, h - 1, w - r, h - 1, fill=BORDER, tags="bd")
        self._rcap = self.create_image(w - r - 1, 0, image=cap("r", INPUT, BORDER), anchor="nw", tags="bd")
        self._lcap_off = self.create_image(0, 0, image=cap("l", INPUT, BORDER), anchor="nw", tags="bd")
        self._barw = w
        self._update()

    def _update(self):
        if not hasattr(self, "_barw"):
            return
        w, h, r = self._barw, self.H, self.H // 2
        if self._mode == "indeterminate":
            span = w * 0.25
            a = (w + span) * self._phase - span
            b = a + span
            self.coords(self._cover, max(r, b), 0, w, h)
            self.coords(self._cover2, r, 0, max(r, a), h)
            self.itemconfigure(self._lcap_off, state="normal" if a > 0 else "hidden")
            self.itemconfigure(self._rcap, state="normal" if b < w - r else "hidden")
        else:
            f = 0.0 if self._max <= 0 else max(0.0, min(1.0, self._val / self._max))
            x = r + (w - 2 * r) * f
            self.coords(self._cover, x, 0, w, h)
            self.coords(self._cover2, r, 0, r, h)
            self.itemconfigure(self._lcap_off, state="hidden" if f > 0 else "normal")
            self.itemconfigure(self._rcap, state="hidden" if f >= 0.999 else "normal")

    def _tick(self, ms):
        self._phase = (self._phase + 0.012) % 1.0
        self._update()
        self._anim = self.after(ms, self._tick, ms)

    def start(self, ms=12):
        self.stop()
        self._tick(max(10, int(ms)))

    def stop(self):
        if self._anim:
            self.after_cancel(self._anim)
            self._anim = None

    def configure(self, cnf=None, **kw):
        ch = False
        for k in ("mode", "maximum", "value"):
            if k in kw:
                v = kw.pop(k)
                if k == "mode":
                    self._mode = str(v)
                elif k == "maximum":
                    self._max = float(v)
                else:
                    self._val = float(v or 0)
                ch = True
        kw.pop("style", None)
        r = super().configure(cnf, **kw) if (kw or cnf) else None
        if ch:
            self._update()
        return r

    config = configure

    def cget(self, key):
        if key == "mode":
            return self._mode
        if key == "maximum":
            return self._max
        if key == "value":
            return self._val
        return super().cget(key)


# ---------------------------------------------------------------- zona con desplazamiento
_SCROLLS = []


class ScrollArea(ttk.Frame):
    """Contenido que ocupa todo el alto disponible; si la ventana es más baja que lo que necesita,
    aparece una barra para desplazarse (y la rueda del ratón funciona fuera de las tablas)."""

    def __init__(self, parent, bg=BG):
        super().__init__(parent, style="TFrame")
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=self.sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.cv, style="TFrame")
        self._win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self._sb_on = False
        self.cv.bind("<Configure>", lambda e: self.sync())
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=(0, 0, e.width, e.height)))
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        _SCROLLS.append(self)

    def sync(self):
        try:
            cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
            if cw < 2 or ch < 2:
                return
            rh = self.inner.winfo_reqheight()
            h = max(ch, rh)
            self.cv.itemconfigure(self._win, width=cw, height=h)
            self.cv.configure(scrollregion=(0, 0, cw, h))
            need = rh > ch + 2
            if need != self._sb_on:
                self._sb_on = need
                if need:
                    self.sb.pack(side="right", fill="y")
                else:
                    self.sb.pack_forget()
                    self.cv.yview_moveto(0)
        except tk.TclError:
            pass

    def _wheel(self, e):
        if not self._sb_on:
            return
        w = e.widget
        try:
            if str(w).startswith(str(self)) is False:
                return
            if w.winfo_class() in ("Treeview", "Text", "Listbox", "TCombobox", "TSpinbox"):
                return
        except Exception:
            return
        self.cv.yview_scroll(int(-e.delta / 120) * 3, "units")


def keep_scroll_in_sync(root):
    for s in list(_SCROLLS):
        s.sync()
    root.after(400, lambda: keep_scroll_in_sync(root))


# ================================================================ estilo Vortexs Live (animado)
# Un solo reloj para toda la ventana que solo funciona mientras algo se mueve; cada control lleva
# sus transiciones (encima 0,14 s, pulsado 0,08 s, interruptor 0,18 s, pestañas 0,22 s) con la
# misma curva suave que Vortexs Live.
HOVER_SEC, PRESS_SEC = 0.14, 0.08


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def approach(v, target, step):
    return min(target, v + step) if v < target else max(target, v - step)


class _Clock:
    items: set = set()
    root = None
    running = False
    last = 0.0

    @classmethod
    def add(cls, w):
        import time
        cls.items.add(w)
        if not cls.running:
            cls.running = True
            cls.last = time.time()
            try:
                w.after(15, cls.tick)
            except tk.TclError:
                cls.running = False

    @classmethod
    def tick(cls):
        import time
        now = time.time()
        dt = min(0.05, now - cls.last)
        cls.last = now
        any_w = None
        for w in list(cls.items):
            try:
                more = w._animate(dt)
                w._draw()
                any_w = w
            except tk.TclError:
                more = False
            if not more:
                cls.items.discard(w)
        if cls.items and any_w is not None:
            any_w.after(15, cls.tick)
        else:
            cls.running = False


# ---------------------------------------------------------------- dibujo rápido con numpy
def _np():
    import numpy as np
    return np


def _rr_cov(w, h, x0, y0, x1, y1, r):
    """Cobertura (0..1, con borde suave) de un rectángulo redondeado."""
    np = _np()
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    xs += 0.5
    ys += 0.5
    cx = np.clip(xs, x0 + r, x1 - r)
    cy = np.clip(ys, y0 + r, y1 - r)
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) - r
    return np.clip(0.5 - d, 0.0, 1.0)


def _rgbf(c):
    np = _np()
    return np.array(_rgb(c), dtype=np.float32)


def _grad(w, h, x0, x1):
    """Degradado de la marca (morado -> amarillo) en horizontal, como (h, w, 3)."""
    np = _np()
    t = np.clip((np.arange(w, dtype=np.float32) + 0.5 - x0) / max(1.0, x1 - x0), 0, 1)
    a, b = _rgbf(VIOLET), _rgbf(YELLOW)
    row = a[None, :] + (b - a)[None, :] * t[:, None]
    return np.broadcast_to(row[None, :, :], (h, w, 3))


def _photo(img, master=None):
    """(h, w, 3) float -> PhotoImage (PPM en memoria: rápido)."""
    np = _np()
    h, w = img.shape[:2]
    data = np.clip(img + 0.5, 0, 255).astype(np.uint8).tobytes()
    return tk.PhotoImage(master=master, data=f"P6 {w} {h} 255 ".encode() + data, format="PPM")


def _over(base, color, alpha):
    np = _np()
    a = alpha[..., None] if alpha.ndim == 2 else alpha
    return base * (1 - a) + color * a


def _mix(c1, c2, t):
    return _hex(_lerp(_rgb(c1), _rgb(c2), t))


def glow_into(img, cx, cy, rx, ry, color, strength):
    """Brillo difuminado (elipse que se desvanece) sobre img (h, w, 3)."""
    np = _np()
    h, w = img.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xs - cx) / max(1, rx)) ** 2 + ((ys - cy) / max(1, ry)) ** 2)
    a = np.clip(1 - d, 0, 1) ** 1.6 * strength
    return _over(img, _rgbf(color), a)


# ---------------------------------------------------------------- botón
KIND = {None: "ghost", "TButton": "ghost", "Accent.TButton": "primary", "Purple.TButton": "pill",
        "Ghost.TButton": "link", "Danger.TButton": "danger", "Yellow.TButton": "yellow"}


class VButton(tk.Canvas):
    """
    Botón como los de Vortexs Live. kind: primary (degradado de la marca, halo violeta al pasar),
    ghost (gris con borde que se aclara), pill (violeta, para la acción destacada), link (solo texto
    violeta), danger (rojo). Al pasar el ratón cambia con transición; al pulsar se encoge un poco.
    Se usa igual que ttk.Button: configure(text=, state=, command=), cget, invoke.
    """
    HALO = 6

    def __init__(self, parent, text="", command=None, style=None, width=None, kind=None, font=None,
                 compact=False, **_kw):
        try:
            bg = ttk.Style(parent).lookup(parent.cget("style") or "TFrame", "background") or SURFACE
        except Exception:
            try:
                bg = parent.cget("bg")
            except Exception:
                bg = SURFACE
        self._bgc = bg
        self.kind = kind or KIND.get(style, "ghost")
        self._text, self._cmd, self._state = text, command, "normal"
        self._wchars = width
        self._compact = compact
        if font is None and compact:
            font = semi(9)
        if font is None:
            font = display(12) if self.kind in ("primary", "yellow") else (body(9) if self.kind == "link" else semi(9))
        self._font = font
        self._hov = self._prs = 0.0
        self._hover = self._press = False
        self._cache = {}
        m = self.HALO if (self.kind == "primary" and not compact) else 0
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._m = m
        for ev, fn in (("<Enter>", self._enter), ("<Leave>", self._leave),
                       ("<ButtonPress-1>", self._down), ("<ButtonRelease-1>", self._up)):
            self.bind(ev, fn)
        self._size()
        self._draw()

    # tamaño según el texto
    def _size(self):
        fm = tkfont.Font(root=self, font=self._font)
        if self._compact:
            padx, h = (12, 30) if self.kind != "link" else (6, 26)
        elif self.kind == "yellow":
            padx, h = 22, 38
        elif self.kind == "primary":
            padx, h = 26, 42
        elif self.kind == "link":
            padx, h = 6, 28
        else:
            padx, h = 14, 32
        tw = fm.measure(self._text)
        if self._wchars:
            tw = max(tw, fm.measure("0") * int(self._wchars))
        self._bw, self._bh = tw + 2 * padx, h
        super().configure(width=self._bw + 2 * self._m, height=self._bh + 2 * self._m)

    def _face(self, hq, pq, dis):
        """Imagen del botón para un punto de la transición (cacheada)."""
        key = (self._bw, self._bh, self.kind, hq, pq, dis, self._bgc)
        if key in self._cache:
            return self._cache[key]
        np = _np()
        m, w, h = self._m, self._bw, self._bh
        W, H = w + 2 * m, h + 2 * m
        img = np.empty((H, W, 3), np.float32)
        img[:] = _rgbf(self._bgc)
        s = 1.5 * pq
        x0, y0, x1, y1 = m + s + 0.5, m + s + 0.5, m + w - s - 0.5, m + h - s - 0.5
        r = (9.0 if self._compact else 12.0) if self.kind != "pill" else (y1 - y0) / 2
        k = self.kind if not (dis and self.kind in ("danger",)) else "ghost"
        if k == "primary":
            if dis:
                img = _over(img, _rgbf(ELEVATED), _rr_cov(W, H, x0, y0, x1, y1, r))
                inner = _rr_cov(W, H, x0 + 1, y0 + 1, x1 - 1, y1 - 1, r - 1)
                img = _over(img, _rgbf(BORDER), _rr_cov(W, H, x0, y0, x1, y1, r) - inner)
            else:
                for ring in (3, 2, 1):            # halo violeta de tres capas (aparece al pasar)
                    if hq > 0:
                        c = _rr_cov(W, H, x0 - 2 * ring, y0 - 2 * ring, x1 + 2 * ring, y1 + 2 * ring, r + 2 * ring)
                        img = _over(img, _rgbf(VIOLET), c * (18 / 255) * hq)
                img = _over(img, _grad(W, H, x0, x1), _rr_cov(W, H, x0, y0, x1, y1, r))
        elif k == "link":
            pass
        elif k == "yellow":                      # amarillo liso del canal (botón principal)
            if dis:
                fill = _lerp(_rgb(ELEVATED), _rgb(YELLOW), 0.25)
            else:
                fill = _lerp(_rgb(YELLOW), (255, 246, 120), 0.45 * hq)
            img = _over(img, np.array(fill, np.float32), _rr_cov(W, H, x0, y0, x1, y1, r))
        else:
            if k == "danger":
                fill = _lerp(_lerp(_rgb(self._bgc), _rgb(RED), 18 / 255), _lerp(_rgb(self._bgc), _rgb(RED), 40 / 255), hq)
                bord = _lerp(_rgb(self._bgc), _rgb(RED), (150 + 80 * hq) / 255)
            elif k == "pill":
                fill = _lerp(_lerp(_rgb(self._bgc), _rgb(VIOLET), 38 / 255), _lerp(_rgb(self._bgc), _rgb(VIOLET), 60 / 255), hq)
                bord = _lerp(_rgb(self._bgc), _rgb(VIOLET), (200 if not dis else 90) / 255)
            else:
                fill = _lerp(_rgb(ELEVATED), (32, 32, 42), hq)
                bord = _lerp(_rgb(BORDER), _rgb(BORDER_HI), hq)
            outer = _rr_cov(W, H, x0, y0, x1, y1, r)
            bw = 1.2 if k in ("pill", "danger") else 1.0
            inner = _rr_cov(W, H, x0 + bw, y0 + bw, x1 - bw, y1 - bw, max(0.5, r - bw))
            img = _over(img, np.array(bord, np.float32), outer)
            img = _over(img, np.array(fill, np.float32), inner)
        ph = _photo(img, self)
        self._cache[key] = ph
        return ph

    def _text_color(self):
        h = ease(self._hov)
        dis = self._state == "disabled"
        if self.kind == "primary":
            return DIM if dis else "#0b0a10"
        if self.kind == "yellow":
            return "#3a3606" if dis else "#0b0a10"
        if self.kind == "link":
            return DIM if dis else _mix(VIOLET, VIOLET_L, h)
        if self.kind == "pill":
            return _mix(VIOLET_L, BG, 0.4) if dis else VIOLET_L
        if self.kind == "danger":
            return DIM if dis else "#ff7882"
        return DIM if dis else TEXT

    def _draw(self):
        self.delete("all")
        dis = self._state == "disabled"
        hq = round(ease(self._hov) * 6) / 6
        pq = round(ease(self._prs) * 3) / 3
        self._img = self._face(hq, pq, dis)
        self.create_image(0, 0, image=self._img, anchor="nw")
        cx = self._m + self._bw / 2
        if self.kind == "link":
            cx = self._m + self._bw - 6
            self.create_text(cx, self._m + self._bh / 2, text=self._text, font=self._font,
                             fill=self._text_color(), anchor="e")
        else:
            self.create_text(cx, self._m + self._bh / 2 + (0.5 if pq else 0), text=self._text, font=self._font,
                             fill=self._text_color())
        super().configure(cursor="arrow" if dis else "hand2")

    def _animate(self, dt):
        th = 1.0 if (self._hover and self._state != "disabled") else 0.0
        tp = 1.0 if self._press else 0.0
        self._hov = approach(self._hov, th, dt / HOVER_SEC)
        self._prs = approach(self._prs, tp, dt / PRESS_SEC)
        return self._hov != th or self._prs != tp

    def _enter(self, _e):
        self._hover = True
        _Clock.add(self)

    def _leave(self, _e):
        self._hover = self._press = False
        _Clock.add(self)

    def _down(self, _e):
        if self._state != "disabled":
            self._press = True
            _Clock.add(self)

    def _up(self, e):
        was = self._press
        self._press = False
        _Clock.add(self)
        if was and self._state != "disabled" and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            if self._cmd:
                self._cmd()

    def invoke(self):
        if self._state != "disabled" and self._cmd:
            self._cmd()

    def configure(self, cnf=None, **kw):
        redraw = resize = False
        if "state" in kw:
            self._state = str(kw.pop("state"))
            redraw = True
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = resize = True
        if "command" in kw:
            self._cmd = kw.pop("command")
        if "style" in kw:
            st = kw.pop("style")
            self.kind = KIND.get(st, self.kind)
            redraw = True
        kw.pop("width", None)
        r = super().configure(cnf, **kw) if (kw or cnf) else None
        if resize:
            self._size()
        if redraw or resize:
            self._draw()
        return r

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __getitem__(self, key):
        return self.cget(key)


def Button(parent, **kw):          # noqa: F811  (sustituye a la versión anterior)
    """Todos los botones del programa, con el aspecto y las animaciones de Vortexs Live."""
    return VButton(parent, **kw)


# ---------------------------------------------------------------- interruptor animado
class Toggle(tk.Frame):           # noqa: F811
    """Interruptor de Vortexs Live: la bolita se desliza y el degradado aparece al encenderse (0,18 s)."""
    W, H = 38, 22
    SEC = 0.18

    def __init__(self, parent, text="", variable=None, command=None, bg=SURFACE, **_kw):
        super().__init__(parent, bg=bg)
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        self._bg = bg
        lead = len(text) - len(text.lstrip(" "))
        self.cv = tk.Canvas(self, width=self.W, height=self.H, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack(side="left", padx=(lead * 4, 10), pady=1)
        self.lbl = tk.Label(self, text=text.strip(), bg=bg, fg=TEXT, font=semi(9), cursor="hand2",
                            anchor="w", justify="left")
        self.lbl.pack(side="left")
        self._on = 1.0 if self._val() else 0.0
        self._hov = 0.0
        self._hover = False
        self._cache = {}
        for w in (self.cv, self.lbl):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
        try:
            self.var.trace_add("write", lambda *a: _Clock.add(self))
        except Exception:
            pass
        self._draw()

    def _val(self):
        try:
            return bool(self.var.get())
        except Exception:
            return False

    def _set_hover(self, v):
        self._hover = v
        self.lbl.configure(fg=VIOLET_L if v else TEXT)
        _Clock.add(self)

    def _face(self, tq, hq):
        key = (tq, hq, self._bg)
        if key in self._cache:
            return self._cache[key]
        np = _np()
        W, H = self.W, self.H
        img = np.empty((H, W, 3), np.float32)
        img[:] = _rgbf(self._bg)
        t = ease(tq)
        track = _rr_cov(W, H, 0.5, 0.5, W - 0.5, H - 0.5, (H - 1) / 2)
        off = np.array(_lerp((36, 36, 46), (44, 44, 56), hq), np.float32)
        img = _over(img, off, track)
        if t < 1:
            inner = _rr_cov(W, H, 1.5, 1.5, W - 1.5, H - 1.5, (H - 3) / 2)
            img = _over(img, _rgbf(BORDER_HI), (track - inner) * (1 - t))
        if t > 0:
            img = _over(img, _grad(W, H, 0, W), track * t)
        knob = H - 6
        left, right = 3.0, W - knob - 3.0
        kx = left + (right - left) * t
        kc = _rr_cov(W, H, kx, 3, kx + knob, 3 + knob, knob / 2)
        img = _over(img, np.array(_lerp(_rgb(MUTED), (14, 14, 18), t), np.float32), kc)
        ph = _photo(img, self)
        self._cache[key] = ph
        return ph

    def _draw(self):
        tq = round(self._on * 10) / 10
        hq = round(ease(self._hov) * 4) / 4
        self._img = self._face(tq, hq)
        self.cv.delete("all")
        self.cv.create_image(0, 0, image=self._img, anchor="nw")

    def _animate(self, dt):
        target = 1.0 if self._val() else 0.0
        th = 1.0 if self._hover else 0.0
        self._on = approach(self._on, target, dt / self.SEC)
        self._hov = approach(self._hov, th, dt / HOVER_SEC)
        return self._on != target or self._hov != th

    def _click(self, _e=None):
        if str(self.lbl.cget("state")) == "disabled":
            return
        self.var.set(not self._val())
        _Clock.add(self)
        if self.command:
            self.command()

    def configure(self, cnf=None, **kw):
        if "text" in kw:
            self.lbl.configure(text=kw.pop("text"))
        if "state" in kw:
            self.lbl.configure(state=kw.pop("state"), disabledforeground=DIM)
        if kw or cnf:
            return super().configure(cnf, **kw)

    config = configure


# ---------------------------------------------------------------- pestañas con resalte deslizante
class Tabs(ttk.Frame):            # noqa: F811
    """Como en Vortexs Live: barra redondeada, el resalte violeta se desliza de una pestaña a otra
    (0,22 s) y el texto se ilumina según se acerca. Se usa como ttk.Notebook: add(frame, text=), select()."""
    SEC = 0.22

    def __init__(self, parent, bg=SURFACE, **_kw):
        super().__init__(parent, style="Panel.TFrame")
        self._bg = bg
        self.bar = tk.Canvas(self, height=38, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.bar.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.pages, self.names, self.cur = [], [], None
        self._from, self._t, self._hot = 0.0, 1.0, None
        self._cache = {}
        self.bar.bind("<Button-1>", self._click)
        self.bar.bind("<Motion>", self._motion)
        self.bar.bind("<Leave>", lambda e: self._set_hot(None))

    def _cells(self):
        f = semi(10)
        fm = tkfont.Font(root=self, font=f)
        x, out = 4, []
        for n in self.names:
            w = fm.measure(n) + 32
            out.append((x, w))
            x += w
        return out, x + 4

    def add(self, frame, text=""):
        self.pages.append(frame)
        self.names.append(text.strip())
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_remove()
        if self.cur is None:
            self.cur = 0
            frame.grid()
            self._from, self._t = 0.0, 1.0
        self._draw()

    def index_of(self, target):
        if isinstance(target, int):
            return target
        for i, p in enumerate(self.pages):
            if str(p) == str(target):
                return i
        return 0

    def _pos(self):
        return self._from + ((self.cur or 0) - self._from) * ease(self._t)

    def select(self, target=None):
        if target is None:
            return str(self.pages[self.cur]) if self.cur is not None else ""
        i = self.index_of(target)
        if self.cur is not None and i != self.cur:
            self._from = self._pos()
            self._t = 0.0
            self.pages[self.cur].grid_remove()
        self.cur = i
        self.pages[i].grid()
        _Clock.add(self)
        self._draw()
        self.event_generate("<<NotebookTabChanged>>")

    def _at(self, x):
        cells, _ = self._cells()
        for i, (cx, cw) in enumerate(cells):
            if x < cx + cw:
                return i
        return len(cells) - 1

    def _click(self, e):
        if self.names:
            self.select(self._at(e.x))

    def _motion(self, e):
        self._set_hot(self._at(e.x))

    def _set_hot(self, i):
        if i != self._hot:
            self._hot = i
            self._draw()

    def _animate(self, dt):
        self._t = approach(self._t, 1.0, dt / self.SEC)
        return self._t < 1.0

    def _track(self, total, H):
        key = ("track", total, H, self._bg)
        if key not in self._cache:
            np = _np()
            img = np.empty((H, total, 3), np.float32)
            img[:] = _rgbf(self._bg)
            outer = _rr_cov(total, H, 0.5, 0.5, total - 0.5, H - 0.5, (H - 1) / 2)
            inner = _rr_cov(total, H, 1.5, 1.5, total - 1.5, H - 1.5, (H - 3) / 2)
            img = _over(img, _rgbf(BORDER), outer)
            img = _over(img, np.array(_lerp(_rgb(self._bg), _rgb(SURFACE), 0.9), np.float32), inner)
            self._cache[key] = _photo(img, self)
        return self._cache[key]

    def _pill(self, w, h):
        w = max(8, int(w) // 2 * 2)
        key = ("pill", w, h)
        if key not in self._cache:
            np = _np()
            img = np.empty((h, w, 3), np.float32)
            img[:] = _rgbf(SURFACE)
            outer = _rr_cov(w, h, 0.5, 0.5, w - 0.5, h - 0.5, (h - 1) / 2)
            inner = _rr_cov(w, h, 1.5, 1.5, w - 1.5, h - 1.5, (h - 3) / 2)
            img = _over(img, np.array(_lerp(_rgb(SURFACE), _rgb(VIOLET), 120 / 255), np.float32), outer)
            img = _over(img, np.array(_lerp(_rgb(SURFACE), _rgb(VIOLET), 34 / 255), np.float32), inner)
            self._cache[key] = _photo(img, self)
        return self._cache[key]

    def _draw(self):
        cv = self.bar
        cv.delete("all")
        if not self.names:
            return
        cells, total = self._cells()
        H = 38
        cv.configure(width=total)
        cv.create_image(0, 0, image=self._track(total, H), anchor="nw")
        pos = self._pos()
        a = max(0, min(len(cells) - 1, int(pos)))
        b = min(len(cells) - 1, a + 1)
        f = pos - a
        px = cells[a][0] + (cells[b][0] - cells[a][0]) * f
        pw = cells[a][1] + (cells[b][1] - cells[a][1]) * f
        self._pimg = self._pill(pw, H - 8)
        cv.create_image(px, 4, image=self._pimg, anchor="nw")
        fnt = semi(10)
        for i, ((cx, cw), n) in enumerate(zip(cells, self.names)):
            near = max(0.0, 1 - abs(pos - i))
            base = TEXT if i == self._hot else MUTED
            cv.create_text(cx + cw / 2, H / 2, text=n, font=fnt, fill=_mix(base, VIOLET_PALE, near))


VIOLET_PALE = "#e3d5ff"


# ---------------------------------------------------------------- cabecera con brillos
class GlowHeader(tk.Canvas):
    """Fondo de la cabecera con los brillos difuminados de Vortexs Live (violeta a la izquierda,
    amarillo a la derecha). Lo de dentro se coloca encima con .inner (un Frame del color del fondo
    no sirve: los widgets de Tk son opacos), así que el logo y los textos se dibujan en el lienzo."""

    def __init__(self, parent, height=78):
        super().__init__(parent, height=height, bg=BG, highlightthickness=0, bd=0)
        self._hh = height
        self._last = None
        self.bind("<Configure>", lambda e: later(self, "_hdr", 90, lambda w=e.width: self._redraw(w)))
        self.items = []

    def _redraw(self, w):
        if w < 50 or self._last == w:
            return
        self._last = w
        np = _np()
        h = self._hh
        img = np.empty((h, w, 3), np.float32)
        img[:] = _rgbf(BG)
        img = glow_into(img, w * 0.12, -h * 0.4, w * 0.45, h * 2.0, VIOLET, 48 / 255)
        img = glow_into(img, w * 0.85, -h * 0.2, w * 0.40, h * 1.8, YELLOW, 26 / 255)
        self._img = _photo(img, self)
        self.delete("bgimg")
        self.create_image(0, 0, image=self._img, anchor="nw", tags="bgimg")
        self.tag_lower("bgimg")
        # línea fina de abajo con el degradado de la marca, muy suave
        self.delete("line")
        steps = 60
        for i in range(steps):
            c = _hex(_lerp(_rgb(BG), grad_at(i / steps), 0.35))
            self.create_line(w * i / steps, h - 1, w * (i + 1) / steps + 1, h - 1, fill=c, tags="line")
        for fn in self.items:
            fn(w)
