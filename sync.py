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
REGIONS = {
    "killfeed": ("todas", "crop=iw*0.42:ih*0.40:iw*0.58:ih*0.02"),
    "aviso": ("tuyas", "crop=iw*0.30:ih*0.30:iw*0.35:ih*0.55"),
}


@dataclass
class SyncResult:
    offset_s: float        # segundo del VOD en el que está el instante 0 de la partida
    shift_s: float         # cuánto se ha movido respecto a lo esperado
    confidence: float      # 0..1
    ok: bool
    reason: str = ""
    report: str = ""       # informe guardado si no salió claro


def report_dir() -> str:
    d = os.path.join(core.data_dir(), "informes")
    os.makedirs(d, exist_ok=True)
    return d


def _frames(vod: str, start: float, dur: float, hwaccel: bool, should_stop=None, crop: str = "") -> bytes:
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", "auto"]
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", vod, "-an",
            "-vf", f"fps={FPS},{crop + ',' if crop else ''}scale={GW * 4}:{GH * 4}:flags=area,"
                   f"scale={GW}:{GH}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **core._no_window())
    chunks = []
    try:
        while True:
            if should_stop and should_stop():
                p.kill()
                raise RuntimeError("Cancelado.")
            b = p.stdout.read(GW * GH * 64)
            if not b:
                break
            chunks.append(b)
    finally:
        if p.poll() is None:
            p.wait()
    return b"".join(chunks)


def _analyze_once(frames, t0: float, expected: float, events: list, search_s: float = SEARCH_S):
    """
    frames: array (n, GW*GH) uint8 de UNA zona. Para cada celda se mide cuánto cambia su brillo
    entre fotogramas (normalizado: picos sobre lo normal de esa celda) y se suma en los momentos
    en que deberían verse las kills. Se quedan las celdas que mejor siguen el patrón.
    """
    import numpy as np
    F = frames.astype(np.float32)
    n = F.shape[0]
    D = np.zeros_like(F)
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
    top = np.argsort(prom_cell)[-TOP_CELLS:]
    combo = scores[:, top].sum(axis=1)
    best = int(np.argmax(combo))
    peak = float(combo[best])
    # eco: las líneas del killfeed también "desaparecen" unos segundos después -> pico más tarde
    for j in range(best - 1, max(-1, best - 41), -1):
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


def analyze(frames, t0: float, expected: float, events: list, search_s: float = SEARCH_S):
    """
    Valida con dos mitades independientes (kills pares e impares): cada mitad elige sus celdas y su
    desplazamiento por su cuenta. Si la partida está de verdad, las dos señalan el mismo momento;
    si es casualidad, cada una apunta a un sitio distinto.
    """
    full = _analyze_once(frames, t0, expected, events, search_s)
    if len(events) < 10:
        full["ok"] = False
        full["halves"] = None
        return full
    a = _analyze_once(frames, t0, expected, events[0::2], search_s)
    b = _analyze_once(frames, t0, expected, events[1::2], search_s)
    agree = abs(a["shift"] - full["shift"]) <= 1.0 and abs(b["shift"] - full["shift"]) <= 1.0
    full["halves"] = [a["shift"], b["shift"]]
    full["ok"] = bool(agree and full["zpeak"] >= 7 and full["ratio"] >= 1.3)
    # % que se ve en «Sincro»: validada por las dos mitades = 60-100 %; si no, como mucho 30 %
    full["conf"] = 0.6 + 0.4 * full["conf"] if full["ok"] else min(full["conf"], 0.3)
    return full


def _read_region(vod, t0, dur, crop, should_stop):
    import numpy as np
    raw = b""
    for hw in (True, False):
        try:
            raw = _frames(vod, t0, dur, hw, should_stop, crop)
        except RuntimeError as e:
            if "Cancelado" in str(e):
                raise
            raw = b""
        if len(raw) >= GW * GH * 40:
            break
    n = len(raw) // (GW * GH)
    if n < 40:
        return None
    return np.frombuffer(raw[: n * GW * GH], dtype=np.uint8).reshape(n, GW * GH)


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
    results, saved = [], {}
    for region, (kind, crop) in REGIONS.items():
        ev = allk if kind == "todas" else mine
        if len(ev) < 5:
            continue
        if progress:
            progress(None, f"Analizando el {region} de {match.map_name}…")
        frames = _read_region(vod, t0, t1 - t0, crop, should_stop)
        if frames is None:
            continue
        saved[region] = frames
        r = analyze(frames, t0, expected_offset_s, ev)
        r["set"] = region
        results.append(r)
        if r["ok"] and r["conf"] >= 0.5:
            break                                   # claro: no hace falta mirar más
    if not results:
        return SyncResult(expected_offset_s, 0, 0, False, "no se pudo leer el vídeo")
    results.sort(key=lambda r: (r["ok"], r["conf"]), reverse=True)
    best = results[0]
    rep = ""
    if not best["ok"]:
        rep = save_report(saved, t0, expected_offset_s, allk, mine, match, results)
    _log(match, expected_offset_s, results)
    return SyncResult(expected_offset_s + best["shift"], best["shift"], best["conf"], best["ok"],
                      "" if best["ok"] else "no he encontrado un patrón claro de kills", rep)


def save_report(saved, t0, expected, allk, mine, match, results) -> str:
    """Informe pequeño (unos cientos de KB) para ajustar el detector con datos reales."""
    import numpy as np
    name = f"sync_{match.map_name}_{match.started_at.astimezone():%Y%m%d_%H%M}.npz"
    path = os.path.join(report_dir(), name)
    meta = {"t0": t0, "expected": expected, "fps": FPS, "grid": [GW, GH], "map": match.map_name,
            "match_id": match.match_id, "results": results,
            "regions": {k: v[1] for k, v in REGIONS.items()}}
    arrays = {f"frames_{k}": v for k, v in saved.items()}
    np.savez_compressed(path, allk=np.array(allk), mine=np.array(mine),
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
