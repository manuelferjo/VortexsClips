set -x
mkdir -p out/r4
yt-dlp --flat-playlist -J "https://www.twitch.tv/vortexs_mc/videos?filter=archives&sort=time" > out/list.json || true
python3 - <<'PY' > out/vod.txt
import json
d=json.load(open("out/list.json"))
for e in d.get("entries",[])[:15]:
    print(e.get("id"), e.get("timestamp"), e.get("title"))
PY
cat out/vod.txt
VOD=$(python3 -c "
import json,datetime
d=json.load(open('out/list.json'))
for e in d.get('entries',[]):
    ts=e.get('timestamp')
    if ts and datetime.datetime.utcfromtimestamp(ts).strftime('%d')=='18': print(str(e['id']).lstrip('v')); break")
echo "VOD=$VOD" >> out/vod.txt
a=7598; b=7616
yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/$VOD" || true
ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=10,crop=iw*0.31:ih*0.27:iw*0.685:ih*0.035" "out/r4/s${a}_%03d.png" || true
