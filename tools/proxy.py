"""¿Se puede sincronizar con la versión 480p30 de Twitch en vez de la original? Compara las miniaturas
que usa la sincronización en las dos versiones (mismos segundos del VOD): desfase, parecido y tiempo."""
import json, subprocess, sys, time
sys.path.insert(0, "app")
import numpy as np
import sync

vod, start, dur = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
url = f"https://www.twitch.tv/videos/{vod}"
fmts = json.loads(subprocess.run(["yt-dlp", "-J", url], capture_output=True, text=True).stdout)["formats"]
def pick(pred):
    c = [f for f in fmts if f.get("vcodec") not in (None, "none") and pred(f)]
    return max(c, key=lambda f: (f.get("height") or 0, f.get("fps") or 0)) if c else None
best = pick(lambda f: True)
small = pick(lambda f: (f.get("height") or 0) <= 480)
print("original:", best["format_id"], best.get("height"), best.get("fps"), "| pequeña:", small["format_id"], small.get("height"), small.get("fps"))
out = {}
for name, f in (("orig", best), ("480", small)):
    t = time.time()
    raw = sync._frames_all(f["url"], start, dur, False)
    el = time.time() - t
    a = np.frombuffer(raw, np.uint8).reshape(-1, sync.TILE_H, sync.TILE_W).astype(np.float32)
    out[name] = a
    print(name, "fotogramas", len(a), "tiempo", round(el, 1), "s", flush=True)
A, B = out["orig"], out["480"]
n = min(len(A), len(B)); A, B = A[:n], B[:n]
x0 = 0
for name, crop, gw, gh in sync.TILES:
    ta, tb = A[:, :gh, x0:x0 + gw], B[:, :gh, x0:x0 + gw]
    x0 += gw
    da = np.abs(np.diff(ta, axis=0)).mean(axis=(1, 2))
    db = np.abs(np.diff(tb, axis=0)).mean(axis=(1, 2))
    best_l, best_r = None, -9
    for lag in range(-8, 9):
        if lag >= 0:
            x, y = da[lag:], db[:len(db) - lag]
        else:
            x, y = da[:lag], db[-lag:]
        r = float(np.corrcoef(x, y)[0, 1])
        if r > best_r:
            best_r, best_l = r, lag
    mad = float(np.abs(ta - tb).mean())
    pix = float(np.corrcoef(ta.ravel(), tb.ravel())[0, 1])
    print(f"{name:9s} desfase {best_l / sync.FPS:+.2f} s  corr movimiento {best_r:.3f}  corr imagen {pix:.3f}  dif media {mad:.1f}/255")
