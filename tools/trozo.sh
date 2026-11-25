set -x
VOD=$1; shift
mkdir -p out/r4
for r in "$@"; do a=${r%-*}; b=${r#*-}
  yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/$VOD" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=4,crop=iw*0.31:ih*0.27:iw*0.685:ih*0.035" "out/r4/s${a}_%03d.png" || true
  ffmpeg -loglevel error -ss 2 -i "t_$a.mp4" -frames:v 1 -vf "scale=960:-2" "out/full_${a}.png" || true
done
