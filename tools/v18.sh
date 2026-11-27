set -x
mkdir -p out/r4
get(){ VOD=$1; a=$2; b=$3
  yt-dlp -q -f "b/bv*" -S "res,fps" --download-sections "*$a-$b" -o "t_$a.mp4" "https://www.twitch.tv/videos/$VOD" || true
  ffmpeg -loglevel error -i "t_$a.mp4" -vf "fps=10,crop=iw*0.31:ih*0.27:iw*0.685:ih*0.035" "out/r4/s${a}_%03d.png" || true
}
get 2932490878 8608 8624
get 2933868132 14698 14724
get 2933868132 14455 14480
