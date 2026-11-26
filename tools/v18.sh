set -x
mkdir -p out/r4
VOD=2940460868
for r in 9528-9543; do a=${r%-*}; b=${r#*-}
  yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/$VOD" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=10,crop=iw*0.31:ih*0.27:iw*0.685:ih*0.035" "out/r4/s${a}_%03d.png" || true
done
