"""
Sincronización automática de una partida con el VOD (versión 2).

Riot da el segundo exacto de TODAS las kills de la partida. En el VOD cada kill se ve:
  - en el killfeed (una línea nueva arriba a la derecha, que empuja a las demás),
  - y, si la kill es tuya, en el aviso de kill que sale abajo en el centro.
No sabemos dónde cae eso en TU emisión (cámara, overlays, escala…), así que se mira la imagen
ENTERA reducida a una rejilla de 64x36 celdas: se mide cuánto cambia cada celda fotograma a
fotograma y se busca el desplazamiento en el que los cambios de algunas celdas coinciden con el
patrón de kills. Las celdas que siguen el patrón son el killfeed/aviso; la cámara, el juego o el
chat no siguen ese patrón, así que no engañan. Se prueba con todas las kills y con solo las tuyas
y se queda el resultado más claro. Para no inventarse nada, varias celdas tienen que coincidir.
Si no sale claro, se guarda un pequeño informe para poder ajustarlo con datos reales.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass

import core

FPS = 4                    # fotogramas por segundo analizados
GW, GH = 24, 12            # celdas en las que se divide cada zona
SEARCH_S = 120.0           # se busca ±2 min alrededor de lo esperado
USE_MATCH_S = 420.0        # kills de los primeros ~7 min de partida (desde la primera)
TOP_CELLS = 10
# zonas de la imagen (fracciones): killfeed arriba a la derecha y aviso de kill abajo al centro
# cartel WON/LOST de fin de ronda: centro de arriba, por debajo del reloj del overlay
BANNER = ("crop=iw*0.30:ih*0.17:iw*0.35:ih*0.10", 24, 12)
MIN_ENDS = 10              # rondas con final exacto necesarias para fiarse del cartel

# killfeed: caja exacta del HUD de VALORANT (medida sobre la emisión de Vortexs), con más detalle
REGIONS = {
    "killfeed": ("todas", "crop=iw*0.31:ih*0.22:iw*0.685:ih*0.035", 32, 24),
    "aviso": ("tuyas", "crop=iw*0.30:ih*0.30:iw*0.35:ih*0.55", 24, 12),
}


@dataclass
class SyncResult:
    offset_s: float        # segundo del VOD en el que está el instante 0 de la partida
    shift_s: float         # cuánto se ha movido respecto a lo esperado
    confidence: float      # 0..1
    ok: bool
    reason: str = ""
    report: str = ""       # informe guardado si no salió claro
    data: dict | None = None   # (para el consenso entre partidas) fotogramas del killfeed, t0, kills
    approx: bool = False   # sincronizada con el desfase común (sin pico propio claro)


def report_dir() -> str:
    d = os.path.join(core.data_dir(), "informes")
    os.makedirs(d, exist_ok=True)
    return d


def _frames(vod: str, start: float, dur: float, hwaccel: bool, should_stop=None, crop: str = "",
            gw: int = GW, gh: int = GH) -> bytes:
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", "auto"]
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", vod, "-an",
            "-vf", f"fps={FPS},{crop + ',' if crop else ''}scale={gw * 4}:{gh * 4}:flags=area,"
                   f"scale={gw}:{gh}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **core._no_window())
    chunks = []
    try:
        while True:
            if should_stop and should_stop():
                p.kill()
                raise RuntimeError("Cancelado.")
            b = p.stdout.read(gw * gh * 64)
            if not b:
                break
            chunks.append(b)
    finally:
        if p.poll() is None:
            p.wait()
    return b"".join(chunks)


def _prep(frames):
    """Cambios normalizados por celda con tolerancia ±1 fotograma (+ fila final 'fuera')."""
    import numpy as np
    F = frames.astype(np.float32)
    D = np.zeros_like(F)
    D[1:] = np.abs(F[1:] - F[:-1])
    med = np.median(D, axis=0)
    mad = np.median(np.abs(D - med), axis=0) + 0.5
    Z = np.clip((D - med) / (mad * 3), 0, 8)
    S = Z.copy()
    S[1:] = np.maximum(S[1:], Z[:-1])
    S[:-1] = np.maximum(S[:-1], Z[1:])
    return np.vstack([S, np.zeros((1, S.shape[1]), np.float32)])


def _scores(S, t0, expected, events, offs):
    """Media por celda del cambio en los momentos de las kills, para cada desplazamiento."""
    import numpy as np
    n = len(S) - 1
    sc = np.zeros((len(offs), S.shape[1]), np.float32)
    val = np.zeros(len(offs), np.float32)
    for e in events:
        idx = np.rint((expected + e + offs - t0) * FPS).astype(np.int64)
        ins = (idx >= 0) & (idx < n)
        val += ins
        idx[~ins] = n
        sc += S[idx]
    sc /= np.maximum(val, 1)[:, None]
    sc[val < max(5, 0.6 * len(events))] = 0
    return sc


# En la emisión de Vortexs, al final de cada ronda salen DOS textos en el centro de arriba:
# el cartel WON/LOST justo en el momento exacto y otro texto 5,5-8 s después. Se busca la pareja.
PAIR_LAG = (5.5, 8.0)


def _analyze_once(frames, t0: float, expected: float, events: list, search_s: float = SEARCH_S,
                  pair=None, step: bool = False):
    """
    frames: array (n, GW*GH) uint8 de UNA zona. Para cada celda se mide cuánto cambia su brillo
    entre fotogramas (normalizado: picos sobre lo normal de esa celda) y se suma en los momentos
    en que deberían verse las kills. Se quedan las celdas que mejor siguen el patrón.
    """
    import numpy as np
    F = frames.astype(np.float32)
    n = F.shape[0]
    D = np.zeros_like(F)
    if step:
        # cambio que SE QUEDA (marcador): media del segundo siguiente contra la del anterior;
        # un parpadeo de un fotograma casi no cuenta
        w = FPS
        c = np.cumsum(np.vstack([np.zeros((1, F.shape[1]), np.float32), F]), axis=0)
        k = np.arange(w, n - w + 1)
        D[k] = np.abs((c[k + w] - c[k]) - (c[k] - c[k - w])) / w
    else:
        D[1:] = np.abs(F[1:] - F[:-1])
    med = np.median(D, axis=0)
    mad = np.median(np.abs(D - med), axis=0) + 0.5
    Z = np.clip((D - med) / (mad * 3), 0, 8)
    S = Z.copy()                                    # tolerancia ±1 fotograma
    S[1:] = np.maximum(S[1:], Z[:-1])
    S[:-1] = np.maximum(S[:-1], Z[1:])
    S = np.vstack([S, np.zeros((1, S.shape[1]), np.float32)])
    offs = np.arange(-search_s, search_s + 1e-6, 1 / FPS, dtype=np.float64)
    scores = np.zeros((len(offs), S.shape[1]), np.float32)
    valid = np.zeros(len(offs), np.float32)
    for e in events:
        idx = np.rint((expected + e + offs - t0) * FPS).astype(np.int64)
        inside = (idx >= 0) & (idx < n)
        valid += inside
        idx[~inside] = n
        scores += S[idx]
    scores[valid < max(5, 0.6 * len(events))] = 0
    # celdas que siguen el patrón: su mejor nota destaca mucho sobre su media
    coarse = scores[::FPS]
    prom_cell = coarse.max(axis=0) / (coarse.mean(axis=0) + 1e-6)
    top = np.argsort(prom_cell)[-max(TOP_CELLS, S.shape[1] // 20):]
    combo = scores[:, top].sum(axis=1)
    if pair:
        # nota de cada momento = su señal + la mejor señal 5,5-8 s después (el segundo texto)
        a_, b_ = int(round(pair[0] * FPS)), int(round(pair[1] * FPS))
        nxt = np.full(len(combo), 0.0)
        for k in range(a_, b_ + 1):
            sh = np.zeros(len(combo))
            sh[:len(combo) - k] = combo[k:]
            nxt = np.maximum(nxt, sh)
        combo = combo + nxt
    best = int(np.argmax(combo))
    peak = float(combo[best])
    # eco: las líneas del killfeed también "desaparecen" unos segundos después -> pico más tarde
    for j in (range(best - 1, max(-1, best - 41), -1) if not pair else []):
        if (combo[j] >= 0.8 * peak and combo[j] >= combo[max(j - 1, 0)]
                and combo[j] >= combo[min(j + 1, len(combo) - 1)]):
            best, peak = j, float(combo[j])
            break
    shift = float(offs[best])
    cell_peaks = offs[np.argmax(scores[:, top], axis=0)]
    agree = int(np.sum(np.abs(cell_peaks - shift) <= 1.0))
    far = np.abs(offs - shift) > 12
    second = float(combo[far].max()) if far.any() else 0.0
    prominence = peak / (float(combo[valid > 0].mean()) + 1e-6) if (valid > 0).any() else 0.0
    ratio = peak / (second + 1e-6)
    rest = combo[far & (valid > 0)]
    zpeak = (peak - float(rest.mean())) / (float(rest.std()) + 1e-6) if rest.size > 10 else 0.0
    # Límites medidos con pruebas: partida presente z 13-16 y ratio ≥2,8; ausente z ≤6 y ratio ≤1,3
    conf = min(1.0, max(0.0, (zpeak - 6) / 8)) * min(1.0, max(0.0, (ratio - 1.2) / 1.2))
    ok = zpeak >= 9 and ratio >= 1.6
    return {"shift": shift, "ok": bool(ok), "conf": float(conf), "agree": agree,
            "prominence": float(prominence), "ratio": float(ratio), "zpeak": float(zpeak),
            "n_events": len(events)}


def analyze(frames, t0: float, expected: float, events: list, search_s: float = SEARCH_S,
            kind: str = "killfeed"):
    """
    Valida con dos mitades independientes (rondas/kills pares e impares): cada mitad elige sus
    celdas y su desplazamiento por su cuenta. Si es real, las dos señalan el mismo momento.
    Para el cartel se prueba con la pareja de textos (tu emisión) y con el cartel solo.
    """
    if kind != "cartel":
        return _validated(frames, t0, expected, events, search_s, None, "killfeed")
    con = _validated(frames, t0, expected, events, search_s, PAIR_LAG, "pareja")
    sin = _validated(frames, t0, expected, events, search_s, None, "cartel")
    for r in (con, sin):
        if r["ok"]:
            return r
    return max((con, sin), key=lambda r: r["zpeak"])


def _validated(frames, t0, expected, events, search_s, pair, mode):
    step = mode == "marcador"
    full = _analyze_once(frames, t0, expected, events, search_s, pair, step)
    full["mode"] = mode
    if len(events) < 10:
        full["ok"] = False
        full["halves"] = None
        return full
    a = _analyze_once(frames, t0, expected, events[0::2], search_s, pair, step)
    b = _analyze_once(frames, t0, expected, events[1::2], search_s, pair, step)
    agree = abs(a["shift"] - full["shift"]) <= 1.0 and abs(b["shift"] - full["shift"]) <= 1.0
    full["halves"] = [a["shift"], b["shift"]]
    edge = abs(full["shift"]) >= search_s - 2          # pico pegado al borde: artefacto
    z, ratio = full["zpeak"], full["ratio"]
    lz, lr = learned_thr(mode)
    if mode == "pareja":
        # medido con tu emisión real: z 6-7, ratio 1,40 y mitades de acuerdo; falsos z ≤4,2 y ratio ≤1,19
        ok = agree and z >= (lz or 5.2) and ratio >= (lr or 1.28)
    elif mode == "cartel":
        # cartel solo (sin segundo texto): real z≈40 y ratio≈6,5; falsos z≤12 y ratio≤1,4
        ok = (z >= 20 and ratio >= 2.5) or (agree and z >= 14 and ratio >= 1.8)
    elif mode == "marcador":
        # marcador del HUD: aún sin medir con tu emisión -> solo vale solo si es muy claro;
        # si no, cuenta como voto para el acuerdo con otro método
        ok = agree and z >= (lz or 13) and ratio >= (lr or 2.2)
    else:
        # killfeed: con mucho movimiento detrás, un patrón falso llegó a z 12 y ratio 2,3
        ok = agree and z >= (lz or 13) and ratio >= (lr or 2.5)
    full["ok"] = bool(ok and not edge)
    full["conf"] = 0.6 + 0.4 * full["conf"] if full["ok"] else min(full["conf"], 0.3)
    if full["ok"] and mode == "pareja":
        full["conf"] = max(full["conf"], 0.75)
    return full


def _read_region(vod, t0, dur, crop, should_stop, gw=GW, gh=GH):
    import numpy as np
    raw = b""
    for hw in (True, False):
        try:
            raw = _frames(vod, t0, dur, hw, should_stop, crop, gw, gh)
        except RuntimeError as e:
            if "Cancelado" in str(e):
                raise
            raw = b""
        if len(raw) >= gw * gh * 40:
            break
    n = len(raw) // (gw * gh)
    if n < 40:
        return None
    return np.frombuffer(raw[: n * gw * gh], dtype=np.uint8).reshape(n, gw * gh)


# ---------------------------------------------------------------- lectura en una pasada + caché
# El marcador del HUD (arriba al centro): cambia justo al acabar cada ronda y no depende del overlay
MARCADOR = ("crop=iw*0.36:ih*0.075:iw*0.32:ih*0.0", 36, 6)
# Todas las zonas se sacan de UNA sola decodificación del vídeo (antes era una por zona)
TILES = [
    ("cartel", BANNER[0], BANNER[1], BANNER[2]),
    ("killfeed", REGIONS["killfeed"][1], REGIONS["killfeed"][2], REGIONS["killfeed"][3]),
    ("aviso", REGIONS["aviso"][1], REGIONS["aviso"][2], REGIONS["aviso"][3]),
    ("marcador", MARCADOR[0], MARCADOR[1], MARCADOR[2]),
    ("completa", "", 32, 18),
]
TILE_H = max(t[3] for t in TILES)
TILE_W = sum(t[2] for t in TILES)
CACHE_VER = 1
CACHE_PAD_S = 60.0          # se lee un poco de más para que al repetir se reaproveche
CACHE_KEEP_VODS = 3
CACHE_MAX_BYTES = 4 * 1024 ** 3


def cache_root() -> str:
    d = os.path.join(core.data_dir(), "cache_sync")
    os.makedirs(d, exist_ok=True)
    return d


def _vod_cache_dir(vod: str) -> str:
    import hashlib
    try:
        st = os.stat(vod)
        ident = f"{os.path.abspath(vod).lower()}|{st.st_size}|{int(st.st_mtime)}|v{CACHE_VER}|{TILE_W}x{TILE_H}|{FPS}"
    except OSError:
        ident = f"{os.path.abspath(vod).lower()}|v{CACHE_VER}"
    d = os.path.join(cache_root(), hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16])
    os.makedirs(d, exist_ok=True)
    try:
        with open(os.path.join(d, "vod.txt"), "w", encoding="utf-8") as f:
            f.write(vod + "\n")
        os.utime(d, None)
    except OSError:
        pass
    return d


def prune_cache(keep_vods: int = CACHE_KEEP_VODS, max_bytes: int = CACHE_MAX_BYTES) -> None:
    """Deja solo la caché de los últimos VODs usados y sin pasarse de tamaño."""
    import shutil
    try:
        root = cache_root()
        dirs = [os.path.join(root, x) for x in os.listdir(root) if os.path.isdir(os.path.join(root, x))]
    except OSError:
        return
    dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    def size(p):
        tot = 0
        for f in os.listdir(p):
            try:
                tot += os.path.getsize(os.path.join(p, f))
            except OSError:
                pass
        return tot
    total = 0
    for i, d in enumerate(dirs):
        total += size(d)
        if i >= keep_vods or total > max_bytes:
            shutil.rmtree(d, ignore_errors=True)


def _frames_all(vod: str, start: float, dur: float, hwaccel: bool, should_stop=None) -> bytes:
    """Una sola pasada de ffmpeg: cada zona se recorta y reduce y se pegan una al lado de otra."""
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", "auto"]
    n = len(TILES)
    chains = [f"[0:v]fps={FPS},split={n}" + "".join(f"[s{i}]" for i in range(n))]
    for i, (_, crop, gw, gh) in enumerate(TILES):
        chains.append(f"[s{i}]{crop + ',' if crop else ''}scale={gw * 4}:{gh * 4}:flags=area,"
                      f"scale={gw}:{gh}:flags=area,format=gray,pad={gw}:{TILE_H}:0:0[t{i}]")
    chains.append("".join(f"[t{i}]" for i in range(n)) + f"hstack=inputs={n},format=gray[out]")
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", vod, "-an",
            "-filter_complex", ";".join(chains), "-map", "[out]",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **core._no_window())
    chunks = []
    try:
        while True:
            if should_stop and should_stop():
                p.kill()
                raise RuntimeError("Cancelado.")
            b = p.stdout.read(TILE_W * TILE_H * 64)
            if not b:
                break
            chunks.append(b)
    finally:
        if p.poll() is None:
            p.wait()
    return b"".join(chunks)


def _read_all(vod: str, start: float, dur: float, should_stop=None, vod_duration: float = 0.0):
    """
    Devuelve (u0, frames) con frames de forma (n, TILE_H, TILE_W): el fotograma k es el
    segundo u0 + k/FPS del VOD. Lo ya leído se guarda en caché: repetir la sincronización
    de un VOD no vuelve a decodificar el vídeo.
    """
    import math
    import numpy as np
    fsz = TILE_W * TILE_H
    u0 = math.floor(max(0.0, start) * FPS) / FPS
    need = int(math.ceil((start + dur - u0) * FPS))
    cdir = _vod_cache_dir(vod)
    # 1) ¿ya está en caché?
    try:
        names = [x for x in os.listdir(cdir) if x.endswith(".npz")]
    except OSError:
        names = []
    for name in names:
        try:
            s_ms, n_ = (int(v) for v in name[:-4].split("_"))
        except ValueError:
            continue
        s = s_ms / 1000.0
        i0 = int(round((u0 - s) * FPS))
        if i0 < 0 or i0 + need > n_ + 1:
            continue
        try:
            with np.load(os.path.join(cdir, name)) as z:
                arr = z["f"]
        except Exception:
            continue
        part = arr[i0:i0 + need]
        if len(part) >= 40:
            return s + i0 / FPS, part
    # 2) leer del vídeo (con margen) y guardar
    r0 = max(0.0, u0 - CACHE_PAD_S)
    r0 = math.floor(r0 * FPS) / FPS
    r1 = start + dur + CACHE_PAD_S
    if vod_duration:
        r1 = min(vod_duration, r1)
    raw = b""
    for hw in (True, False):
        try:
            raw = _frames_all(vod, r0, r1 - r0, hw, should_stop)
        except RuntimeError as e:
            if "Cancelado" in str(e):
                raise
            raw = b""
        if len(raw) >= fsz * 40:
            break
    n = len(raw) // fsz
    if n < 40:
        return u0, None
    arr = np.frombuffer(raw[: n * fsz], dtype=np.uint8).reshape(n, TILE_H, TILE_W)
    try:
        tmp = os.path.join(cdir, f"tmp_{os.getpid()}_{id(arr)}.npz")
        np.savez_compressed(tmp, f=arr)
        os.replace(tmp, os.path.join(cdir, f"{int(round(r0 * 1000))}_{n}.npz"))
    except OSError:
        pass
    i0 = int(round((u0 - r0) * FPS))
    return r0 + i0 / FPS, arr[i0:i0 + need]


def _tile(arr, u0: float, name: str, a: float, b: float):
    """Saca una zona de la lectura conjunta, entre los segundos a y b del VOD -> (frames, t0)."""
    import numpy as np
    x0 = 0
    for nm, _, gw, gh in TILES:
        if nm == name:
            break
        x0 += gw
    else:
        return None, a
    gw, gh = next((t[2], t[3]) for t in TILES if t[0] == name)
    n = len(arr)
    i0 = max(0, int(round((a - u0) * FPS)))
    i1 = min(n, int(round((b - u0) * FPS)))
    if i1 - i0 < 40:
        return None, a
    fr = np.ascontiguousarray(arr[i0:i1, :gh, x0:x0 + gw]).reshape(i1 - i0, gw * gh)
    return fr, u0 + i0 / FPS


def _halves_ok(r, tol=1.0) -> bool:
    h = r.get("halves")
    return bool(h) and all(abs(x - r["shift"]) <= tol for x in h)


LATE = {"cartel": (5.0, 8.5), "marcador": (5.0, 8.5), "killfeed": (4.0, 5.75), "aviso": (4.0, 5.75)}


def _supports(x: dict, p: float) -> bool:
    """¿El resultado x respalda que la partida esté en p (mismo segundo o con su retraso típico)?"""
    if x.get("zpeak", 0) < 5.0 or x.get("family") == "acuerdo":
        return False
    d = x["shift"] - p
    if abs(d) <= 1.0:
        return True
    lo, hi = LATE.get(x.get("family"), (99, 99))
    return lo <= d <= hi


def _decide(results: list):
    pareja = next((r for r in results if r.get("set") == "cartel(pareja)"), None)
    if pareja is not None and pareja["ok"]:
        return pareja
    if pareja is not None and pareja.get("zpeak", 0) >= 4.0 and pareja.get("ratio", 0) >= 1.15 \
            and abs(pareja["shift"]) < SEARCH_S - 2:
        sup = [x for x in results if x is not pareja and _supports(x, pareja["shift"])]
        if sup:
            out = dict(pareja)
            out.update(ok=True, conf=0.65, family="acuerdo",
                       set="cartel(pareja)+" + "+".join(x["set"] for x in sup))
            return out
    oks = [r for r in results if r["ok"] and r is not pareja]
    if oks:
        oks.sort(key=lambda r: r["conf"], reverse=True)
        b = oks[0]
        # ¿es el segundo texto / la línea que desaparece de un pico de pareja anterior?
        if pareja is not None and b.get("family") in LATE:
            lo, hi = LATE[b["family"]]
            if lo <= b["shift"] - pareja["shift"] <= hi and pareja.get("zpeak", 0) >= 3.5:
                out = dict(pareja)
                out.update(ok=True, conf=0.6, family="acuerdo", set=f"cartel(pareja) corregido desde {b['set']}")
                return out
        return b
    return _agreement(results)


def _agreement(results: list):
    """
    Dos métodos independientes (cartel, marcador, killfeed, aviso) que señalan el mismo segundo:
    un pico falso de uno casi nunca coincide con un pico falso de otro.
    """
    cands = [r for r in results if r.get("zpeak", 0) >= 4.5 and r.get("ratio", 0) >= 1.15
             and abs(r["shift"]) < SEARCH_S - 2]
    best = None
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i], cands[j]
            if a["family"] == b["family"] or abs(a["shift"] - b["shift"]) > 1.0:
                continue
            # el cartel solo y el marcador se equivocan juntos (los dos saltan con el segundo texto)
            if {a.get("set"), b.get("set")} == {"cartel(cartel)", "marcador"}:
                continue
            if not (_halves_ok(a) or _halves_ok(b)):
                continue
            sc = a["zpeak"] + b["zpeak"]
            if best is None or sc > best[0]:
                best = (sc, a, b)
    if best is None:
        return None
    _, a, b = best
    shift = (a["shift"] * a["zpeak"] + b["shift"] * b["zpeak"]) / (a["zpeak"] + b["zpeak"])
    return {"set": f"acuerdo({a['set']}+{b['set']})", "ok": True, "conf": 0.6, "shift": float(shift),
            "agree": 0, "prominence": 0.0, "ratio": min(a["ratio"], b["ratio"]),
            "zpeak": min(a["zpeak"], b["zpeak"]), "halves": None, "family": "acuerdo"}


def sync_match(vod: str, match: core.Match, expected_offset_s: float, vod_duration: float,
               progress=None, should_stop=None) -> SyncResult:
    try:
        import numpy  # noqa: F401
    except ImportError:
        return SyncResult(expected_offset_s, 0, 0, False, "falta numpy (reinstala Vortexs Clips)")
    allk = sorted(k.t_match / 1000.0 for k in match.kills)
    if len(allk) < 8:
        return SyncResult(expected_offset_s, 0, 0, False, "pocas kills en la partida")
    first = allk[0]
    allk = [k for k in allk if k <= first + USE_MATCH_S]
    mine = [k for k in sorted(x.t_match / 1000.0 for x in match.kills if x.killer == match.my_puuid)
            if k <= first + USE_MATCH_S]
    t0 = max(0.0, expected_offset_s + first - SEARCH_S - 3)
    t1 = expected_offset_s + allk[-1] + SEARCH_S + 3
    if vod_duration:
        t1 = min(vod_duration, t1)
    if t1 - t0 < 30:
        return SyncResult(expected_offset_s, 0, 0, False, "la partida no está en el VOD")
    ends = sorted(r.end_match_ms / 1000.0 for r in match.rounds if r.end_match_ms)
    use_ends = len(ends) >= MIN_ENDS
    b0 = b1 = 0.0
    if use_ends:
        b0 = max(0.0, expected_offset_s + ends[0] - SEARCH_S - 5)
        b1 = expected_offset_s + ends[-1] + SEARCH_S + 8
        if vod_duration:
            b1 = min(vod_duration, b1)
    u_a = min(t0, b0) if use_ends else t0
    u_b = max(t1, b1) if use_ends else t1
    if progress:
        progress(None, f"Leyendo {match.map_name} del vídeo (cartel, marcador y killfeed a la vez)…")
    u0, arr = _read_all(vod, u_a, u_b - u_a, should_stop, vod_duration)
    if arr is None:
        return SyncResult(expected_offset_s, 0, 0, False, "no se pudo leer el vídeo")
    if progress:
        progress(None, f"Buscando el patrón de {match.map_name}…")
    results, saved = [], {}
    b0e = kf_t0 = None
    # 1) final de ronda: cartel WON/LOST (pareja de textos o solo) y marcador del HUD
    if use_ends:
        fr, b0e = _tile(arr, u0, "cartel", b0, b1)
        if fr is not None:
            saved["cartel"] = fr
            for pair, mode in ((PAIR_LAG, "pareja"), (None, "cartel")):
                r = _validated(fr, b0e, expected_offset_s, ends, SEARCH_S, pair, mode)
                r["set"], r["family"] = f"cartel({mode})", "cartel"
                results.append(r)
        fr, bm = _tile(arr, u0, "marcador", b0, b1)
        if fr is not None:
            saved["marcador"] = fr
            r = _validated(fr, bm, expected_offset_s, ends, SEARCH_S, None, "marcador")
            r["set"], r["family"] = "marcador", "marcador"
            results.append(r)
    # 2) kills: killfeed (todas) y aviso de kill (las tuyas)
    for region, (kind, _crop, _gw, _gh) in REGIONS.items():
        ev = allk if kind == "todas" else mine
        if len(ev) < 5:
            continue
        fr, te = _tile(arr, u0, region, t0, t1)
        if fr is None:
            continue
        saved[region] = fr
        if region == "killfeed":
            kf_t0 = te
        r = _validated(fr, te, expected_offset_s, ev, SEARCH_S, None, region)
        r["set"], r["family"] = region, region
        results.append(r)
    if not results:
        return SyncResult(expected_offset_s, 0, 0, False, "no se pudo leer el vídeo")
    # 3) decisión (medida con VODs reales): el cartel en pareja es el que acierta; los demás o
    #    coinciden con él o van con un retraso fijo (segundo texto / marcador: 5-8,5 s; killfeed: la
    #    línea que desaparece, ~4-5,5 s). Esos retrasos también cuentan como respaldo de la pareja.
    best = _decide(results)
    if best is not None and best not in results:
        results.append(best)
    if best is not None:
        _log(match, expected_offset_s, results)
        out = SyncResult(expected_offset_s + best["shift"], best["shift"], best["conf"], True, "", "", None)
        out.methods = _summary(results)
        return out
    results.sort(key=lambda r: r["conf"], reverse=True)
    best = results[0]
    fr, _ = _tile(arr, u0, "completa", t0, t1)
    if fr is not None:
        saved["completa"] = fr
    rep = save_report(saved, kf_t0 if kf_t0 is not None else t0, expected_offset_s, allk, mine, match, results)
    _log(match, expected_offset_s, results)
    data = {"expected": expected_offset_s}
    if "killfeed" in saved:
        data.update({"frames": saved["killfeed"], "t0": kf_t0, "events": allk})
    if "cartel" in saved:
        data.update({"cartel": saved["cartel"], "b0": b0e, "ends": ends})
    data["cands"] = [r["shift"] for r in results]
    out = SyncResult(expected_offset_s + best["shift"], best["shift"], best["conf"], False,
                     "no he encontrado un patrón claro de kills", rep, data)
    out.methods = _summary(results)
    return out


def sync_many(vod: str, items: list, vod_duration: float, workers: int = 2, progress=None,
              should_stop=None) -> dict:
    """
    items: [(match, expected_offset_s)]. Sincroniza varias partidas a la vez (cada una es un
    ffmpeg leyendo su trozo del VOD). progress(frac, texto). Devuelve {match_id: SyncResult}.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    prune_cache()
    n = len(items)
    if not n:
        return {}
    workers = max(1, min(n, int(workers or 1)))
    lock = threading.Lock()
    state = {"done": 0, "err": False}

    def stop():
        return bool(should_stop and should_stop()) or state["err"]

    def one(item):
        m, exp = item

        def prog(_f, t):
            if progress:
                with lock:
                    d = state["done"]
                progress(d / n, f"{d} de {n} listas ({workers} a la vez) · {m.map_name} "
                                f"{m.started_at.astimezone():%H:%M}: {t}")
        try:
            r = sync_match(vod, m, exp, vod_duration, prog, stop)
        except Exception:
            state["err"] = True
            raise
        with lock:
            state["done"] += 1
            d = state["done"]
        if progress:
            progress(d / n, f"{d} de {n} partidas leídas")
        return r

    if progress:
        progress(0, f"Sincronizando {n} partidas ({workers} a la vez)…")
    res, errs = {}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [(m.match_id, ex.submit(one, (m, e))) for m, e in items]
        for mid, fu in futs:
            try:
                res[mid] = fu.result()
            except Exception as e:
                errs.append(e)
    if should_stop and should_stop():
        raise RuntimeError("Cancelado.")
    real = [e for e in errs if "Cancelado" not in str(e)]
    if real:
        raise real[0]
    return res


