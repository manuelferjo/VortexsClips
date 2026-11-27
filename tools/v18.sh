set -x
mkdir -p out
cd app && python - <<'PY' > ../out/api20.txt 2>&1
import os, core
from datetime import datetime, timezone, timedelta
key=os.environ.get("HDEV_KEY","").strip()
print("key", bool(key))
vs=datetime(2026,11,20,17,29,7,tzinfo=timezone.utc)
lo,hi=vs-timedelta(hours=1),vs+timedelta(hours=16)
for rid in ["Vortexs#vrtxs","Sovadin#linup"]:
    for p in range(1,8):
        try:
            ids=core.fetch_stored_ids(rid,key,"eu",page=p)
        except Exception as e:
            print(rid,p,"ERR",e); break
        print(rid,p,len(ids), ids[0][1] if ids else None, ids[-1][1] if ids else None, sum(1 for _,w in ids if lo<=w<=hi))
        if not ids or min(w for _,w in ids)<lo: break
PY
