"""
Prueba real (se ejecuta en GitHub, no en tu PC): descarga el VOD de Twitch en calidad baja,
pide tus partidas a la API de HenrikDev, sincroniza cada una y deja un informe con:
  - resumen.txt: dónde ha colocado cada partida, con qué método y si la comprobación la confirma
  - killfeed_<mapa>_<hora>.png: el killfeed justo antes y después de kills tuyas (para revisarlo a ojo)
  - sincronizacion.txt e informes .npz del detector (para ajustar umbrales con datos reales)
Uso: python pruebas/prueba_real.py config.json salida/      (la clave en la variable HDEV_KEY)
"""
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import sync  # noqa: E402
import twitch  # noqa: E402


def log(out, msg):
    print(msg, flush=True)
    with open(os.path.join(out, "resumen.txt"), "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def main(cfg_path, out):
    os.makedirs(out, exist_ok=True)
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    key = os.environ.get("HDEV_KEY", "").strip()
    if not key:
        log(out, "FALTA LA CLAVE: añade el secreto HDEV_KEY en GitHub (Settings → Secrets and variables → Actions).")
        return 2
    url, riot, region = cfg["vod"], cfg["riot"], cfg.get("region", "eu")
    base_delay = float(cfg.get("desfase", 115))
    t0 = time.time()
    info = twitch.vod_info(url)
    log(out, f"VOD {info['id']} · {info['title']!r} · {info['duration'] / 3600:.1f} h · inicio {info['start']}")
    vod = os.path.join(out, "..", "vod.mp4")
    if not os.path.isfile(vod):
        exe = twitch.ensure_ytdlp()
        r = subprocess.run([exe, "-f", "bv*[height<=480]/b[height<=480]/worst", "-N", "16", "--no-part",
                            "-o", vod, url], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(vod):
            log(out, "No se pudo descargar el VOD:\n" + (r.stderr or r.stdout)[-1500:])
            return 3
    pv = core.probe_vod(vod)
    dur = pv["duration"]
    log(out, f"Descargado ({os.path.getsize(vod) / 1e9:.1f} GB, {pv['width']}x{pv['height']}) en "
             f"{(time.time() - t0) / 60:.1f} min")
    vs = info["start"]
    # partidas del directo
    st = core.CutSettings(global_adjust_s=base_delay)
    ms, pos, rawby = [], 0, {}
    for page in range(12):
        raws = core.fetch_recent_matches(riot, key, region, size=10, start=pos)
        pos += 10
        for raw in raws:
            try:
                mm = core.parse_match(raw, riot)
                ms.append(mm)
                rawby[mm.match_id] = raw
            except Exception:
                pass
        if not raws or (ms and min(m.started_at for m in ms) < vs):
            break
    inv = sorted((m for m in ms if core.match_in_vod(m, vs, dur, st, {}, None)), key=lambda m: m.started_at)
    log(out, f"{len(ms)} partidas leídas · {len(inv)} dentro del VOD")
    if not inv:
        return 4

    def run(delay):
        items = [(m, core.match_offset_s(m, vs, {}) + delay) for m in inv]
        t1 = time.time()
        res = sync.sync_many(vod, items, dur, 3)
        times = {m.match_id: m.started_at.timestamp() for m in inv}
        if any(not r.ok for r in res.values()) and len(res) > 1:
            res = sync.consensus(res, "prueba", times)
        return res, items, time.time() - t1

    res, items, secs = run(base_delay)
    shifts = sorted(r.shift_s for r in res.values() if r.ok and not r.approx and r.confidence >= 0.6)
    if shifts:
        learned = base_delay + shifts[len(shifts) // 2]
        log(out, f"Desfase base aprendido: {learned:.1f} s (punto de partida {base_delay:.0f} s)")
        if abs(learned - base_delay) > 8:
            log(out, "Repito con el desfase aprendido (como hace el programa la 2ª vez)…")
            res, items, secs = run(learned)
    log(out, f"Sincronización: {secs:.0f} s para {len(inv)} partidas")
    exp = dict((m.match_id, e) for m, e in items)
    log(out, "\nPARTIDA | RESULTADO | DESFASE vs ESPERADO | MÉTODO | COMPROBACIÓN")
    for m in inv:
        r = res[m.match_id]
        ck = sync.verify_match(vod, m, r.offset_s, dur) if r.ok else {"state": "-"}
        how = "aprox (común/vecina)" if r.approx else ("consenso" if r.confidence in (0.55, 0.65) else
                                                       next((x["set"] for x in (getattr(r, "methods", None) or [])
                                                             if abs(x["shift"] - r.shift_s) < 0.6), "auto"))
        log(out, f"{m.map_name:8s} {m.started_at.astimezone():%d/%m %H:%M} | "
                 f"{'OK  ' if r.ok else 'FALLA'} conf {r.confidence:.2f} | {r.shift_s:+7.2f} s | {how} | "
                 f"{ck['state']} (pico {ck.get('shift', 0):+.2f} s, z {ck.get('z', 0):.1f})")
        # imágenes del killfeed antes / después de kills tuyas
        mine = sorted((k for k in m.kills if k.killer == m.my_puuid), key=lambda k: k.t_match)[:3]
        if r.ok and mine:
            try:
                from PIL import Image, ImageDraw
                tiles = []
                for k in mine:
                    t = r.offset_s + k.t_match / 1000
                    for dt, lab in ((-1.0, "antes"), (1.5, "después")):
                        png = os.path.join(out, "_tmp.png")
                        core.grab_crop(vod, t + dt, png, sync.REGIONS["killfeed"][1], 320)
                        im = Image.open(png).convert("RGB")
                        canvas = Image.new("RGB", (im.width, im.height + 18), (20, 18, 26))
                        canvas.paste(im, (0, 18))
                        ImageDraw.Draw(canvas).text((4, 3), f"ronda {k.round + 1} {lab}", fill=(255, 210, 31))
                        tiles.append(canvas)
                w, h = tiles[0].width, tiles[0].height
                sheet = Image.new("RGB", (w * 2 + 6, (h + 6) * (len(tiles) // 2)), (11, 10, 15))
                for i, tl in enumerate(tiles):
                    sheet.paste(tl, ((i % 2) * (w + 6), (i // 2) * (h + 6)))
                sheet.save(os.path.join(out, f"killfeed_{m.map_name}_{m.started_at.astimezone():%H%M}.png"))
                os.remove(os.path.join(out, "_tmp.png"))
            except Exception as e:
                log(out, f"   (sin imágenes: {e})")
    # ---- datos para probar variantes del detector sin volver a descargar, e imágenes por candidato
    import numpy as np
    from PIL import Image, ImageDraw
    datos = os.path.join(out, "datos")
    os.makedirs(datos, exist_ok=True)
    for m in inv:
        r = res[m.match_id]
        e = exp[m.match_id]
        allk = sorted(k.t_match / 1000.0 for k in m.kills)
        ends = sorted(rr.end_match_ms / 1000.0 for rr in m.rounds if rr.end_match_ms)
        a = max(0.0, e + min(allk[0], ends[0] if ends else allk[0]) - 140)
        b = min(dur, e + max(allk[-1], ends[-1] if ends else 0) + 150)
        u0, arr = sync._read_all(vod, a, b - a, None, dur)
        tiles = {}
        for nm, _, gw, gh in sync.TILES:
            if nm == "completa":
                continue
            fr, tt = sync._tile(arr, u0, nm, a, b)
            tiles[nm] = fr
            tiles[nm + "_t0"] = np.array([tt])
        tag = f"{m.map_name}_{m.started_at.astimezone():%H%M}"
        np.savez_compressed(os.path.join(datos, f"{tag}.npz"), expected=np.array([e]), **tiles)
        with open(os.path.join(datos, f"{tag}.json"), "w", encoding="utf-8") as f:
            json.dump({"riot": riot, "raw": rawby.get(m.match_id)}, f)
        # imágenes: filas = candidatos (cada método + lo elegido), columnas = 2 kills antes/después
        cands = []
        for x in (getattr(r, "methods", None) or []):
            cands.append((x["shift"], x["set"]))
        cands.append((r.shift_s, "ELEGIDO"))
        uniq = []
        for sh, lab in sorted(cands):
            if uniq and abs(uniq[-1][0] - sh) < 0.75:
                uniq[-1] = (uniq[-1][0], uniq[-1][1] + "+" + lab)
            else:
                uniq.append((sh, lab))
        mine = sorted((k for k in m.kills if k.killer == m.my_puuid), key=lambda k: k.t_match)
        pick = [mine[len(mine) // 4], mine[(3 * len(mine)) // 4]] if len(mine) >= 4 else mine[:2]
        rows = []
        for sh, lab in uniq[:7]:
            tl = []
            for k in pick:
                t = e + sh + k.t_match / 1000
                for dt, cap in ((-1.0, "antes"), (1.5, "despues")):
                    png = os.path.join(out, "_c.png")
                    try:
                        core.grab_crop(vod, t + dt, png, sync.REGIONS["killfeed"][1], 240)
                        im = Image.open(png).convert("RGB")
                    except Exception:
                        im = Image.new("RGB", (240, 96), (40, 0, 0))
                    tl.append((im, f"r{k.round + 1} {cap}"))
            rows.append((sh, lab, tl))
        if rows and rows[0][2]:
            w, h = rows[0][2][0][0].width, rows[0][2][0][0].height
            sheet = Image.new("RGB", (190 + len(rows[0][2]) * (w + 4), len(rows) * (h + 20)), (11, 10, 15))
            d = ImageDraw.Draw(sheet)
            for ri, (sh, lab, tl) in enumerate(rows):
                y = ri * (h + 20)
                d.text((4, y + h // 2), f"{sh:+.2f}s", fill=(255, 210, 31))
                d.text((4, y + h // 2 + 14), lab[:30], fill=(199, 166, 255))
                for ci, (im, cap) in enumerate(tl):
                    x = 190 + ci * (w + 4)
                    sheet.paste(im, (x, y + 16))
                    d.text((x + 2, y + 2), cap, fill=(200, 200, 200))
            sheet.save(os.path.join(out, f"candidatos_{tag}.png"))
    if os.path.exists(os.path.join(out, "_c.png")):
        os.remove(os.path.join(out, "_c.png"))
    # informes del detector
    for f in os.listdir(sync.report_dir()):
        if f.endswith((".txt", ".npz")):
            shutil.copy2(os.path.join(sync.report_dir(), f), os.path.join(out, f))
    log(out, f"\nTotal: {(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