def verify_match(vod: str, match: core.Match, offset_s: float, vod_duration: float = 0.0,
                 should_stop=None) -> dict:
    """
    Comprobación de una partida ya colocada en offset_s, buscando solo ±20 s alrededor (rápido:
    usa la caché). Primero con la pareja de textos del final de ronda (lo más fiable en tu emisión);
    si no hay finales de ronda, con el killfeed teniendo en cuenta el «eco» de cuando desaparece la línea.
    Devuelve {"state": "ok" | "dudosa" | "?", "shift": s, "z": z}; con "dudosa", offset_s + shift
    es dónde parece estar de verdad.
    """
    out = {"state": "?", "shift": 0.0, "z": 0.0}
    try:
        import numpy  # noqa: F401
    except ImportError:
        return out
    allk = sorted(k.t_match / 1000.0 for k in match.kills)
    ends = sorted(r.end_match_ms / 1000.0 for r in match.rounds if r.end_match_ms)
    if len(allk) < 8:
        return out
    evs = ends if len(ends) >= MIN_ENDS else allk
    a = max(0.0, offset_s + min(evs[0], allk[0]) - 25)
    b = offset_s + max(evs[-1], allk[-1]) + 25
    if vod_duration:
        b = min(vod_duration, b)
    if b - a < 30:
        return out
    u0, arr = _read_all(vod, a, b - a, should_stop, vod_duration)
    if arr is None:
        return out
    how = ""
    if len(ends) >= MIN_ENDS:
        fr, t0 = _tile(arr, u0, "cartel", a, b)
        if fr is not None:
            r = _validated(fr, t0, offset_s, ends, 20.0, PAIR_LAG, "pareja")
            how = "cartel"
            out.update(shift=float(r["shift"]), z=float(r["zpeak"]))
            if abs(r["shift"]) <= 1.0 and r["zpeak"] >= 2.5:
                out["state"] = "ok"
            elif abs(r["shift"]) > 1.5 and r["zpeak"] >= 6.0 and r["ratio"] >= 1.3 and _halves_ok(r):
                out["state"] = "dudosa"
    if out["state"] == "?" and not how:
        fr, t0 = _tile(arr, u0, "killfeed", a, b)
        if fr is not None:
            r = _analyze_once(fr, t0, offset_s, allk, 20.0)
            how = "killfeed"
            out.update(shift=float(r["shift"]), z=float(r["zpeak"]))
            lo, hi = LATE["killfeed"]
            if (abs(r["shift"]) <= 1.0 or lo <= r["shift"] <= hi) and r["zpeak"] >= 3.0:
                out["state"] = "ok"
            elif abs(r["shift"]) > 1.5 and r["zpeak"] >= 6.0 and r["ratio"] >= 1.3:
                out["state"] = "dudosa"
    _log_line(f"comprobación {match.map_name} {match.started_at.astimezone():%d/%m %H:%M}: {out['state']} "
              f"({how}: pico {out['shift']:+.2f} s, z {out['z']:.1f})")
    return out


