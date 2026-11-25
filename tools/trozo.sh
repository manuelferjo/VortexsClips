set -x
mkdir -p out/r4
for r in "$@"; do a=${r%-*}; b=${r#*-}
  yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/2939461539" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=2,crop=iw*0.31:ih*0.27:iw*0.685:ih*0.035" "out/r4/s${a}_%03d.png" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=1/2,scale=640:-2" "out/full_${a}_%03d.png" || true
done
