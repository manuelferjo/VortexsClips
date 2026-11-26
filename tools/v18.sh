set -x
mkdir -p out/hud
VOD=2931490150
for r in 11100-11590; do a=${r%-*}; b=${r#*-}
  yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/$VOD" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=4,crop=iw*0.30:ih*0.13:iw*0.35:ih*0.87" "out/hud/h${a}_%04d.png" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=4,crop=iw*0.16:ih*0.10:iw*0.42:ih*0.55" "out/hud/c${a}_%04d.png" || true
done