# ---------------------------------------------------------------- aprender de tus correcciones
LEARN_MIN = 4                     # ejemplos buenos necesarios para ajustar un método
LEARN_FLOOR = {"pareja": (3.5, 1.15), "killfeed": (7.0, 1.5), "marcador": (6.0, 1.4)}
LEARN_DEFAULT = {"pareja": (5.2, 1.28), "killfeed": (13.0, 2.5), "marcador": (13.0, 2.2)}
_LEARN_CACHE = {"mtime": None, "thr": {}}


def _summary(results: list) -> list:
    return [{"set": r.get("set"), "family": r.get("family"), "mode": r.get("mode"), "shift": float(r["shift"]),
             "z": float(r.get("zpeak", 0)), "ratio": float(r.get("ratio", 0)), "halves_ok": _halves_ok(r)}
            for r in results if r.get("family") != "acuerdo"]


def learn_file() -> str:
    return os.path.join(core.data_dir(), "aprendizaje_sincro.json")


def _load_learn() -> list:
    try:
        with open(learn_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def learned_thr(mode: str):
    """(z, ratio) aprendidos de tus sincronizaciones a mano para ese método, o (None, None)."""
    if mode not in LEARN_DEFAULT:
        return None, None
    try:
        mt = os.path.getmtime(learn_file())
    except OSError:
        return None, None
    if _LEARN_CACHE["mtime"] != mt:
        _LEARN_CACHE["thr"] = compute_thresholds(_load_learn())
        _LEARN_CACHE["mtime"] = mt
    return _LEARN_CACHE["thr"].get(mode, (None, None))


def compute_thresholds(samples: list) -> dict:
    """
    Con lo que vio cada método en el sitio correcto (picos buenos) y lejos de él (picos falsos):
    umbral = por debajo de casi todos los buenos y por encima de todos los falsos.
    Nunca más bajo que un mínimo de seguridad ni mucho más alto que el de fábrica.
    """
    thr = {}
    for mode, (dz, dr) in LEARN_DEFAULT.items():
        good = [x for x in samples if x.get("mode") == mode and x.get("true") and x.get("halves_ok")]
        bad = [x for x in samples if x.get("mode") == mode and not x.get("true")]
        if len(good) < LEARN_MIN:
            continue
        gz = sorted(x["z"] for x in good)
        gr = sorted(x["ratio"] for x in good)
        p20z, p20r = gz[int(len(gz) * 0.2)], gr[int(len(gr) * 0.2)]
        fz = max((x["z"] for x in bad), default=0.0)
        fr = max((x["ratio"] for x in bad), default=0.0)
        fz_, fr_ = LEARN_FLOOR[mode]
        z = min(dz * 1.5, max(fz_, 0.85 * p20z, 1.1 * fz))
        r = min(dr * 1.5, max(fr_, 0.92 * p20r, 1.05 * fr))
        thr[mode] = (round(z, 2), round(r, 3))
    return thr


def learn_from_manual(vod: str, match: core.Match, true_offset_s: float, vod_duration: float = 0.0,
                      should_stop=None) -> list:
    """
    Tras una sincronización a mano: analiza la partida tomando tu momento como el bueno y apunta
    qué vio cada método ahí (pico bueno) o lejos de ahí (pico falso). Devuelve lo apuntado.
    """
    r = sync_match(vod, match, true_offset_s, vod_duration, None, should_stop)
    rows = []
    for x in getattr(r, "methods", None) or []:
        if not x.get("mode"):
            continue
        rows.append({"mode": x["mode"], "set": x["set"], "z": x["z"], "ratio": x["ratio"],
                     "halves_ok": x["halves_ok"], "true": abs(x["shift"]) <= 1.0,
                     "match": match.match_id, "when": time.strftime("%Y-%m-%d %H:%M")})
    if rows:
        data = [d for d in _load_learn() if d.get("match") != match.match_id] + rows
        try:
            tmp = learn_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data[-400:], f)
            os.replace(tmp, learn_file())
        except OSError:
            pass
        _log_line(f"aprendido de tu sincronización a mano en {match.map_name}: " +
                  ", ".join(f"{x['set']} {'bien' if x['true'] else 'falso'} z {x['z']:.1f}" for x in rows))
    return rows


