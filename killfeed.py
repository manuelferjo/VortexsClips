"""
Killfeed en la vertical: en cada kill TUYA se recorta del directo la línea exacta que aparece en el
killfeed (con su arma, headshot, wallbang, habilidad, agentes y nombres: es la imagen real) y se queda
fija; las siguientes de la misma ronda se van apilando debajo, como en los edits.

Cómo encuentra la línea: compara el killfeed justo antes y justo después de la kill (Riot da el
segundo exacto). Las líneas nuevas entran por ABAJO y todas van alineadas a la derecha, así que la
línea nueva es la franja de abajo que ha cambiado; se recorta a su largo real (cada una mide distinto)
y con fondo transparente fuera de la línea (los extremos inclinados quedan limpios).
"""
import os
import subprocess

import core

# zona del killfeed en la imagen del directo (fracciones del ancho / alto)
KF_X, KF_Y, KF_W, KF_H = 0.685, 0.035, 0.31, 0.27
LINE_H_1080 = 34          # alto de una línea a 1080p


def grab_region(vod: str, t: float):
    """Fotograma del killfeed a tamaño real (numpy H x W x 3) en el segundo t del VOD."""
    import numpy as np
    ffmpeg = core.find_tool("ffmpeg")
    f, lt = core.vod_resolve(vod, t)
    info = core.probe_vod(vod) if not hasattr(grab_region, "_dims") or grab_region._dims[0] != vod \
        else {"width": grab_region._dims[1], "height": grab_region._dims[2]}
    W, H = int(info.get("width") or 1920), int(info.get("height") or 1080)
    grab_region._dims = (vod, W, H)
    cw, ch = int(W * KF_W) // 2 * 2, int(H * KF_H) // 2 * 2
    cx, cy = int(W * KF_X), int(H * KF_Y)
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, lt):.3f}", "-i", f,
                        "-frames:v", "1", "-vf", f"crop={cw}:{ch}:{cx}:{cy}", "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "pipe:1"], capture_output=True, **core._no_window())
    if len(r.stdout) < cw * ch * 3:
        return None
    return np.frombuffer(r.stdout[: cw * ch * 3], dtype=np.uint8).reshape(ch, cw, 3)


def _masks(img):
    import numpy as np
    a = img.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    teal = (g > 140) & (b > 110) & (r < 130) & (g > r + 50) & (b < g + 10)   # (no el azul cielo de Abyss)
    red = (r > 190) & (g < 120) & (b < 130)
    yel = (r > 190) & (g > 170) & (b < 110)
    return teal, red, yel


def _segs(m, min_w=1):
    out, x, n = [], 0, len(m)
    while x < n:
        if m[x]:
            a = x
            while x < n and m[x]:
                x += 1
            if x - a >= min_w:
                out.append((a, x))
        x += 1
    return out


def own_marker(band, src_h: int = 1080):
    """
    Toda línea empieza con un marcador de color justo antes del retrato de quien mata: amarillo en
    las TUYAS («Me»), verde en las de compañeros, rojo en las de rivales. Está a ~4,9 líneas de alto a
    la izquierda de donde empieza la franja verde del nombre. Devuelve (x_inicio_marcador) si es tuya.
    """
    import numpy as np
    lh = LINE_H_1080 * src_h / 1080.0
    core_ = band[3:-3] if band.shape[0] > 10 else band
    teal, red, yel = _masks(core_)
    t_cols = teal.mean(axis=0) > 0.5
    # la franja del nombre suele ser la primera zona verde azulada ancha, pero algunos retratos
    # (Sova, Neon, Fade…) tienen azules que también lo parecen y la adelantan: se prueba cada una
    cands = [s_[0] for s_ in _segs(t_cols) if s_[1] - s_[0] >= 0.3 * lh]
    if not cands:
        return None
    c = core_.astype(np.int16)
    pure = (c[..., 0] > 200) & (c[..., 1] > 200) & (c[..., 2] < 80)
    for xt in cands:
        r = _marker_at(core_, pure, yel, xt, lh)
        if r is not None:
            # en tus kills el que muere es un rival: la línea ACABA en rojo. Si acaba en verde, la víctima
            # eres tú («X → Me») y el amarillo era el de tu retrato o el del retrato del que te mata
            # (se compara dónde está el rojo respecto al verde a la derecha del marcador, no solo el
            # final: la pastilla del delay de tu emisión puede tapar el final de la línea)
            rc = np.flatnonzero(red[:, r:].mean(axis=0) > 0.5)
            tcc = np.flatnonzero(teal[:, r:].mean(axis=0) > 0.5)
            if len(rc) >= 0.4 * lh and len(tcc) >= 0.4 * lh and np.median(rc) < np.median(tcc):
                continue
            return r
    return None


