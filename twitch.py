"""
Descarga de VODs de Twitch en la máxima calidad con yt-dlp.
Si yt-dlp no está, se descarga solo (yt-dlp.exe junto al programa) y se
mantiene actualizado, porque Twitch cambia a menudo y las versiones viejas fallan.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone

import core

APP_DIR = core.data_dir()   # carpeta con permisos de escritura
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
    if old and (exe.startswith(APP_DIR) or exe.startswith(core.app_dir())):
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
    dur = j.get("duration") or 0
    size = best.get("filesize") or best.get("filesize_approx")
    if not size and best.get("tbr") and dur:
        size = best["tbr"] * 1000 / 8 * dur
    return {
        "size_est": int(size) if size else 0,
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


def partial_bytes(out_dir: str, base: str) -> int:
    """Lo ya descargado de ese VOD (trozos .part de una descarga anterior)."""
    tot = 0
    try:
        for n in os.listdir(out_dir):
            if n.startswith(base) and (".part" in n or n.endswith((".ytdl", ".temp"))):
                try:
                    tot += os.path.getsize(os.path.join(out_dir, n))
                except OSError:
                    pass
    except OSError:
        pass
    return tot


def check_space(out_dir: str, need_bytes: int, what: str) -> str:
    """
    Error si no cabe; aviso (texto) si cabe pero va justo. need_bytes ya descontado lo que hay.
    """
    import shutil
    try:
        free = shutil.disk_usage(out_dir).free
    except OSError:
        return ""
    if need_bytes <= 0:
        return ""
    if free < need_bytes * 1.05 + 512 * 1024 ** 2:
        raise RuntimeError(f"No hay espacio en el disco para {what}: hacen falta unos {fmt_size(need_bytes)} "
                           f"y quedan {fmt_size(free)} libres en {out_dir}.\n"
                           "Libera espacio o elige otra carpeta (pestaña Salida → Carpeta…).")
    return ""


def download(url: str, out_dir: str, progress=None, should_stop=None, info: dict | None = None) -> str:
    """Descarga el VOD en la mejor calidad (normalmente 1080p60 'Source') y devuelve la ruta.
    Si se corta la conexión, reintenta y sigue por donde iba."""
    exe = ensure_ytdlp(progress)
    os.makedirs(out_dir, exist_ok=True)
    info = info or vod_info(url)
    base = base_name(info)
    target = os.path.join(out_dir, base + ".mp4")
    # un .mp4 con ese nombre que no está completo (si lo estuviera, find_existing lo habría usado):
    # se aparta para que los reintentos sigan desde los .part sin pisar ni saltarse nada
    if os.path.isfile(target):
        try:
            os.replace(target, target + ".incompleto")
        except OSError:
            pass
    est = int(info.get("size_est") or 0)
    if est:
        # al final yt-dlp rehace el MP4 (copia): hace falta sitio para casi otra copia
        check_space(out_dir, est * 2 - partial_bytes(out_dir, base), "descargar el VOD")
    ffmpeg = core.find_tool("ffmpeg")
    cmd = [exe, *_net_opts(), "--encoding", "utf-8", "--no-playlist", "--newline", "--progress",
           "--continue", "--no-overwrites",
           "-f", "b/bv*+ba", "-S", "res,fps,br", "--merge-output-format", "mp4",
           "--remux-video", "mp4", "-N", "8",
           "--retries", "30", "--fragment-retries", "60", "--retry-sleep", "fragment:exp=1:30",
           "--socket-timeout", "30",
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
    tail, last_prog, rc = [], "", 1
    for attempt in range(6):
        tail, last_prog, rc = _run_ytdlp(cmd, env, progress, should_stop)
        if rc == 0:
            break
        msg = "\n".join(tail).lower()
        fatal = any(x in msg for x in ("subscriber", "sub-only", "401", "404", "does not exist", "no space",
                                         "espacio", "certificate_verify_failed"))
        if fatal or attempt == 5:
            break
        wait = 10 * (attempt + 1)
        for k in range(wait):
            if should_stop and should_stop():
                raise RuntimeError("Descarga cancelada. Si vuelves a darle, sigue por donde iba.")
            if progress:
                progress(None, f"Se cortó la descarga. Reintento {attempt + 1} de 5 en {wait - k} s "
                               "(seguirá por donde iba)…")
            time.sleep(1)
    # localizar el archivo final por su nombre fijo, sin depender de lo que imprima yt-dlp
    found = None
    for ext in (".mp4", ".mkv", ".ts"):
        cand = os.path.join(out_dir, base + ext)
        if os.path.isfile(cand) and os.path.getsize(cand) > 0:
            found = cand
            break
    if rc == 0 and found:
        return found
    detail = "\n".join(tail) or f"(sin mensaje; código {rc}; último progreso: {last_prog or 'ninguno'})"
    raise RuntimeError(_explain(detail))


def _run_ytdlp(cmd, env, progress, should_stop):
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
    return tail, last_prog, p.returncode


# ---------------------------------------------------------------- vigilar el canal
def channel_name(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", text)
    return (m.group(1) if m else text.lstrip("@")).lower()


def is_live(channel: str) -> bool:
    """True si el canal está en directo ahora mismo."""
    exe = ensure_ytdlp()
    r = subprocess.run([exe, *_net_opts(), "--no-warnings", "-q", "--skip-download", "--print", "is_live",
                        f"https://www.twitch.tv/{channel}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=90, **core._no_window())
    return r.returncode == 0 and "True" in r.stdout


def latest_vods(channel: str, n: int = 5) -> list[dict]:
    """Últimos VODs (emisiones pasadas) del canal, del más nuevo al más viejo: [{'id','url','title'}]."""
    exe = ensure_ytdlp()
    url = f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time"
    r = subprocess.run([exe, *_net_opts(), "--no-warnings", "--flat-playlist", "-J",
                        "--playlist-end", str(n), url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, **core._no_window())
    if r.returncode != 0:
        raise RuntimeError(_explain(r.stderr))
    j = json.loads(r.stdout or "{}")
    out = []
    for e in j.get("entries") or []:
        vid = str(e.get("id") or "")
        if not vid:
            continue
        num = vid.lstrip("v")
        out.append({"id": vid, "url": e.get("url") or f"https://www.twitch.tv/videos/{num}",
                    "title": e.get("title") or ""})
    return out


def find_vod_urls(text: str) -> list[str]:
    """Todos los enlaces de VOD de un texto (para pegar varios a la vez)."""
    seen, out = set(), []
    for m in re.finditer(r"(?:https?://)?(?:www\.)?twitch\.tv/(?:videos/|[^/\s]+/v(?:ideo)?/)(\d+)", text or ""):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(f"https://www.twitch.tv/videos/{m.group(1)}")
    return out


# ---------------------------------------------------------------- descarga solo de las partidas
def playlist_url(url: str) -> str:
    """URL de la lista de reproducción (m3u8) de la mejor calidad del VOD."""
    exe = ensure_ytdlp()
    r = subprocess.run([exe, "-g", "--no-warnings", "--no-playlist", *_net_opts(),
                        "-f", "b/bv*", "-S", "res,fps,br", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       **core._no_window())
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(_explain(r.stderr))
    return r.stdout.strip().splitlines()[0]


def _fetch(url: str, timeout: float = 30) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VortexsClips"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read()


def parse_m3u8(text: str, base_url: str) -> dict:
    """Segmentos con su hora exacta dentro del VOD (suma de #EXTINF)."""
    import urllib.parse
    segs, t, dur, init = [], 0.0, None, None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = urllib.parse.urljoin(base_url, m.group(1))
        elif not line.startswith("#") and dur is not None:
            segs.append({"url": urllib.parse.urljoin(base_url, line), "start": t, "dur": dur,
                         "ext": os.path.splitext(urllib.parse.urlparse(line).path)[1] or ".ts"})
            t += dur
            dur = None
    if not segs:
        raise RuntimeError("La lista de Twitch no trae trozos de vídeo.")
    return {"segments": segs, "init": init, "duration": t}


def merge_sections(sections: list[tuple[float, float]], duration: float, join_gap: float = 300.0):
    """Ordena, recorta al VOD y junta tramos que se tocan o están a menos de join_gap."""
    out = []
    for a, b in sorted((max(0.0, a), min(duration, b)) for a, b in sections):
        if b <= a:
            continue
        if out and a <= out[-1][1] + join_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def download_sections(url: str, out_dir: str, sections: list[tuple[float, float]], progress=None,
                      should_stop=None, info: dict | None = None, m3u8: str | None = None) -> str:
    """
    Descarga SOLO los tramos indicados (segundos del VOD) y devuelve un manifiesto
    «<nombre>.partes.json». Los tiempos de todo el programa siguen siendo los del VOD entero.
    Si se corta, al repetir solo baja lo que falte.
    """
    import concurrent.futures as cf
    info = info or vod_info(url)
    base = base_name(info)
    os.makedirs(out_dir, exist_ok=True)
    if progress:
        progress(None, "Leyendo la lista de trozos del VOD…")
    m3u8 = m3u8 or playlist_url(url)
    pl = parse_m3u8(_fetch(m3u8).decode("utf-8", "replace"), m3u8)
    segs = pl["segments"]
    total_dur = float(info.get("duration") or pl["duration"])
    secs = merge_sections(sections, pl["duration"])
    if not secs:
        raise RuntimeError("No hay tramos que descargar.")
    pdir_name = base + " (partidas)"
    pdir = os.path.join(out_dir, pdir_name)
    os.makedirs(pdir, exist_ok=True)
    plan = []                                   # (tramo, [segmentos])
    for a, b in secs:
        chosen = [s for s in segs if s["start"] + s["dur"] > a and s["start"] < b]
        if chosen:
            plan.append(((a, b), chosen))
    need = sum(len(c) for _, c in plan)
    est = int(info.get("size_est") or 0)
    if est and pl["duration"]:
        frac = sum(sum(s["dur"] for s in c) for _, c in plan) / pl["duration"]
        check_space(out_dir, int(est * frac * 2.1), "descargar las partidas")
    ffmpeg = core.find_tool("ffmpeg")
    done = [0]
    nbytes = [0]
    t0 = time.time()
    lock = threading.Lock()

    def get(seg, path):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            with lock:
                done[0] += 1
            return
        last = None
        for attempt in range(8):
            if should_stop and should_stop():
                raise RuntimeError("Descarga cancelada. Si vuelves a darle, sigue por donde iba.")
            try:
                data = _fetch(seg["url"], 60)
                with open(path + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(path + ".tmp", path)
                with lock:
                    done[0] += 1
                    nbytes[0] += len(data)
                return
            except Exception as e:          # red: reintentar con espera creciente
                last = e
                time.sleep(min(30, 2 * (attempt + 1)))
        raise RuntimeError(f"No se pudo bajar un trozo del VOD: {last}")

    parts = []
    for k, ((a, b), chosen) in enumerate(plan):
        name = f"tramo_{k + 1:02d}_{int(chosen[0]['start'])}.mp4"
        final = os.path.join(pdir, name)
        p_start = chosen[0]["start"]
        p_dur = sum(s["dur"] for s in chosen)
        if os.path.isfile(final) and os.path.getsize(final) > 0:
            with lock:
                done[0] += len(chosen)
            parts.append({"file": os.path.join(pdir_name, name), "start": p_start, "dur": p_dur})
            continue
        sdir = os.path.join(pdir, f"trozos_{k + 1:02d}")
        os.makedirs(sdir, exist_ok=True)
        local = []
        for i, s in enumerate(chosen):
            local.append((s, os.path.join(sdir, f"s{i:05d}{s['ext']}")))
        init_local = None
        if pl["init"]:
            init_local = os.path.join(sdir, "init" + (os.path.splitext(pl["init"])[1] or ".mp4"))
            if not os.path.isfile(init_local):
                with open(init_local + ".tmp", "wb") as f:
                    f.write(_fetch(pl["init"], 60))
                os.replace(init_local + ".tmp", init_local)
        with cf.ThreadPoolExecutor(8) as ex:
            futs = [ex.submit(get, s, p) for s, p in local]
            while futs:
                fin, futs_set = cf.wait(futs, timeout=0.5)
                for f in fin:
                    f.result()
                futs = list(futs_set)
                if progress:
                    el = max(0.1, time.time() - t0)
                    frac = done[0] / max(1, need)
                    spd = nbytes[0] / el
                    eta = (el / frac - el) if frac > 0.02 else None
                    progress(frac, f"Partidas: trozo {done[0]} de {need}  ·  {fmt_size(nbytes[0])} a "
                                   f"{fmt_size(spd)}/s" + (f"  ·  quedan {fmt_eta(eta)}" if eta else ""))
                if should_stop and should_stop():
                    for f in futs:
                        f.cancel()
                    raise RuntimeError("Descarga cancelada. Si vuelves a darle, sigue por donde iba.")
        # unir los trozos de este tramo en un MP4 (copia, sin recodificar)
        lst = os.path.join(sdir, "lista.m3u8")
        with open(lst, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:%d\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    % int(max(s["dur"] for s in chosen) + 1))
            if init_local:
                f.write(f'#EXT-X-MAP:URI="{os.path.basename(init_local)}"\n')
            for s, p in local:
                f.write(f"#EXTINF:{s['dur']:.6f},\n{os.path.basename(p)}\n")
            f.write("#EXT-X-ENDLIST\n")
        if progress:
            progress(None, f"Uniendo el tramo {k + 1} de {len(plan)}…")
        tmpf = final[:-4] + ".tmp.mp4"
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-allowed_extensions", "ALL",
                            "-i", "lista.m3u8", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                            "-bsf:a", "aac_adtstoasc", "-avoid_negative_ts", "make_zero",
                            "-movflags", "+faststart", os.path.abspath(tmpf)],
                           capture_output=True, text=True, cwd=sdir, **core._no_window())
        if r.returncode != 0:
            raise RuntimeError("No se pudo unir un tramo del VOD:\n" + r.stderr[-500:])
        os.replace(tmpf, final)
        shutil.rmtree(sdir, ignore_errors=True)
        parts.append({"file": os.path.join(pdir_name, name), "start": p_start, "dur": p_dur})
    man = os.path.join(out_dir, base + core.PARTS_SUFFIX)
    old = {}
    if os.path.isfile(man):
        try:
            with open(man, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = {}
    have = {p["file"]: p for p in old.get("parts", []) if os.path.isfile(os.path.join(out_dir, p["file"]))}
    for p in parts:
        have[p["file"]] = p
    m = {"version": 1, "id": info.get("id"), "url": url, "title": info.get("title", ""),
         "duration": total_dur,
         "start_utc": info["start"].isoformat() if info.get("start") else None,
         "parts": sorted(have.values(), key=lambda p: p["start"])}
    with open(man + ".tmp", "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(man + ".tmp", man)
    return man


def find_parts(out_dir: str, info: dict, sections: list[tuple[float, float]] | None = None) -> str | None:
    """Un manifiesto ya descargado de ese VOD que cubra todos los tramos pedidos."""
    man = os.path.join(out_dir, base_name(info) + core.PARTS_SUFFIX)
    if not os.path.isfile(man):
        return None
    try:
        m = core.load_parts(man)
    except Exception:
        return None
    if any(not os.path.isfile(p["file"]) for p in m["parts"]):
        return None
    for a, b in sections or []:
        if not core.vod_covers(man, max(0.0, a), min(b, m["duration"])):
            return None
    return man
