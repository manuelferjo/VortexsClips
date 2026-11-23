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


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_size(b):
    if b is None:
        return "?"
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if b >= div:
            return f"{b / div:.1f} {unit}".replace(".", ",")
    return f"{int(b)} B"


def fmt_eta(sec):
    if sec is None:
        return "?"
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_progress(line: str):
    """'PROG dl total total_est speed eta frag_i frag_n' -> (fracción, texto) o None."""
    parts = line.split()
    if len(parts) < 8 or parts[0] != "PROG":
        return None
    dl, tot, est, speed, eta, fi, fn = (_num(x) for x in parts[1:8])
    total = tot or est
    if fi is not None and fn:
        frac = fi / fn                      # VOD de Twitch: por trozos, lo más fiable
        if dl and not total:
            total = dl / max(frac, 1e-6)
    elif dl is not None and total:
        frac = dl / total
    else:
        frac = None
    if frac is not None:
        frac = max(0.0, min(1.0, frac))
    bits = []
    if frac is not None:
        bits.append(f"{frac * 100:.1f} %".replace(".", ","))
    if dl is not None:
        bits.append(f"{fmt_size(dl)} de {fmt_size(total)}" if total else fmt_size(dl))
    if speed:
        bits.append(f"{fmt_size(speed)}/s")
    if eta is not None:
        bits.append(f"quedan {fmt_eta(eta)}")
    return frac, "  ·  ".join(bits)


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
            progress(None, "Preparando el descargador (solo la primera vez)…")
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
            progress(None, "Actualizando el descargador…")
        subprocess.run([exe, "-U"], capture_output=True, text=True, timeout=120, **core._no_window())
        open(STAMP, "w").close()
    return exe


def _net_opts() -> list[str]:
    # Usar los certificados de Windows en vez de la lista propia de yt-dlp.
    # Así funciona en redes que inspeccionan HTTPS (empresa, antivirus) sin desactivar la seguridad.
    return ["--compat-options", "no-certifi"] if os.name == "nt" else []


def vod_info(url: str) -> dict:
    """Título, duración y hora exacta de inicio del directo (UTC)."""
    exe = ensure_ytdlp()
    r = subprocess.run([exe, "-J", "--no-warnings", "--no-playlist", *_net_opts(), url],
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
    e = err or "(sin mensaje)"
    if "subscriber" in e.lower() or "sub-only" in e.lower() or "401" in e:
        return "Ese VOD es solo para suscriptores. Ponlo público un momento en Twitch o descárgalo a mano."
    if "CERTIFICATE_VERIFY_FAILED" in e:
        return ("La red bloquea la conexión segura con Twitch (certificado no reconocido).\n"
                "Prueba desde otra red (en casa) o descarga el VOD a mano y usa «Elegir VOD…».")
    if "404" in e or "does not exist" in e.lower():
        return "No encuentro ese VOD (¿está borrado o el enlace está mal?)."
    return "yt-dlp no pudo con el VOD:\n" + e.strip()[-600:]


def base_name(info: dict) -> str:
    """Nombre fijo y seguro para el archivo del VOD (sin tildes ni emojis)."""
    day = info["start"].astimezone().strftime("%Y-%m-%d") if info.get("start") else "VOD"
    return f"{day} Twitch {info['id']}"


def find_existing(out_dir: str, info: dict) -> str | None:
    """Busca en la carpeta un VOD ya descargado y COMPLETO de ese id (también con nombres antiguos)."""
    if not os.path.isdir(out_dir):
        return None
    vid = str(info["id"]).lstrip("v")
    need = (info.get("duration") or 0) - 120
    cands = []
    for n in os.listdir(out_dir):
        low = n.lower()
        if not low.endswith((".mp4", ".mkv", ".ts")) or low.endswith(".part"):
            continue
        if f"[{vid}]" in n or f"[v{vid}]" in n or f"twitch {vid}." in low or f"twitch v{vid}." in low:
            cands.append(os.path.join(out_dir, n))
    for p in sorted(cands, key=os.path.getsize, reverse=True):
        try:
            if core.probe_vod(p)["duration"] >= need:
                return p
        except Exception:
            continue
    return None


def download(url: str, out_dir: str, progress=None, should_stop=None, info: dict | None = None) -> str:
    """Descarga el VOD en la mejor calidad (normalmente 1080p60 'Source') y devuelve la ruta."""
    exe = ensure_ytdlp(progress)
    os.makedirs(out_dir, exist_ok=True)
    info = info or vod_info(url)
    base = base_name(info)
    target = os.path.join(out_dir, base + ".mp4")
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [exe, *_net_opts(), "--encoding", "utf-8", "--no-playlist", "--newline", "--progress",
           "--continue", "--force-overwrites" if os.path.isfile(target) else "--no-overwrites",
           "-f", "b/bv*+ba", "-S", "res,fps,br", "--merge-output-format", "mp4",
           "--remux-video", "mp4", "-N", "8",
           "-o", os.path.join(out_dir, base + ".%(ext)s"),
           "--progress-template",
           "download:PROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
           "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s "
           "%(progress.fragment_index)s %(progress.fragment_count)s"]
    if ffmpeg:
        cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]
    cmd.append(url)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if progress:
        progress(None, "Conectando con Twitch…")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1, env=env, **core._no_window())
    tail, last_prog = [], ""
    try:
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            if should_stop and should_stop():
                p.terminate()
                raise RuntimeError("Descarga cancelada. Si vuelves a darle, sigue por donde iba.")
            if line.startswith("PROG"):
                last_prog = line
                r = parse_progress(line)
                if r and progress:
                    progress(*r)
            else:
                tail = (tail + [line])[-15:]
                if progress and ("Merging" in line or "Remux" in line or "Fixup" in line):
                    progress(None, "Descarga completa. Preparando el MP4 (puede tardar unos minutos)…")
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    # localizar el archivo final por su nombre fijo, sin depender de lo que imprima yt-dlp
    found = None
    for ext in (".mp4", ".mkv", ".ts"):
        cand = os.path.join(out_dir, base + ext)
        if os.path.isfile(cand) and os.path.getsize(cand) > 0:
            found = cand
            break
    if p.returncode == 0 and found:
        return found
    detail = "\n".join(tail) or f"(sin mensaje; código {p.returncode}; último progreso: {last_prog or 'ninguno'})"
    raise RuntimeError(_explain(detail))
