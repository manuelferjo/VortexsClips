set -x
mkdir -p out
for v in 2931475172 2931490150 2930625736; do yt-dlp -J --skip-download "https://www.twitch.tv/videos/$v" | python3 -c "import json,sys;d=json.load(sys.stdin);print('$v',d.get('timestamp'),d.get('duration'),d.get('title'))" >> out/info.txt; done
VOD=2931490150
yt-dlp -q -f "b/bv*" -S "res:480" --download-sections "*300-1300" -o "t.mp4" "https://www.twitch.tv/videos/$VOD" || true
ffmpeg -loglevel error -i t.mp4 -vf "fps=1/10,scale=320:-2,tile=6x17" -frames:v 1 out/hoja.png || true
