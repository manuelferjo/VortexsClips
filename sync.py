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
                  pair=None):
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
    full = _analyze_once(frames, t0, expected, events, search_s, pair)
    full["mode"] = mode
    if len(events) < 10:
        full["ok"] = False
        full["halves"] = None
        return full
    a = _analyze_once(frames, t0, expected, events[0::2], search_s, pair)
    b = _analyze_once(frames, t0, expected, events[1::2], search_s, pair)
    agree = abs(a["shift"] - full["shift"]) <= 1.0 and abs(b["shift"] - full["shift"]) <= 1.0
    full["halves"] = [a["shift"], b["shift"]]
    edge = abs(full["shift"]) >= search_s - 2          # pico pegado al borde: artefacto
    z, ratio = full["zpeak"], full["ratio"]
    if mode == "pareja":
        # medido con tu emisión real: z 6-7, ratio 1,40 y mitades de acuerdo; falsos z ≤4,2 y ratio ≤1,19
        ok = agree and z >= 5.2 and ratio >= 1.28
    elif mode == "cartel":
        # cartel solo (sin segundo texto): real z≈40 y ratio≈6,5; falsos z≤12 y ratio≤1,4
        ok = (z >= 20 and ratio >= 2.5) or (agree and z >= 14 and ratio >= 1.8)
    else:
        # killfeed: con mucho movimiento detrás, un patrón falso llegó a z 12 y ratio 2,3
        ok = agree and z >= 13 and ratio >= 2.5
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
    b0, ends = 0.0, []

    # 1) cartel WON/LOST: grande y opaco, una señal fuerte por ronda en toda la partida
    ends = sorted(r.end_match_ms / 1000.0 for r in match.rounds if r.end_match_ms)
    if len(ends) >= MIN_ENDS:
        if progress:
            progress(None, f"Leyendo la partida entera de {match.map_name} (carteles de fin de ronda)…")
        b0 = max(0.0, expected_offset_s + ends[0] - SEARCH_S - 5)
        b1 = expected_offset_s + ends[-1] + SEARCH_S + 8
        if vod_duration:
            b1 = min(vod_duration, b1)
        crop, gw, gh = BANNER
        frames = _read_region(vod, b0, b1 - b0, crop, should_stop, gw, gh)
        if frames is not None:
            saved["cartel"] = frames
            r = analyze(frames, b0, expected_offset_s, ends, kind="cartel")
            r["set"] = "cartel(" + r.get("mode", "") + ")"
            results.append(r)
            if r["ok"]:
                _log(match, expected_offset_s, results)
                return SyncResult(expected_offset_s + r["shift"], r["shift"], r["conf"], True, "", "",
                                  None)
    for region, (kind, crop, gw, gh) in REGIONS.items():
        ev = allk if kind == "todas" else mine
        if len(ev) < 5:
            continue
        if progress:
            progress(None, f"Analizando el {region} de {match.map_name}…")
        frames = _read_region(vod, t0, t1 - t0, crop, should_stop, gw, gh)
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
        full = _read_region(vod, t0, t1 - t0, "", should_stop, 32, 18)
        if full is not None:
            saved["completa"] = full
        rep = save_report(saved, t0, expected_offset_s, allk, mine, match, results)
    _log(match, expected_offset_s, results)
    data = {"expected": expected_offset_s}
    if "killfeed" in saved:
        data.update({"frames": saved["killfeed"], "t0": t0, "events": allk})
    if "cartel" in saved:
        data.update({"cartel": saved["cartel"], "b0": b0, "ends": ends})
    data["cands"] = [r["shift"] for r in results]
    return SyncResult(expected_offset_s + best["shift"], best["shift"], best["conf"], best["ok"],
                      "" if best["ok"] else "no he encontrado un patrón claro de kills", rep, data)


# ---------------------------------------------------------------- consenso entre partidas
LOCAL_S = 20.0


def consensus(results: dict, log_name: str = "") -> dict:
    """
    results: {match_id: SyncResult} de un mismo VOD. Las partidas que no se sincronizaron solas
    se afinan alrededor del desfase común (las del mismo directo se parecen mucho: mismo delay y
    casi la misma espera de selección de agente / carga).
    """
    import numpy as np
    good = [r for r in results.values() if r.ok]
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
        for cs in d.get("cands", []):
            if abs(cs - g) > 3 and all(abs(cs - x) > 2 for x in centers):
                centers.append(float(cs))
        del centers[4:]
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
            out[mid] = SyncResult(d["expected"] + g, g, 0.35, True, "", r.report, None, True)
            _log_line(f"{log_name} consenso ({how}): partida {mid[:8]} -> {g:+.2f}s aprox (desfase común)")
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
            shift, conf, approx = g, 0.35, True
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
