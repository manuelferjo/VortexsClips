"""Vuelve a pasar los casos en los que no salió alguna línea tuya del killfeed.

Cada vez que al generar falta una línea, la app guarda en «killfeed sin línea/casos» los fotogramas del
killfeed de ese momento y lo que decían los datos (.npz + .json). Este script los busca otra vez con el
killfeed.py actual y dice cuáles ya salen: así cada fallo se convierte en una prueba.

Uso:  python tools/kf_casos.py <carpeta con casos> [más carpetas…]
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import killfeed as kf  # noqa: E402


def run(folder: str) -> tuple:
    ok = tot = 0
    for js in sorted(glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)):
        npz = js[:-5] + ".npz"
        if not os.path.isfile(npz):
            continue
        info = json.load(open(js, encoding="utf-8"))
        seq = list(np.load(npz)["seq"])
        cl = [tuple(x) for x in info["cl"]]
        g = tuple(info["group"])
        victim_of = {(g, round(c[0], 3)): tuple(v) for c, v in zip(cl, info["victims"])}
        log = []
        found, src = kf.locate(seq, info["t0"], cl, info["H"], [], info["fps"], None)
        found, src = kf._check_victims(found, src, seq, info["t0"], cl, info["H"], [], None, g,
                                       victim_of, info.get("enemies") or [], [], log)
        tot += len(cl)
        ok += len(found)
        mark = "OK " if len(found) == len(cl) else "MAL"
        print(f"{mark} {os.path.basename(js)[:-5]}: {len(found)}/{len(cl)}"
              + ("".join("\n      " + x for x in log) if log else ""))
    return ok, tot


if __name__ == "__main__":
    dirs = sys.argv[1:] or ["."]
    ok = tot = 0
    for d in dirs:
        a, b = run(d)
        ok += a
        tot += b
    print(f"\n{ok}/{tot} líneas salen ahora")
    sys.exit(0 if ok == tot else 1)
