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
KF_X, KF_Y, KF_W, KF_H = 0.685, 0.035, 0.31, 0.22
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
    cands = [s_[0] for s_ in _segs(t_cols) if s_[1] - s_[0] >= 0.6 * lh]
    if not cands:
        return None
    c = core_.astype(np.int16)
    pure = (c[..., 0] > 200) & (c[..., 1] > 200) & (c[..., 2] < 80)
    for xt in cands:
        r = _marker_at(core_, pure, yel, xt, lh)
        if r is not None:
            return r
    return None


def _marker_at(core_, pure, yel, xt, lh):
    import numpy as np
    # tu marcador es un bloque de amarillo PURO (≈240,244,9) casi de arriba abajo; en las líneas de
    # compañeros ahí hay su retrato o fondo, nunca ese amarillo (medido: 13–16 columnas frente a 0)
    w0, w1 = max(0, int(xt - 5.4 * lh)), max(0, int(xt - 3.4 * lh))
    if w1 <= w0:
        return None
    full = pure[:, w0:w1].mean(axis=0) >= 0.5
    if int(full.sum()) < 0.3 * lh:
        return None
    for a, b in _segs(yel.mean(axis=0) > 0.15, 2):
        if 4.4 * lh <= xt - b <= 5.3 * lh and (b - a) <= 1.3 * lh:
            return a
    return w0 + int(np.flatnonzero(full)[0])


def lines(img, src_h: int = 1080):
    """Líneas del killfeed en la imagen: [(y0, y1, x_marcador_propio o None)]. Se buscan por sus
    colores (franja verde azulada / roja), que no dependen del fondo del juego."""
    teal, red, yel = _masks(img)
    h, w = teal.shape
    lh = LINE_H_1080 * src_h / 1080.0
    bar = (teal | red)[:, int(w * 0.30):int(w * 0.98)]
    on = bar.mean(axis=1) >= 0.22
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


def find_new_lines(before, after, src_h: int = 1080, n: int = 1) -> list:
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
        if any(same_line(before, b0, b1, after, y0, y1) for b0, b1, _bmx in olds):
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
            key = (c.group, k.t_match)
            if key in seen or not (c.vod_start <= tv <= c.vod_end):
                continue
            seen.add(key)
            by_group.setdefault(c.group, []).append((tv, t0 + (tv - c.vod_start)))
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
    out, log = [], list(pre_log)
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
        for cl in clusters:
            # se mira el directo 10 veces por segundo alrededor de la(s) kill(s): la línea sale en el
            # instante exacto en que entra en el killfeed (no en la hora de Riot, que puede ir desfasada)
            t0 = cl[0][0] - 1.2
            seq = grab_seq(vod, t0, (cl[-1][0] + 1.0) - t0, SEQ_FPS)
            if len(seq) < 4:
                log.append(f"{core.fmt_ts(cl[0][0])}  ronda {g[1] + 1}: no se pudo leer el vídeo")
                continue
            # la imagen «después»: el primer momento en que tus líneas nuevas están enteras (y estables);
            # no un fotograma fijo, que puede caer cuando el killfeed está tapado (destello, muerte…)
            before = seq[0]
            best_i, boxes = None, []
            for i in range(1, len(seq)):
                got = find_new_lines(before, seq[i], H, len(cl))
                if len(got) > len(boxes) or (best_i is not None and len(got) == len(boxes) == len(cl)
                                             and i <= best_i + 2):
                    if len(got) > len(boxes):
                        best_i, boxes = i, got
                if len(boxes) == len(cl) and i >= best_i + 2:
                    break
            if not boxes:
                log.append(f"{core.fmt_ts(cl[0][0])}  ronda {g[1] + 1}: NO se ve tu línea en el killfeed "
                           f"({len(cl)} kill(s))")
                continue
            # se recorta un par de fotogramas después de que entre (ya con su amarillo pleno)
            src_i = min(len(seq) - 1, best_i + 2)
            if len(find_new_lines(before, seq[src_i], H, len(cl))) == len(boxes):
                boxes = find_new_lines(before, seq[src_i], H, len(cl))
            else:
                src_i = best_i
            src = seq[src_i]
            pairs = list(zip(cl[len(cl) - len(boxes):], boxes))
            for (tv, tout), box in pairs:
                x0, y0, x1, y1, _a = box
                appear = None
                for i in range(1, src_i + 1):
                    # primer fotograma en que esta línea ya se reconoce como TUYA y nueva, en su sitio
                    if any(abs(b[1] - y0) <= 4 for b in find_new_lines(before, seq[i], H, len(cl))):
                        appear = t0 + i / SEQ_FPS - 0.1      # el amarillo llega ~0,1 s después de la línea
                        break
                if appear is None:
                    appear = t0 + best_i / SEQ_FPS
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
