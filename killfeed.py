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
    teal = (g > 140) & (b > 110) & (r < 130) & (g > r + 50)
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
    on = bar.mean(axis=1) >= 0.15
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


def same_line(img_a, a0, a1, img_b, b0, b1, tol: float = 28.0) -> bool:
    """¿Es la misma línea del killfeed (aunque esté a otra altura)? Se compara su parte derecha."""
    import numpy as np
    h = min(a1 - a0, b1 - b0)
    if h < 8:
        return False
    w = img_a.shape[1]
    xa = int(w * 0.45)
    pa = img_a[a0:a0 + h, xa:int(w * 0.97)].astype(np.int16)
    pb = img_b[b0:b0 + h, xa:int(w * 0.97)].astype(np.int16)
    return float(np.abs(pa - pb).mean()) < tol


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


def locate(seq, t0, cl, H, known=(), fps=SEQ_FPS):
    """
    En los fotogramas seq (desde t0, fps por segundo): las líneas de las kills cl [(t_vod, t_vídeo)].
    Devuelve ([((t_vod, t_vídeo), caja, momento en que entra)], fotograma del que se recortan).
    known: tus líneas de la ronda ya colocadas, que no cuentan como nuevas.
    """
    n = len(cl)
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
            if any(same_line(seq[i], b[1], b[3], seq[src_i], box[1], box[3]) for b in new(i)):
                appear = t0 + i / fps - 0.1      # el amarillo llega ~0,1 s después de la línea
                break
        if appear is None:
            appear = t0 + best_i / fps
        found.append(((tv, tout), box, appear))
    return found, seq[src_i]


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
    seen = set()
    for c, t0 in spans:
        if c.base is None:
            continue
        m, r = c.match, c.round
        for k in m.kills:
            if k.round != r.index or k.killer != m.my_puuid:
                continue
            tv = c.base + k.t_match / 1000.0
            key = (c.group, k.t_match, k.victim)     # con la ulti puedes matar a dos en el mismo instante
            if key in seen or not (c.vod_start <= tv <= c.vod_end):
                continue
            seen.add(key)
            by_group.setdefault(c.group, []).append((tv, t0 + (tv - c.vod_start)))
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
                fuera.append(f"{core.fmt_ts(c.base + k.t_match / 1000.0)}  ronda {r.index + 1}: "
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
        for cl in clusters:
            # se mira el directo 10 veces por segundo alrededor de la(s) kill(s): la línea sale en el
            # instante exacto en que entra en el killfeed (no en la hora de Riot, que puede ir desfasada)
            # desde 2,5 s antes: según el directo la línea sale hasta ~2 s ANTES de la hora de Riot
            t0 = cl[0][0] - 2.5
            seq = grab_seq(vod, t0, (cl[-1][0] + 1.0) - t0, SEQ_FPS)
            if len(seq) < 4:
                log.append(f"{core.fmt_ts(cl[0][0])}  ronda {g[1] + 1}: no se pudo leer el vídeo")
                continue
            found, src = locate(seq, t0, cl, H, known, SEQ_FPS)
            if not found:
                log.append(f"{core.fmt_ts(cl[0][0])}  ronda {g[1] + 1}: NO se ve tu línea en el killfeed "
                           f"({len(cl)} kill(s))")
                continue
            for (tv, tout), box, appear in found:
                known.append((src, box[1], box[3]))
                png = os.path.join(folder, f"kf_{len(out):03d}.png")
                ww, hh = save_rgba(src, box, png)
                sc = OUT_LINE_H / max(1, hh)
                w2 = int(round(ww * sc / 2)) * 2
                h2 = int(round(hh * sc / 2)) * 2
                start = max(0.0, tout + (appear - tv) - 0.03)
                out.append({"png": png, "w": w2, "h": h2, "x": 1080 - w2,
                            "y": int(y_top + slot * (OUT_LINE_H + GAP)),
                            "start": start, "end": gend[g]})
                log.append(f"{core.fmt_ts(tv)}  ronda {g[1] + 1}: línea {slot + 1}, entra a {core.fmt_ts(appear)}")
                slot += 1
    for p in [os.path.join(folder, "registro_killfeed.txt")] + ([log_path] if log_path else []):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("Killfeed de la vertical: una línea por cada kill tuya (segundo del VOD)\n\n")
                f.write("\n".join(log) + "\n")
        except OSError:
            pass
    return out