def _marker_at(core_, pure, yel, xt, lh):
    import numpy as np
    # tu marcador es un bloque amarillo casi de arriba abajo justo antes de tu retrato, con un núcleo
    # de amarillo PURO (≈240,244,9). Según el agente el bloque es más ancho y puro (≈11 px de puro) o
    # más estrecho con algo de degradado (Sova: ~18 px amarillos y 3–5 de puro). En las líneas de
    # compañeros ahí hay su retrato o fondo, sin ese bloque. Su distancia a la franja verde también
    # depende del agente: entre ~1,5 y ~5,6 líneas de alto a la izquierda.
    w0, w1 = max(0, int(xt - 6.6 * lh)), max(0, int(xt - 1.5 * lh))
    if w1 <= w0:
        return None
    ycol = yel[:, w0:w1].mean(axis=0) >= 0.6
    pcol = pure[:, w0:w1].mean(axis=0) >= 0.5
    best = None
    for a, b in _segs(ycol):
        if not (0.25 * lh <= b - a <= 1.3 * lh):          # (el de Hush mide ~10 px)
            continue
        if int(pcol[a:b].sum()) < 3:
            continue
        best = (w0 + a, w0 + b)              # el más cercano al nombre
    if best is None:
        best = _chevron(pure, yel, w0, w1)
    if best is None:
        return None
    a = best[0]
    ycols = yel.mean(axis=0) > 0.15
    while a > 0 and ycols[a - 1] and (best[1] - a) < 1.3 * lh:
        a -= 1
    return a


def _chevron(pure, yel, w0, w1):
    """Otra forma del marcador (p. ej. con Sova): una raya vertical de amarillo puro de 2–4 px de
    alto completo con una punta de flecha amarilla a su alrededor (◀|), que cubre parte de la altura."""
    pm = pure.mean(axis=0)
    ym = yel.mean(axis=0)
    n = len(pm)
    best = None
    # la raya: amarillo casi de arriba abajo, con buena parte puro (la compresión del directo lo ensucia)
    for a, b in _segs((ym[w0:w1] >= 0.85) & (pm[w0:w1] >= 0.5)):
        a, b = w0 + a, w0 + b
        if b - a > 5:
            continue
        right = ym[b:min(n, b + 14)]
        left = ym[max(0, a - 8):a]
        if int(((right > 0.1) & (right < 0.75)).sum()) >= 8 and int(((left > 0.03) & (left < 0.8)).sum()) >= 3:
            best = (max(0, a - 8), b + 14)
    return best


