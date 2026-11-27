"""Iconos del killfeed de las armas y habilidades (para reconocer con qué es cada línea).
Lo usa GitHub al publicar: armas/<uuid del arma>.png y armas/<agente>_<ranura>.png, más armas/nombres.json."""
import json
import os
import sys
import urllib.request

H = {"User-Agent": "VortexsClips"}
dst = sys.argv[1] if len(sys.argv) > 1 else "armas"
os.makedirs(dst, exist_ok=True)


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40).read()


names = {}
for w in json.loads(get("https://valorant-api.com/v1/weapons"))["data"]:
    u = w.get("killStreamIcon") or w.get("displayIcon")
    if not u:
        continue
    k = w["uuid"].lower()
    names[k] = w["displayName"]
    p = os.path.join(dst, k + ".png")
    if not os.path.isfile(p):
        with open(p, "wb") as f:
            f.write(get(u))
for a in json.loads(get("https://valorant-api.com/v1/agents?isPlayableCharacter=true"))["data"]:
    ag = "".join(c for c in a["displayName"].lower() if c.isalnum())
    for ab in a.get("abilities") or []:
        slot = (ab.get("slot") or "").lower()
        u = ab.get("displayIcon")
        if not u or slot not in ("ability1", "ability2", "grenade", "ultimate"):
            continue
        k = f"{ag}_{slot}"
        names[k] = f"{a['displayName']} {ab.get('displayName')}"
        p = os.path.join(dst, k + ".png")
        if not os.path.isfile(p):
            with open(p, "wb") as f:
                f.write(get(u))
with open(os.path.join(dst, "nombres.json"), "w", encoding="utf-8") as f:
    json.dump(names, f, ensure_ascii=False, indent=1)
print("iconos:", len(names))
