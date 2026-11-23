"""
Descarga de VODs de Twitch en la máxima calidad con yt-dlp.
Si yt-dlp no está, se descarga solo (yt-dlp.exe junto al programa) y se
mantiene actualizado, porque Twitch cambia a menudo y las versiones viejas fallan.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

import core

APP_DIR = os.path.dirname(os.path.abspath(__file__))
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
STAMP = os.path.join(APP_DIR, ".ytdlp_updated")
VOD_RE = re.compile(r"twitch\.tv/(?:videos/|[^/]+/v(?:ideo)?/)(\d+)", re.I)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


def is_twitch_vod(url: str) -> bool:
    return bool(VOD_RE.search(url or ""))


def _exe() -> str | None:
    return core.find_tool("yt-dlp")


def ensure_ytdlp(progress=None) -> str:
    """Devuelve la ruta de yt-dlp; si no existe la descarga, y la actualiza 1 vez al día."""
    exe = _exe()
    if exe is None:
        if os.name != "nt":
            raise RuntimeError("Falta yt-dlp. Instálalo (pip install yt-dlp).")
        if progress:
            progress(0, "Descargando el descargador (yt-dlp)…")
        dst = os.path.join(APP_DIR, "yt-dlp.exe")
        req = urllib.request.Request(YTDLP_URL, headers={"User-Agent": "VortexsClips"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dst + ".part", "wb") as f:
            while True:
                b = r.read(1 << 16)
                if not b:
                    break
                f.write(b)
        os.replace(dst + ".part", dst)
        open(STAMP, "w").close()
        return dst
    try:
        old = time.time() - os.path.getmtime(STAMP) > 86400
    except OSError:
        old = True
    if old and exe.startswith(APP_DIR):
        if progress:
            progress(0, "Actualizando yt-dlp…")
        subprocess.run([exe, "-U"], capture_output=True, text=True, timeout=120, **core._no_window())
        open(STAMP, "w").close()
    return exe


def vod_info(url: str) -> dict:
    """Título, duración y hora exacta de inicio del directo (UTC)."""
    exe = ensure_ytdlp()
    r = subprocess.run([exe, "-J", "--no-warnings", "--no-playlist", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       **core._no_window())
    if r.returncode != 0:
        raise RuntimeError(_explain(r.stderr))
    j = json.loads(r.stdout)
    ts = j.get("timestamp")
    best = max((f for f in j.get("formats") or [] if f.get("height")),
               key=lambda f: (f.get("height") or 0, f.get("fps") or 0), default={})
    return {
        "id": j.get("id"),
        "title": j.get("title") or "",
        "duration": j.get("duration") or 0,
        "start": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
        "quality": f"{best.get('height', '?')}p{int(best['fps']) if best.get('fps') else ''}",
    }


def _explain(err: str) -> str:
    e = err or ""
    if "subscriber" in e.lower() or "sub-only" in e.lower() or "401" in e:
        return "Ese VOD es solo para suscriptores. Ponlo público un momento en Twitch o descárgalo a mano."
    if "404" in e or "does not exist" in e.lower():
        return "No encuentro ese VOD (¿está borrado o el enlace está mal?)."
    return "yt-dlp no pudo con el VOD:\n" + e.strip()[-600:]


def download(url: str, out_dir: str, progress=None, should_stop=None) -> str:
    """Descarga el VOD en la mejor calidad (normalmente 1080p60 'Source') y devuelve la ruta."""
    exe = ensure_ytdlp(progress)
    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [exe, "--no-playlist", "--newline", "--progress", "--no-part", "--windows-filenames",
           "-f", "b/bv*+ba", "-S", "res,fps,br", "--merge-output-format", "mp4",
           "--remux-video", "mp4", "-N", "8",
           "-o", os.path.join(out_dir, "%(upload_date>%Y-%m-%d)s %(title).80B [%(id)s].%(ext)s"),
           "--print", "after_move:filepath",
           "--progress-template", "download:PROG %(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s"]
    if ffmpeg:
        cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]
    cmd.append(url)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1, **core._no_window())
    final, tail = None, []
    try:
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            if should_stop and should_stop():
                p.terminate()
                raise RuntimeError("Descarga cancelada.")
            if line.startswith("PROG"):
                m = PCT_RE.search(line)
                if m and progress:
                    rest = line[4:].strip()
                    progress(float(m.group(1)) / 100, "Descargando VOD: " + " ".join(rest.split()))
            elif os.path.isabs(line) and os.path.splitext(line)[1]:
                final = line
            else:
                tail = (tail + [line])[-15:]
                if progress and ("Merging" in line or "Remux" in line or "Fixup" in line):
                    progress(1, "Preparando el MP4…")
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    if p.returncode != 0 or not final or not os.path.isfile(final):
        raise RuntimeError(_explain("\n".join(tail)))
    return final