def lines(img, src_h: int = 1080):
    """Líneas del killfeed en la imagen: [(y0, y1, x_marcador_propio o None)]. Se buscan por sus
    colores (franja verde azulada / roja), que no dependen del fondo del juego."""
    teal, red, yel = _masks(img)
    h, w = teal.shape
    lh = LINE_H_1080 * src_h / 1080.0
    bar = (teal | red)[:, int(w * 0.30):int(w * 0.98)]
    # fondo del juego del mismo color (pared verde azulada, humo rojo…): esas columnas son de ese color
    # en casi toda la altura, no solo en una línea; no cuentan
    bg = bar.mean(axis=0) > 0.6
    if bg.any() and (~bg).sum() >= bar.shape[1] * 0.3:
        bar = bar[:, ~bg]
    cov = bar.mean(axis=1)
    on = cov >= 0.15
    # dentro de una línea corta (nombre corto, icono de habilidad) algunas filas tienen poco color por
    # el texto blanco: un hueco corto con algo de color entre dos tramos de línea se rellena
    gmax = max(2, int(round(LINE_H_1080 * src_h / 1080.0 * 0.4)))
    y = 0
    while y < h:
        if not on[y] and y > 0 and on[y - 1]:
            y1 = y
            while y1 < h and not on[y1] and cov[y1] >= 0.08:
                y1 += 1
            if y1 < h and on[y1] and y1 - y <= gmax:
                # (solo si todo junto sigue midiendo como una línea: no se juntan dos líneas)
                ya = y
                while ya > 0 and on[ya - 1]:
                    ya -= 1
                yb = y1
                while yb < h and on[yb]:
                    yb += 1
                if yb - ya <= LINE_H_1080 * src_h / 1080.0 * 1.3:
                    on[y:y1] = True
            y = y1
        y += 1
    out, y = [], 0
    top = int(h * 0.20)                 # por encima: marcador y pastilla «Sin delay», nunca líneas
    while y < h:
        if on[y]:
            y0 = y
            while y < h and on[y]:
                y += 1
            n = y - y0
            # una línea de verdad mide ~34 px a 1080p y hay un hueco entre líneas; un fondo de color
            # (pared, destello) da franjas de otro alto
            if y0 >= top and lh * 0.8 <= n <= lh * 1.3:
                out.append((y0, y, own_marker(img[y0:y], src_h)))
            elif lh * 0.45 <= n < lh * 0.8 and y <= top + lh * 1.5:
                # la primera línea con la mitad de arriba tapada por la pastilla «EN DIRECTO · Sin delay»
                # de tu emisión: se ve solo su parte de abajo; se toma con su alto normal
                ya = max(0, y - int(round(lh)))
                out.append((ya, y, own_marker(img[ya:y], src_h)))
        y += 1
    # tus líneas tienen un borde amarillo fino abajo: con un fondo del mismo color que las líneas (pared
    # verde azulada…) las franjas de color no sirven, pero ese borde sí
    import numpy as np
    L = int(round(lh))
    for yy in range(max(top, L), h - 2):
        row = yel[yy]
        if row.sum() < lh * 1.7:
            continue
        idx = np.flatnonzero(row)
        # tramo continuo más largo
        cuts = np.flatnonzero(np.diff(idx) > 2)
        starts = np.r_[idx[0], idx[cuts + 1]]
        ends = np.r_[idx[cuts], idx[-1]]
        k = int(np.argmax(ends - starts))
        xs, xe = int(starts[k]), int(ends[k]) + 1
        if xe - xs < lh * 1.7 or (yy + 1 < h and yel[yy + 1, xs:xe].mean() > 0.5):
            continue                      # (la fila de abajo del borde: se toma la última)
        # fino: ni encima (dentro de la línea) ni debajo es amarillo
        if yel[max(0, yy - L // 2), xs:xe].mean() > 0.3 or (yy + 3 < h and yel[yy + 3, xs:xe].mean() > 0.3):
            continue
        yb = yy + 1
        ya = yb - L
        # y su borde de arriba, una línea más arriba (así no se confunde con el de arriba de otra
        # línea ni con el borde de la pastilla «Sin delay»)
        if max(yel[r, xs:xe].mean() for r in range(max(0, ya - 3), ya + 4)) < 0.25:
            continue
        if any(o[0] < yb - 4 and ya + 4 < o[1] for o in out):   # (ya hay una línea ahí)
            continue
        mx = own_marker(img[ya:yb], src_h)
        out.append((ya, yb, mx if mx is not None else xs))
    out.sort()
    return out


def grab_seq(vod: str, t: float, dur: float, fps: int = 10) -> list:
    """Fotogramas del killfeed a tamaño real entre t y t+dur (fps por segundo), en una sola lectura."""
    import numpy as np
    ffmpeg = core.find_tool("ffmpeg")
    if not hasattr(grab_region, "_dims") or grab_region._dims[0] != vod:
        info = core.probe_vod(vod)
        grab_region._dims = (vod, int(info.get("width") or 1920), int(info.get("height") or 1080))
    _, W, H = grab_region._dims
    cw, ch = int(W * KF_W) // 2 * 2, int(H * KF_H) // 2 * 2
    cx, cy = int(W * KF_X), int(H * KF_Y)
    f, lt = core.vod_resolve(vod, t)
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, lt):.3f}", "-t", f"{dur:.3f}",
                        "-i", f, "-an", "-vf", f"fps={fps},crop={cw}:{ch}:{cx}:{cy}", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, **core._no_window())
    sz = cw * ch * 3
    n = len(r.stdout) // sz
    return [np.frombuffer(r.stdout[i * sz:(i + 1) * sz], dtype=np.uint8).reshape(ch, cw, 3) for i in range(n)]


def _name_mask(img, y0, y1):
    """Texto blanco (nombre de a quién mataste) de la parte roja de una línea, o None."""
    import numpy as np
    w = img.shape[1]
    band = img[max(0, y0 + 2):max(0, y1 - 2)]
    if band.shape[0] < 6:
        return None
    _t, red, _y = _masks(band)
    xa = int(w * 0.30)
    cols = np.flatnonzero(red[:, xa:].mean(axis=0) >= 0.3)
    if not len(cols):
        return None
    x0 = xa + int(cols[0])
    # umbral relativo: cuando la línea se desvanece (va a caducar) o hay un destello, todo cambia de
    # brillo a la vez, pero el texto sigue siendo lo más claro de la parte roja
    mn = band.min(axis=2).astype(np.int16)
    part = mn[:, x0:int(w * 0.97)]
    if part.size == 0:
        return None
    top = float(np.percentile(part, 97))
    thr = max(110.0, top * 0.82)
    return x0, mn > thr


