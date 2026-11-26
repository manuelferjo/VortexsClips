set -x
mkdir -p out/r4 out/hud
VOD=2931490150
for r in 11596-11640; do a=${r%-*}; b=${r#*-}
  yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/$VOD" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=5,crop=iw*0.30:ih*0.13:iw*0.35:ih*0.87" "out/hud/h${a}_%03d.png" || true
  ffmpeg -loglevel error -ss 5 -i "t_$a.mp4" -frames:v 1 "out/full_${a}.png" || true
done