# ---------------------------------------------------------------- consenso entre partidas
LOCAL_S = 20.0


JUMP_S = 6.0      # diferencia de desfase entre partidas seguidas que indica un corte del directo


def _neighbors(mid, times: dict, good: dict):
    """Partidas sincronizadas justo antes y justo después (en el tiempo) de mid."""
    t = times.get(mid)
    if t is None:
        return None, None
    prev = [(times[g], r) for g, r in good.items() if g in times and times[g] < t]
    nxt = [(times[g], r) for g, r in good.items() if g in times and times[g] > t]
    return (max(prev, key=lambda x: x[0])[1] if prev else None,
            min(nxt, key=lambda x: x[0])[1] if nxt else None)


def stream_jumps(good: dict, times: dict) -> list:
    """Saltos del desfase entre partidas seguidas (reconexión del directo): [(antes, después, salto)]."""
    seq = sorted((times[m], m, r.shift_s) for m, r in good.items() if m in times)
    out = []
    for (t1, m1, s1), (t2, m2, s2) in zip(seq, seq[1:]):
        if abs(s2 - s1) > JUMP_S:
            out.append((m1, m2, s2 - s1))
    return out


def consensus(results: dict, log_name: str = "", times: dict | None = None) -> dict:
    """
    results: {match_id: SyncResult} de un mismo VOD. Las partidas que no se sincronizaron solas
    se afinan alrededor del desfase común (las del mismo directo se parecen mucho: mismo delay y
    casi la misma espera de selección de agente / carga).
    """
    import numpy as np
    times = times or {}
    good_d = {mid: r for mid, r in results.items() if r.ok}
    good = list(good_d.values())
    jumps = stream_jumps(good_d, times)
    for m1, m2, j in jumps:
        _log_line(f"{log_name} posible corte del directo entre {m1[:8]} y {m2[:8]}: el desfase salta {j:+.1f} s")
    bad = {mid: r for mid, r in results.items() if not r.ok and r.data and
           ("frames" in r.data or "cartel" in r.data)}
    if not bad:
        return results
    g = None
    how = ""
    if good:
        g = float(np.median([r.shift_s for r in good]))
        how = f"mediana de {len(good)} partida(s) sincronizadas"
    elif len(bad) >= 2:
        # sumar la señal de todas las partidas (misma rejilla del killfeed)
        offs = np.arange(-SEARCH_S, SEARCH_S + 1e-6, 1 / FPS)
        bad = {mid: r for mid, r in bad.items() if "frames" in r.data}
        if len(bad) < 2:
            _log_line(f"{log_name} consenso: no hay desfase común claro")
            return results
        preps = {mid: _prep(r.data["frames"]) for mid, r in bad.items()}

        def total(part):
            tot = None
            for mid, r in bad.items():
                ev = r.data["events"] if part is None else r.data["events"][part::2]
                sc = _scores(preps[mid], r.data["t0"], r.data["expected"], ev, offs)
                sc = (sc - sc.mean(0)) / (sc.std(0) + 1e-6)
                tot = sc if tot is None else tot + sc
            return tot
        tot = total(None)
        prom = tot.max(0)
        top = np.argsort(prom)[-max(TOP_CELLS, tot.shape[1] // 20):]
        c = tot[:, top].sum(1)
        b = int(np.argmax(c))
        far = np.abs(offs - offs[b]) > 12
        z = (c[b] - c[far].mean()) / (c[far].std() + 1e-6)
        ha = total(0)[:, top].sum(1)
        hb = total(1)[:, top].sum(1)
        if abs(offs[int(np.argmax(ha))] - offs[b]) <= 1 and abs(offs[int(np.argmax(hb))] - offs[b]) <= 1 and z >= 6:
            g = float(offs[b])
            how = f"señal sumada de {len(bad)} partidas (z {z:.1f})"
    if g is None:
        _log_line(f"{log_name} consenso: no hay desfase común claro")
        return results
    out = dict(results)
    for mid, r in bad.items():
        d = r.data
        # centros donde buscar: el desfase común y los picos propios de esta partida que
        # cayeron lejos del común (p.ej. una pausa en el directo que la desplaza varios segundos).
        centers = [g]
        prev_r, next_r = _neighbors(mid, times, good_d)
        for nb in (prev_r, next_r):
            if nb is not None and all(abs(nb.shift_s - x) > 2 for x in centers):
                centers.append(float(nb.shift_s))
        # con un corte del directo, lo más fiable es la partida de al lado, no la media de todas
        fallback = g
        if jumps and (prev_r is not None or next_r is not None):
            fallback = float((prev_r or next_r).shift_s)
        for cs in d.get("cands", []):
            if abs(cs - g) > 3 and all(abs(cs - x) > 2 for x in centers):
                centers.append(float(cs))
        del centers[6:]
        # a) cartel (pareja de textos) en ±20 s alrededor de cada centro; se queda el más claro
        pick = None
        if "cartel" in d and len(d["ends"]) >= MIN_ENDS:
            for ctr in centers:
                loc = _validated(d["cartel"], d["b0"], d["expected"] + ctr, d["ends"], LOCAL_S, PAIR_LAG, "pareja")
                loc_ok = (loc["halves"] is not None and abs(loc["halves"][0] - loc["shift"]) <= 1.0
                          and abs(loc["halves"][1] - loc["shift"]) <= 1.0 and loc["zpeak"] >= 2.8
                          and loc["ratio"] >= 1.35 and abs(loc["shift"]) < LOCAL_S - 1)
                if loc_ok and (pick is None or loc["zpeak"] > pick[3]):
                    pick = (ctr + loc["shift"], loc["ratio"], loc["halves"], loc["zpeak"])
        if pick is not None:
            shift = pick[0]
            out[mid] = SyncResult(d["expected"] + shift, shift, 0.65, True, "", r.report, None, False)
            _log_line(f"{log_name} consenso ({how}): partida {mid[:8]} -> {shift:+.2f}s afinada con el cartel "
                      f"(z {pick[3]:.1f} ratio {pick[1]:.2f})")
            continue
        if "frames" not in d:
            out[mid] = SyncResult(d["expected"] + fallback, fallback, 0.35, True, "", r.report, None, True)
            _log_line(f"{log_name} consenso ({how}): partida {mid[:8]} -> {fallback:+.2f}s aprox "
                      f"({'partida vecina, hubo un corte' if fallback != g else 'desfase común'})")
            continue
        # b) killfeed en ±20 s alrededor de cada centro; se exige que las dos mitades coincidan
        S = _prep(d["frames"])
        pick = None
        for ctr in centers:
            loc = np.arange(ctr - LOCAL_S, ctr + LOCAL_S + 1e-6, 1 / FPS)
            sc = _scores(S, d["t0"], d["expected"], d["events"], loc)
            prom = sc.max(0) / (sc.mean(0) + 1e-6)
            top = np.argsort(prom)[-max(TOP_CELLS, sc.shape[1] // 20):]
            c = sc[:, top].sum(1)
            b = int(np.argmax(c))
            sa = _scores(S, d["t0"], d["expected"], d["events"][0::2], loc)[:, top].sum(1)
            sb = _scores(S, d["t0"], d["expected"], d["events"][1::2], loc)[:, top].sum(1)
            agree = abs(loc[int(np.argmax(sa))] - loc[b]) <= 1.5 and abs(loc[int(np.argmax(sb))] - loc[b]) <= 1.5
            if not agree:
                continue
            fr = np.abs(loc - loc[b]) > 8
            zz = (c[b] - c[fr].mean()) / (c[fr].std() + 1e-6) if fr.any() else 0.0
            if pick is None or zz > pick[1]:
                pick = (float(loc[b]), float(zz))
        if pick is not None:
            shift, conf, approx = pick[0], 0.55, False
        else:
            shift, conf, approx = fallback, 0.35, True
        out[mid] = SyncResult(d["expected"] + shift, shift, conf, True, "", r.report, None, approx)
        _log_line(f"{log_name} consenso ({how}): partida {mid[:8]} -> {shift:+.2f}s "
                  f"{'afinada' if not approx else 'aprox (desfase común)'}")
    return out


def _log_line(txt):
    try:
        with open(os.path.join(report_dir(), "sincronizacion.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + txt + "\n")
    except OSError:
        pass


def save_report(saved, t0, expected, allk, mine, match, results) -> str:
    """Informe pequeño (unos cientos de KB) para ajustar el detector con datos reales."""
    import numpy as np
    name = f"sync_{match.map_name}_{match.started_at.astimezone():%Y%m%d_%H%M}.npz"
    path = os.path.join(report_dir(), name)
    meta = {"t0": t0, "expected": expected, "fps": FPS, "grid": [GW, GH], "map": match.map_name,
            "match_id": match.match_id, "results": results,
            "regions": {k: list(v[1:]) for k, v in REGIONS.items()}}
    arrays = {f"frames_{k}": v for k, v in saved.items()}
    rounds = [r.start_match_ms / 1000.0 for r in match.rounds if r.start_match_ms is not None]
    deaths = sorted(k.t_match / 1000.0 for k in match.kills if k.victim == match.my_puuid)
    ends = [r.end_match_ms / 1000.0 for r in match.rounds if r.end_match_ms]
    np.savez_compressed(path, allk=np.array(allk), mine=np.array(mine), rounds=np.array(rounds),
                        ends=np.array(ends),
                        deaths=np.array(deaths),
                        meta=np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8), **arrays)
    files = sorted((os.path.join(report_dir(), f) for f in os.listdir(report_dir()) if f.endswith(".npz")),
                   key=os.path.getmtime)
    for f in files[:-20]:
        try:
            os.remove(f)
        except OSError:
            pass
    return path


def _log(match, expected, results):
    try:
        with open(os.path.join(report_dir(), "sincronizacion.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" | {match.map_name} "
                    f"{match.started_at.astimezone():%d/%m %H:%M} | esperado {expected:.1f}s | " +
                    " ; ".join(f"{r['set']}: {'OK' if r['ok'] else 'no'} mov {r['shift']:+.2f}s "
                               f"conf {r['conf']:.2f} coinc {r['agree']} prom {r['prominence']:.2f} "
                               f"ratio {r['ratio']:.2f} z {r.get('zpeak', 0):.1f} mitades {r.get('halves')}"
                               for r in results) + "\n")
    except OSError:
        pass