def same_line(img_a, a0, a1, img_b, b0, b1, tol: float = 28.0) -> bool:
    """¿Es la misma línea del killfeed (aunque esté a otra altura)?
    Se compara sobre todo el NOMBRE (texto blanco de la parte roja): no depende del fondo, que se
    transparenta, ni de la pastilla que tapa parte de la línea. Si no hay nombre que comparar, el color."""
    import numpy as np
    h = min(a1 - a0, b1 - b0) - 8
    if h < 8:
        return False
    w = img_a.shape[1]
    na, nb = _name_mask(img_a, a0, a1), _name_mask(img_b, b0, b1)
    if na and nb:
        # la misma línea tiene su parte roja empezando en el mismo sitio (solo se mueve en vertical)
        if abs(na[0] - nb[0]) > 6:
            return False
        x0 = min(na[0], nb[0])
        # hasta el retrato (el retrato tiene brillos blancos que cambian con el fondo)
        xb = int(w * 0.978 - 1.6 * (a1 - a0))
        if xb - x0 < 20:
            xb = int(w * 0.97)
        ma = na[1][:, x0:xb]
        n = min(ma.shape[0], nb[1].shape[0])
        if ma[:n].sum() >= 25 and nb[1][:n, x0:xb].sum() >= 25:   # (hay nombre que comparar)
            best = 0.0
            # solo la mitad de abajo de la línea: la de arriba puede taparla la pastilla «Sin delay»
            r0, r1 = int(n * 0.38), int(n * 0.92)
            nbm = nb[1][r0:r1, x0:xb]
            ma = ma[r0:r1]
            n = r1 - r0
            for dy in range(-3, 4):
                A, B = (ma[0:n - dy], nbm[dy:n]) if dy >= 0 else (ma[-dy:n], nbm[0:n + dy])
                for dx in range(-6, 7):
                    Bs = np.roll(B, dx, axis=1)
                    u = (A | Bs).sum()
                    if u:
                        best = max(best, (A & Bs).sum() / u)
            return best >= 0.55
    # (la banda de una línea puede salir unos píxeles más arriba o más abajo, sobre todo mientras sube
    # en el killfeed al caducar la de encima: se prueba con ese pequeño desplazamiento)
    xa, xb = int(w * 0.45), int(w * 0.97)
    pa = img_a[a0 + 4:a0 + 4 + h, xa:xb].astype(np.int16)
    best = 1e9
    for dy in range(-4, 5):
        y = b0 + 4 + dy
        if y < 0 or y + h > img_b.shape[0]:
            continue
        pb = img_b[y:y + h, xa:xb].astype(np.int16)
        best = min(best, float(np.abs(pa - pb).mean()))
    return best < tol


def find_new_lines(before, after, src_h: int = 1080, n: int = 1, known=()) -> list:
    """
    Las n líneas TUYAS (marcador amarillo) más bajas del killfeed «después» que no estaban ya igual
    «antes», ordenadas de arriba a abajo (= en el orden en que salieron). Cada una: (x0, y0, x1, y1, alpha).
    """
    import numpy as np
    if after is None:
        return []
    h, w = after.shape[:2]
    lh = LINE_H_1080 * src_h / 1080.0
    olds = lines(before, src_h) if before is not None and before.shape == after.shape else []
    news = []
    for y0, y1, mx in reversed(lines(after, src_h)):
        if mx is None:
            continue
        # ¿ya estaba antes (en cualquier altura: cuando caduca una de arriba, todas suben)?
        # (y también las tuyas de esta ronda ya colocadas, aunque entraran después de «antes»)
        if any(same_line(before, b0, b1, after, y0, y1) for b0, b1, _bmx in olds) or \
                any(same_line(img, k0, k1, after, y0, y1) for img, k0, k1 in known):
            break                       # por encima de una que ya estaba, todo es anterior
        news.append((y0, y1, mx))
        if len(news) >= n:
            break
    out = []
    for y0, y1, mx in reversed(news):
        y0, y1 = max(0, y0 - 1), min(h, y1 + 1)
        teal, red, yel = _masks(after[y0:y1])
        x1 = int(round(w * 0.978))
        x0 = max(0, mx - 2)
        # el «III» / «IV» de racha va a la izquierda del marcador, separado por un hueco
        win0 = max(0, int(x0 - lh * 1.8))
        rr = red[:, win0:x0]
        if rr.any():
            cols = np.flatnonzero(rr.sum(axis=0) >= 3)
            if len(cols):
                x0 = win0 + int(cols.min()) - 2
        x0 = max(0, x0)
        # transparencia: desde el marcador (inclinado, por fila) hasta el borde derecho, más el distintivo
        sub_r, sub_y = red[:, x0:x1], yel[:, x0:x1]
        hh, ww = sub_y.shape
        alpha = np.zeros((hh, ww), dtype=np.uint8)
        m0 = mx - x0
        for r in range(hh):
            yy = np.flatnonzero(sub_y[r, max(0, m0 - 2): m0 + int(lh * 1.2)])
            start = max(0, m0 - 2) + (int(yy[0]) if len(yy) else 2)
            alpha[r, start:] = 255
            idx = np.flatnonzero(sub_r[r, : max(0, m0 - 2)])
            if len(idx) >= 2:
                alpha[r, idx[0]: idx[-1] + 1] = 255
        out.append((x0, y0, x1, y1, alpha))
    return out


def find_new_line(before, after, src_h: int = 1080):
    r = find_new_lines(before, after, src_h, 1)
    return r[0] if r else None


