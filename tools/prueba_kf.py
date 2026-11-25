"""Prueba del killfeed con trozos de otros VODs: detecta tus líneas, simula kills y mide el momento."""
import json, os, subprocess, sys
sys.path.insert(0, "app")
import numpy as np
from PIL import Image
import killfeed as kf

FPS = 5
OUT = "out"
os.makedirs(OUT, exist_ok=True)
ref = sys.argv[1]
info = json.loads(subprocess.run(["yt-dlp", "-J", "--skip-download", ref], capture_output=True, text=True).stdout or "{}")
chan = info.get("uploader_id") or info.get("channel_id") or info.get("uploader")
lst = json.loads(subprocess.run(["yt-dlp", "--flat-playlist", "-J",
                                 f"https://www.twitch.tv/{chan}/videos?filter=archives&sort=time"],
                                capture_output=True, text=True).stdout or "{}")
vods = [e for e in (lst.get("entries") or []) if e.get("id") and e["id"].lstrip("v") not in ref]
vods = vods[int(sys.argv[3]) if len(sys.argv) > 3 else 0:][:int(sys.argv[2])]
if len(sys.argv) > 4:
    vods = []
    for vid in sys.argv[4].split(","):
        j = json.loads(subprocess.run(["yt-dlp", "-J", "--skip-download", f"https://www.twitch.tv/videos/{vid}"],
                                      capture_output=True, text=True).stdout or "{}")
        vods.append({"id": vid, "duration": j.get("duration")})
print("canal", chan, "vods", [v.get("id") for v in vods], flush=True)
report = []


def frames_of(path):
    W, H = 1920, 1080
    cw, ch = int(W * kf.KF_W) // 2 * 2, int(H * kf.KF_H) // 2 * 2
    cx, cy = int(W * kf.KF_X), int(H * kf.KF_Y)
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-an", "-vf",
                        f"scale=1920:1080,fps={FPS},crop={cw}:{ch}:{cx}:{cy}", "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "pipe:1"], capture_output=True)
    sz = cw * ch * 3
    return [np.frombuffer(r.stdout[i * sz:(i + 1) * sz], np.uint8).reshape(ch, cw, 3) for i in range(len(r.stdout) // sz)]


def sheet(items, path):
    if not items:
        return
    ims = [Image.fromarray(x) for x in items[:140]]
    w = max(i.width for i in ims)
    h = sum(i.height + 3 for i in ims)
    m = Image.new("RGB", (w, h), (30, 30, 30))
    y = 0
    for i in ims:
        m.paste(i, (0, y))
        y += i.height + 3
    m = m.resize((w // 2, h // 2))
    m.save(path)


for v in vods:
    vid = v["id"].lstrip("v")
    dur = float(v.get("duration") or 0)
    if dur < 1200:
        continue
    own_imgs, other_imgs = [], []
    for frac in (0.2, 0.35, 0.5, 0.65, 0.8):
        a = int(dur * frac)
        p = f"t_{vid}_{a}.mp4"
        subprocess.run(["yt-dlp", "-q", "-f", "b/bv*", "-S", "res,fps", "--download-sections", f"*{a}-{a + 90}",
                        "-o", p, f"https://www.twitch.tv/videos/{vid}"])
        if not os.path.exists(p):
            report.append(f"{vid} {a}: no se pudo bajar")
            continue
        F = frames_of(p)
        os.remove(p)
        uniq = []          # [frame_i, (y0,y1,own), img, last_i, (y0,y1) último, img último]
        for i, f in enumerate(F):
            for y0, y1, mx in kf.lines(f, 1080):
                hit = None
                for u in uniq:
                    # la misma línea: igual que la última vez que se vio, o en el mismo sitio en el
                    # fotograma anterior (mientras hace su destello de entrada cambia de aspecto)
                    if kf.same_line(u[5], u[4][0], u[4][1], f, y0, y1) or \
                            (u[3] == i - 1 and abs(u[4][0] - y0) <= 4):
                        hit = u
                        break
                if hit:
                    hit[3], hit[4], hit[5] = i, (y0, y1), f
                    if mx is not None and not hit[1][2]:
                        hit[1] = (hit[1][0], hit[1][1], True)
                    continue
                uniq.append([i, (y0, y1, mx is not None), f, i, (y0, y1), f])
        owns = [u for u in uniq if u[1][2]]
        for u in uniq:
            (own_imgs if u[1][2] else other_imgs).append(u[5][u[4][0]:u[4][1]])
        # simulación: cada kill tuya 0,35 s después de que salga su línea; juntas si están a <0,7 s
        known, cl = [], []
        groups = []
        for u in owns:
            if cl and (u[0] - cl[-1][0]) / FPS > 0.7:
                groups.append(cl)
                cl = []
            cl.append(u)
        if cl:
            groups.append(cl)
        for g in groups:
            T = [u[0] / FPS + 0.35 + 0.01 * k for k, u in enumerate(g)]
            i0 = max(0, int(round((T[0] - 1.2) * FPS)))
            i1 = min(len(F), int(round((T[-1] + 1.0) * FPS)) + 1)
            seq = F[i0:i1]
            if len(seq) < 4:
                continue
            found, src = kf.locate(seq, i0 / FPS, [(t, 0) for t in T], 1080, known, FPS)
            got = {round(tv, 2): (box, ap) for (tv, _), box, ap in found}
            for u, t in zip(g, T):
                if round(t, 2) not in got:
                    report.append(f"{vid} {a}+{u[0] / FPS:.1f}s: FALTA (no se coloca)")
                    sheet([x for x in seq], f"{OUT}/falta_{vid}_{a}_{u[0]}.png")
                    continue
                box, ap = got[round(t, 2)]
                ok = kf.same_line(src, box[1], box[3], u[5], u[4][0], u[4][1])
                err = ap - (u[0] / FPS)
                report.append(f"{vid} {a}+{u[0] / FPS:.1f}s: {'OK ' if ok else 'MAL (otra línea)'} "
                              f"entra {err:+.2f}s")
                known.append((src, box[1], box[3]))
    sheet(own_imgs, f"{OUT}/{vid}_tuyas.png")
    sheet(other_imgs, f"{OUT}/{vid}_otras.png")
    print(vid, "tuyas", len(own_imgs), "otras", len(other_imgs), flush=True)
open(f"{OUT}/informe.txt", "w").write("\n".join(report) + "\n")
print("\n".join(report))
