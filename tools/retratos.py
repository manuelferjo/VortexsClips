"""Añade a agentes/ los retratos del killfeed de los agentes nuevos (lo usa GitHub al publicar)."""
import json
import os
import urllib.request

H = {"User-Agent": "VortexsClips"}
url = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"
d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30))["data"]
os.makedirs("agentes", exist_ok=True)
n = 0
for a in d:
    u = a.get("killfeedPortrait")
    k = "".join(c for c in a["displayName"].lower() if c.isalnum())
    p = os.path.join("agentes", k + ".png")
    if u and not os.path.isfile(p):
        with open(p, "wb") as f:
            f.write(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30).read())
        n += 1
print("retratos nuevos:", n)
