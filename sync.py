"""
Sincronización automática de una partida con el VOD.

Idea: Riot nos da el segundo exacto de TODAS las kills de la partida. En el VOD,
cada kill hace aparecer una línea nueva en el killfeed (arriba a la derecha).
Medimos cuánto cambia la imagen en esa zona, fotograma a fotograma, y buscamos
el desplazamiento en el que los cambios coinciden con el patrón de kills.
No hace falta saber dónde está exactamente el killfeed: la zona se divide en
celdas y se quedan las que mejor siguen el patrón (la cámara o el juego no
siguen ese patrón, así que no engañan).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import core

FPS = 4                    # fotogramas por segundo analizados
W, H = 96, 48              # tamaño reducido de la zona analizada
CELL = 4                   # celdas de 4x4 px -> 24x12 = 288 celdas
CROP = "crop=iw*0.42:ih*0.40:iw*0.58:ih*0.02"   # cuarto superior derecho (killfeed)
SEARCH_S = 75.0            # se busca ±75 s alrededor de lo que dice la hora
USE_MATCH_S = 300.0        # se usan las kills de los primeros ~5 min de partida


@dataclass
class SyncResult:
    offset_s: float        # segundo del VOD en el que está el instante 0 de la partida
    shift_s: float         # cuánto se ha movido respecto a lo esperado
    confidence: float      # 0..1
    ok: bool
    reason: str = ""


def _frames(vod: str, start: float, dur: float, hwaccel: bool, should_stop=None):
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", "auto"]
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", vod, "-an",
            "-vf", f"fps={FPS},{CROP},scale={W}:{H}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **core._no_window())
    size = W * H
    frames = []
    try:
        while True:
            if should_stop and should_stop():
                p.kill()
                raise RuntimeError("Cancelado.")
            b = p.stdout.read(size)
            if len(b) < size:
                break
            frames.append(b)
    finally:
        if p.poll() is None:
            p.wait()
    if p.returncode not in (0, None) and not frames:
        raise RuntimeError("ffmpeg no pudo leer el VOD: " + p.stderr.read().decode("utf-8", "ignore")[-300:])
    return frames


def _cell_series(frames: list[bytes]) -> list[list[float]]:
    """Para cada celda, cuánto cambia su brillo medio entre fotogramas seguidos."""
    cw, ch = W // CELL, H // CELL
    n_cells = cw * ch
    means = []
    for fr in frames:
        sums = [0] * n_cells
        for y in range(H):
            row = fr[y * W:(y + 1) * W]
            base = (y // CELL) * cw
            for cx in range(cw):
                sums[base + cx] += sum(row[cx * CELL:(cx + 1) * CELL])
        means.append(sums)
    series = [[0.0] * len(frames) for _ in range(n_cells)]
    for i in range(1, len(means)):
        a, b = means[i - 1], means[i]
        for c in range(n_cells):
            series[c][i] = abs(b[c] - a[c])
    # normalizar cada celda (mediana / desviación robusta) y quedarse con los picos
    out = []
    for s in series:
        srt = sorted(s)
        med = srt[len(srt) // 2]
        mad = sorted(abs(x - med) for x in s)[len(s) // 2] or 1.0
        out.append([min(8.0, max(0.0, (x - med) / (mad * 3))) for x in s])
    return out


def _score(series: list[float], kills: list[float], shift: float, t0: float) -> float:
    """Suma de picos en los momentos en que deberían aparecer las kills (±1 fotograma)."""
    n = len(series)
    tot = 0.0
    for k in kills:
        i = int(round((k + shift - t0) * FPS))
        if 1 <= i < n - 1:
            tot += max(series[i - 1], series[i], series[i + 1])
    return tot


def sync_match(vod: str, match: core.Match, expected_offset_s: float, vod_duration: float,
               progress=None, should_stop=None) -> SyncResult:
    kills = sorted(k.t_match / 1000.0 for k in match.kills)
    if len(kills) < 8:
        return SyncResult(expected_offset_s, 0, 0, False, "pocas kills en la partida")
    kills = [k for k in kills if k <= kills[0] + USE_MATCH_S]
    # zona del VOD que hay que leer
    t0 = expected_offset_s + kills[0] - SEARCH_S - 3
    t1 = expected_offset_s + kills[-1] + SEARCH_S + 3
    t0 = max(0.0, t0)
    t1 = min(vod_duration, t1) if vod_duration else t1
    if t1 - t0 < 30:
        return SyncResult(expected_offset_s, 0, 0, False, "la partida no está en el VOD")
    if progress:
        progress(None, f"Analizando el killfeed de {match.map_name}…")
    try:
        frames = _frames(vod, t0, t1 - t0, True, should_stop)
        if len(frames) < 20:
            raise RuntimeError("pocos fotogramas")
    except RuntimeError as e:
        if "Cancelado" in str(e):
            raise
        frames = _frames(vod, t0, t1 - t0, False, should_stop)
    if len(frames) < 20:
        return SyncResult(expected_offset_s, 0, 0, False, "no se pudo leer el vídeo")
    series = _cell_series(frames)

    # 1) paso grueso (1 s) por celda; quedarse con las celdas que siguen el patrón
    coarse = [float(s) for s in range(int(-SEARCH_S), int(SEARCH_S) + 1)]
    cell_best = []
    for c, s in enumerate(series):
        if max(s) <= 0:
            continue
        sc = [_score(s, kills, expected_offset_s + d, t0) for d in coarse]
        avg = sum(sc) / len(sc) or 1e-6
        cell_best.append((max(sc) / avg, c))
    cell_best.sort(reverse=True)
    top = [c for _, c in cell_best[:10]]
    if not top:
        return SyncResult(expected_offset_s, 0, 0, False, "no se ve el killfeed")
    combo = [sum(series[c][i] for c in top) for i in range(len(frames))]

    # 2) paso fino (0,25 s) sobre la suma de las mejores celdas
    fine = [x / 4 for x in range(int(-SEARCH_S * 4), int(SEARCH_S * 4) + 1)]
    sc = [_score(combo, kills, expected_offset_s + d, t0) for d in fine]
    best_i = max(range(len(sc)), key=sc.__getitem__)
    best = sc[best_i]
    # las líneas del killfeed también "desaparecen" unos segundos después; ese eco da
    # un segundo pico MÁS TARDE. Si hay un pico casi igual hasta 10 s antes, el bueno es ese.
    for j in range(best_i - 1, max(-1, best_i - 41), -1):
        if sc[j] >= 0.8 * best and (j == 0 or sc[j] >= sc[j - 1]) and (j == len(sc) - 1 or sc[j] >= sc[j + 1]):
            best_i, best = j, sc[j]
            break
    shift = fine[best_i]
    # confianza: cuánto destaca el pico frente al resto (excluyendo ±12 s)
    others = sorted((v for i, v in enumerate(sc) if abs(fine[i] - shift) > 12), reverse=True)
    second = others[0] if others else 0.0
    mean = sum(sc) / len(sc) or 1e-6
    prominence = best / mean
    ratio = best / (second or 1e-6)
    confidence = max(0.0, min(1.0, (prominence - 1.5) / 2.5)) * max(0.0, min(1.0, (ratio - 1.0) / 0.5))
    ok = prominence >= 2.2 and ratio >= 1.25
    reason = "" if ok else "no he encontrado un patrón claro de kills"
    return SyncResult(expected_offset_s + shift, shift, confidence, ok, reason)