def save_rgba(after, box, path: str) -> tuple[int, int]:
    """Guarda la línea recortada como PNG con transparencia. Devuelve (ancho, alto)."""
    import numpy as np
    x0, y0, x1, y1, alpha = box
    rgb = after[y0:y1, x0:x1]
    rgba = np.dstack([rgb, alpha]).astype(np.uint8)
    hh, ww = rgba.shape[:2]
    ffmpeg = core.find_tool("ffmpeg")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                        "-s", f"{ww}x{hh}", "-i", "pipe:0", "-frames:v", "1", path],
                       input=rgba.tobytes(), capture_output=True, **core._no_window())
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return ww, hh


# ---------------------------------------------------------------- preparar las líneas de un vídeo
OUT_LINE_H = 42          # alto de cada línea en la vertical (1080x1920): como en tus edits
GAP = 10
SEQ_FPS = 10


def locate(seq, t0, cl, H, known=(), fps=SEQ_FPS, after_t=None):
    """
    En los fotogramas seq (desde t0, fps por segundo): las líneas de las kills cl [(t_vod, t_vídeo)].
    Devuelve ([((t_vod, t_vídeo), caja, momento en que entra)], fotograma del que se recortan).
    known: tus líneas de la ronda ya colocadas, que no cuentan como nuevas.
    """
    n = len(cl)
    # after_t: momento en que entró tu kill anterior de la ronda. Con kills muy seguidas, su línea
    # podría verse aún como «nueva» desde t0 (sobre todo si mientras tanto ha subido de sitio);
    # entonces la imagen «antes» se toma justo después de que esa entrara.
    if after_t is not None:
        import math
        k = int(math.ceil((after_t + 0.4 - t0) * fps))
        if 0 < k < len(seq) - 2:
            seq, t0 = seq[k:], t0 + k / fps
    before = seq[0]

    def new(i):
        return find_new_lines(before, seq[i], H, n, known)
    # la imagen «después»: el primer momento en que tus líneas nuevas están enteras (y estables);
    # no un fotograma fijo, que puede caer cuando el killfeed está tapado (destello, muerte…)
    best_i, boxes = None, []
    for i in range(1, len(seq)):
        got = new(i)
        if len(got) > len(boxes):
            best_i, boxes = i, got
        if len(boxes) == n and i >= best_i + 2:
            break
    if not boxes:
        return [], None
    # se recorta un par de fotogramas después de que entre (ya con su amarillo pleno)
    src_i = min(len(seq) - 1, best_i + 2)
    again = new(src_i)
    if len(again) == len(boxes):
        boxes = again
    else:
        src_i = best_i
    found = []
    for (tv, tout), box in zip(cl[n - len(boxes):], boxes):
        appear = None
        for i in range(1, src_i + 1):
            # primer fotograma en que esta línea ya se reconoce como TUYA y nueva, en su sitio
            # (comparando su contenido: con varias seguidas, cada una con la suya)
            # (por su contenido, a cualquier altura y aunque aún no se reconozca su marcador amarillo:
            # al entrar, con su destello, puede salir más abajo y subir cuando caduca la de encima)
            if any(same_line(seq[i], y0, y1, seq[src_i], box[1], box[3])
                   for y0, y1, _m in lines(seq[i], H)):
                appear = t0 + i / fps - 0.1      # el amarillo llega ~0,1 s después de la línea
                break
        if appear is None:
            appear = t0 + best_i / fps
        found.append(((tv, tout), box, appear))
    return found, seq[src_i]


# ---------------------------------------------------------------- a quién mataste (retrato del agente)
# En el killfeed, a la derecha de cada línea sale el retrato del agente al que mataste: el
# «killfeedPortrait» de Riot (valorant-api.com), en espejo y del alto de la línea. Se descarga una vez
# y se guarda; si no se puede descargar, la comprobación simplemente no se hace.
_PORTRAITS: dict | None = None


def _norm_agent(n: str) -> str:
    return "".join(ch for ch in str(n).lower() if ch.isalnum())


