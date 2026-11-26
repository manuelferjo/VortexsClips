set -x
mkdir -p out
yt-dlp --flat-playlist -J "https://www.twitch.tv/vortexs_mc/videos?filter=archives&sort=time" > out/list.json || true
python3 - <<'PY' > out/vods.txt
import json,datetime
d=json.load(open("out/list.json"))
for e in d.get("entries",[]):
    ts=e.get("timestamp")
    print(e.get("id"), datetime.datetime.utcfromtimestamp(ts).isoformat() if ts else "", e.get("duration"), e.get("title"))
PY
cat out/vods.txt