def portraits() -> dict:
    """{agente normalizado: imagen RGBA en espejo} (vacío si no hay forma de tenerlas)."""
    global _PORTRAITS
    if _PORTRAITS is not None:
        return _PORTRAITS
    _PORTRAITS = {}
    try:
        import json
        import urllib.request
        from PIL import Image
        d = os.path.join(core.data_dir(), "agentes")
        os.makedirs(d, exist_ok=True)
        idx = os.path.join(d, "agentes.json")

        def get(url, path):
            req = urllib.request.Request(url, headers={"User-Agent": "VortexsClips/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)

        def load_idx():
            with open(idx, encoding="utf-8") as f:
                return {_norm_agent(a["displayName"]): a.get("killfeedPortrait")
                        for a in json.load(f)["data"] if a.get("killfeedPortrait")}
        try:
            items = load_idx()
        except Exception:
            items = {}
        # agentes nuevos: la lista se refresca como mucho una vez al día
        import time
        if not items or time.time() - os.path.getmtime(idx) > 86400:
            try:
                get("https://valorant-api.com/v1/agents?isPlayableCharacter=true", idx + ".tmp")
                os.replace(idx + ".tmp", idx)
                items = load_idx()
            except Exception:
                pass
        for n, url in items.items():
            p = os.path.join(d, n + ".png")
            try:
                if not os.path.isfile(p):
                    get(url, p + ".tmp")
                    os.replace(p + ".tmp", p)
                _PORTRAITS[n] = Image.open(p).convert("RGBA").transpose(Image.FLIP_LEFT_RIGHT)
            except Exception:
                continue
    except Exception:
        _PORTRAITS = {}
    return _PORTRAITS


_PCACHE: dict = {}


def agent_scores(img, box, agents) -> dict:
    """Parecido (0..1) del retrato de la derecha de la línea `box` con cada uno de esos agentes."""
    import numpy as np
    from PIL import Image
    P = portraits()
    x0b, y0b, x1b, y1b = box[:4]
    c = img[y0b:y1b, max(0, x1b - 3 * (y1b - y0b)):x1b].astype(np.float32)
    h, W = c.shape[:2]
    out = {}
    if h < 12:
        return out
    for ag in agents:
        n = _norm_agent(ag)
        im = P.get(n)
        if im is None:
            continue
        best = -1.0
        for sc in (0.94, 1.0, 1.06, 1.12, 1.18):
            hh = int(round(h * sc))
            key = (n, hh)
            a = _PCACHE.get(key)
            if a is None:
                a = _PCACHE[key] = np.asarray(im.resize((2 * hh, hh), Image.LANCZOS)).astype(np.float32)
            ww = a.shape[1]
            for e in range(-16, 13, 2):
                x0 = W + e - ww
                for y0 in range(min(-2, -(hh - h) - 2), 3):
                    xa, xb = max(0, x0), min(W, x0 + ww)
                    ya, yb = max(0, y0), min(h, y0 + hh)
                    if xb - xa < h or yb - ya < h * 0.7:
                        continue
                    t = a[ya - y0:yb - y0, xa - x0:xb - x0]
                    m = t[..., 3] > 200
                    if m.sum() < 150:
                        continue
                    A = c[ya:yb, xa:xb][m]
                    B = t[..., :3][m]
                    A = A - A.mean(0)
                    B = B - B.mean(0)
                    v = float((A * B).sum() / np.sqrt((A * A).sum() * (B * B).sum() + 1e-6))
                    if v > best:
                        best = v
        out[ag] = best
    return out


def victim_check(img, box, want, enemies) -> tuple[str, float, str]:
    """
    ¿La línea es de tu kill a `want` (agente)? Devuelve (veredicto, parecido con want, agente que más se
    parece). veredicto: "ok" (es él), "otro" (claramente es otro enemigo), "?" (no se puede saber).
    """
    if not want or not enemies or not portraits():
        return "?", 0.0, ""
    sc = agent_scores(img, box, set(enemies) | {want})
    if _norm_agent(want) not in {_norm_agent(k) for k in sc}:
        return "?", 0.0, ""
    w = sc.get(want, -1.0)
    best = max(sc, key=sc.get)
    if _norm_agent(best) == _norm_agent(want) or w >= 0.5:
        return ("ok" if w >= 0.45 else "?"), w, best
    if sc[best] >= 0.5 and sc[best] - w >= 0.15:
        return "otro", w, best
    return "?", w, best


def _check_victims(found, src, seq, t0, cl, H, known, last_in, g, victim_of, enemies, placed, log):
    """
    Revisa las líneas encontradas antes de ponerlas:
    1) con varias kills seguidas, cada línea va con la kill cuyo agente sale en su retrato;
    2) si el retrato es claramente de OTRO enemigo, se busca otra vez sin esa línea (y si tampoco, no se pone);
    3) nunca la misma línea dos veces en la ronda (salvo que sea el mismo enemigo, p. ej. revivido).
    """
    import itertools
    if not found:
        return [], src
    want = lambda tv: (victim_of.get((g, round(tv, 3))) or ("", ""))
    # 1) reparto por retrato
    if len(found) > 1 and enemies:
        boxes = [f[1] for f in found]
        kills = [f[0] for f in found]
        S = [[agent_scores(src, b, [want(k[0])[1]]).get(want(k[0])[1], 0.0) if want(k[0])[1] else 0.0
              for k in kills] for b in boxes]
        best = max(itertools.permutations(range(len(kills))),
                   key=lambda p: sum(S[i][p[i]] for i in range(len(boxes))))
        if list(best) != list(range(len(kills))):
            found = [(kills[best[i]], boxes[i], found[i][2]) for i in range(len(boxes))]
    def problem(s_, b_, vic, ag):
        """Por qué no vale esa línea para esta kill (o None si vale)."""
        v, _w, other = victim_check(s_, b_, ag, enemies)
        if v == "otro":
            return f"el retrato es de {other}, no de {ag}"
        for (ps, pb, pv) in placed:
            if pv and vic and pv == vic:        # el mismo enemigo otra vez (revivido): puede repetirse
                continue
            if same_line(ps, pb[1], pb[3], s_, b_[1], b_[3]):
                return "es la misma línea que la de una kill anterior de la ronda"
        return None

    out = []
    for (tv, tout), box, appear in found:
        vic, ag = want(tv)
        cur = (src, box, appear)
        why = problem(src, box, vic, ag)
        if why and len(cl) == 1:
            # se busca otra vez sin contar esa línea
            f2, s2 = locate(seq, t0, cl, H, list(known) + [(src, box[1], box[3])], SEQ_FPS, last_in)
            if f2 and problem(s2, f2[0][1], vic, ag) is None:
                log.append(f"{core.fmt_ts(tv)}  la primera línea que salía no valía ({why}): se usa otra")
                cur, why = (s2, f2[0][1], f2[0][2]), None
        if why:
            log.append(f"{core.fmt_ts(tv)}  no se pone la línea: {why}")
            continue
        # (con la imagen de la que se recorta: si vino de la segunda búsqueda, es otra)
        out.append(((tv, tout), cur[1], cur[2], cur[0]))
    return out, src


LAST_STATS: dict = {}


def prepare(vod: str, clips, cam, folder: str, progress=None, should_stop=None,
            log_path: str | None = None) -> list[dict]:
    """
    Para el vídeo montado con esos clips: las líneas del killfeed de TUS kills, recortadas del directo,
    con su posición y su momento en la vertical. [{png, x, y, w, h, start, end}] (tiempos del vídeo).
    """
    os.makedirs(folder, exist_ok=True)
    info = core.probe_vod(vod)
    W, H = int(info.get("width") or 1920), int(info.get("height") or 1080)
    # dónde cae cada clip en el vídeo montado y hasta dónde dura su ronda
    spans, t = [], 0.0
    gend = {}
    for c in clips:
        d = c.vod_end - c.vod_start
        spans.append((c, t))
        gend[c.group] = t + d
        t += d
    # tus kills, en orden, con su momento en el VOD y en el vídeo
    by_group: dict = {}
    victim_of: dict = {}             # (grupo, segundo) -> (puuid, agente) de a quién mataste
    enemy_agents: dict = {}          # grupo -> agentes del equipo rival
    seen = set()
    for c, t0 in spans:
        if c.base is None:
            continue
        m, r = c.match, c.round
        for k in m.kills:
            if k.round != r.index or k.killer != m.my_puuid:
                continue
            tv = core.kill_vod_time(m, c.base, k.t_match)
            key = (c.group, k.t_match, k.victim)     # con la ulti puedes matar a dos en el mismo instante
            if key in seen or not (c.vod_start <= tv <= c.vod_end):
                continue
            seen.add(key)
            by_group.setdefault(c.group, []).append((tv, t0 + (tv - c.vod_start)))
            ags = getattr(m, "agents", None) or {}
            victim_of[(c.group, round(tv, 3))] = (k.victim, ags.get(k.victim, ""))
            enemy_agents[c.group] = [ags[p] for p in m.enemies if p in ags]
    # kills tuyas de esas rondas que no caen dentro de ningún corte (no pueden salir)
    fuera = []
    rounds_done = set()
    for c, _t0 in spans:
        if c.base is None or c.group in rounds_done:
            continue
        rounds_done.add(c.group)
        m, r = c.match, c.round
        for k in m.kills:
            if k.round == r.index and k.killer == m.my_puuid and (c.group, k.t_match, k.victim) not in seen:
                fuera.append(f"{core.fmt_ts(core.kill_vod_time(m, c.base, k.t_match))}  ronda {r.index + 1}: "
                             f"esta kill cae fuera del corte (no sale en el vídeo)")
    # rondas del vídeo en las que no se encontró ninguna kill tuya (para el registro)
    sin = []
    for c, _t0 in spans:
        if c.group in by_group or c.group in sin:
            continue
        sin.append(c.group)
    pre_log = []
    for g in sin:
        c = next(c for c, _ in spans if c.group == g)
        why = "la partida no está sincronizada" if c.base is None else "no hay kills tuyas dentro del corte"
        pre_log.append(f"{core.fmt_ts(c.vod_start)}  ronda {c.round.index + 1}: sin línea ({why})")
    # justo debajo de la cámara y de los subtítulos (que van en su borde de abajo), pegadas a la derecha
    y_top = core.cam_bottom_y(cam, W, H) + 95 if cam else 700
    out, log = [], list(pre_log) + fuera
    total = sum(len(v) for v in by_group.values())
    missing = []                     # [(segundo del VOD, ronda, foto)]
    pics = os.path.join(os.path.dirname(log_path), "killfeed sin línea") if log_path else None

    def photo(seq_, t0_, tv_, rnd):
        """Foto del killfeed de una kill sin línea (para revisarla o mandarla)."""
        if not pics or not seq_:
            return None
        try:
            from PIL import Image
            i = max(0, min(len(seq_) - 1, int(round((tv_ + 0.6 - t0_) * SEQ_FPS))))
            os.makedirs(pics, exist_ok=True)
            p = os.path.join(pics, f"{core.fmt_ts(tv_).replace(':', '-')} ronda {rnd}.png")
            Image.fromarray(seq_[i]).save(p)
            return p
        except Exception:
            return None
    groups = list(by_group.items())
    for gi, (g, ks) in enumerate(groups):
        if should_stop and should_stop():
            raise RuntimeError("Cancelado.")
        if progress:
            progress(gi / max(1, len(groups)), f"Killfeed: ronda {gi + 1} de {len(groups)}…")
        ks.sort()
        # kills muy seguidas: sus líneas entran casi juntas, se buscan de una vez
        clusters, cur = [], []
        for kv in ks:
            if cur and kv[0] - cur[-1][0] > 0.7:
                clusters.append(cur)
                cur = []
            cur.append(kv)
        if cur:
            clusters.append(cur)
        slot = 0
        known = []                      # tus líneas de esta ronda ya colocadas
        placed = []                     # (fotograma, caja, a quién) de esas líneas
        last_in = None                  # cuándo entró la última de ellas
        for cl in clusters:
            # se mira el directo 10 veces por segundo alrededor de la(s) kill(s): la línea sale en el
            # instante exacto en que entra en el killfeed (no en la hora de Riot, que puede ir desfasada)
            # desde 2,5 s antes: según el directo la línea sale hasta ~2 s ANTES de la hora de Riot
            t0 = cl[0][0] - 2.5
            seq = grab_seq(vod, t0, (cl[-1][0] + 1.0) - t0, SEQ_FPS)
            if len(seq) < 4:
                log.append(f"{core.fmt_ts(cl[0][0])}  ronda {g[1] + 1}: no se pudo leer el vídeo")
                missing += [(tv_, g[1] + 1, None) for tv_, _ in cl]
                continue
            found, src = locate(seq, t0, cl, H, known, SEQ_FPS, last_in)
            found, src = _check_victims(found, src, seq, t0, cl, H, known, last_in, g,
                                        victim_of, enemy_agents.get(g) or [], placed, log)
            got = {round(x[0][0], 3) for x in found}
            for tv_, _ in cl:
                if round(tv_, 3) not in got and len(found) < len(cl):
                    missing.append((tv_, g[1] + 1, photo(seq, t0, tv_, g[1] + 1)))
            if not found:
                log.append(f"{core.fmt_ts(cl[0][0])}  ronda {g[1] + 1}: NO se ve tu línea en el killfeed "
                           f"({len(cl)} kill(s))")
                continue
            for (tv, tout), box, appear, src in found:
                known.append((src, box[1], box[3]))
                placed.append((src, box, (victim_of.get((g, round(tv, 3))) or ("", ""))[0]))
                last_in = appear if last_in is None else max(last_in, appear)
                png = os.path.join(folder, f"kf_{len(out):03d}.png")
                ww, hh = save_rgba(src, box, png)
                sc = OUT_LINE_H / max(1, hh)
                w2 = int(round(ww * sc / 2)) * 2
                h2 = int(round(hh * sc / 2)) * 2
                start = max(0.0, tout + (appear - tv) - 0.03)
                out.append({"png": png, "w": w2, "h": h2, "x": 1080 - w2,
                            "y": int(y_top + slot * (OUT_LINE_H + GAP)),
                            "start": start, "end": gend[g]})
                who = (victim_of.get((g, round(tv, 3))) or ("", ""))[1]
                log.append(f"{core.fmt_ts(tv)}  ronda {g[1] + 1}: línea {slot + 1}"
                           f"{f' ({who})' if who else ''}, entra a {core.fmt_ts(appear)}")
                slot += 1
    global LAST_STATS
    LAST_STATS = {"total": total, "ok": len(out), "missing": missing, "pics": pics if missing else None}
    for p in [os.path.join(folder, "registro_killfeed.txt")] + ([log_path] if log_path else []):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("Killfeed de la vertical: una línea por cada kill tuya (segundo del VOD)\n")
                f.write(f"Resumen: {len(out)} de {total} kills con su línea"
                        + (f" · faltan {len(missing)} (fotos en «killfeed sin línea»)" if missing else "") + "\n\n")
                f.write("\n".join(log) + "\n")
        except OSError:
            pass
    return out
