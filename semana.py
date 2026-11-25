"""
Top de la semana: junta las mejores jugadas de los directos de una semana (lunes a domingo) en un
vídeo con cuenta atrás (#10 … #1), en horizontal para YouTube y en vertical para TikTok/Reels/Shorts.

Cada vez que se generan los clips sueltos de un VOD, sus jugadas (con su nota) se apuntan en un
historial. El top se monta con esos clips ya hechos (con sus subtítulos, en la vertical).
"""
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta

import core

_LOCK = threading.Lock()
TOP_N = 10


def history_path() -> str:
    return os.path.join(core.data_dir(), "historial_jugadas.json")


def load_history() -> list[dict]:
    with _LOCK:
        try:
            with open(history_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []


def record(folder: str, stream_day: datetime | None) -> int:
    """Tras sacar los clips sueltos de un VOD: apunta sus jugadas (lee «publicar.json» de la carpeta)."""
    try:
        with open(os.path.join(folder, "publicar.json"), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0
    day = (stream_day or datetime.now().astimezone()).astimezone().strftime("%Y-%m-%d")
    new = []
    for it in data.get("clips", []):
        h = os.path.normpath(os.path.join(folder, it["file"]))
        v = os.path.normpath(os.path.join(folder, it["vertical"])) if it.get("vertical") else None
        new.append({"day": day, "file": h, "vertical": v, "label": it.get("label", ""),
                    "score": float(it.get("score") or 0), "title": (it.get("texts") or {}).get("title", "")})
    with _LOCK:
        try:
            with open(history_path(), encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
        files = {e["file"] for e in new}
        hist = [e for e in hist if e["file"] not in files] + new
        cut = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        hist = [e for e in hist if e["day"] >= cut]
        with open(history_path() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
        os.replace(history_path() + ".tmp", history_path())
    return len(new)


def last_week(today: datetime | None = None) -> tuple[datetime, datetime]:
    """Lunes y domingo de la semana pasada (completa)."""
    today = (today or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7), this_monday - timedelta(days=1)


def week_key(monday: datetime) -> str:
    return monday.strftime("%Y-%m-%d")


def pick(monday: datetime, sunday: datetime, n: int = TOP_N, vertical: bool = False) -> list[dict]:
    """Las n mejores jugadas de esa semana cuyos archivos siguen ahí, de la peor a la mejor (cuenta atrás)."""
    a, b = monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")
    cand = [e for e in load_history() if a <= e["day"] <= b and os.path.isfile(e["file"])
            and (not vertical or (e.get("vertical") and os.path.isfile(e["vertical"])))]
    cand.sort(key=lambda e: (-e["score"], e["day"]))
    top = cand[:n]
    return list(reversed(top))


def _label_text(e: dict) -> str:
    # «4K · Bind · Ronda 7» -> «4K · Bind»
    parts = [p.strip() for p in (e.get("label") or "").split("·")]
    return " · ".join(p for p in parts[:2] if p)


def render_top(items: list[dict], out: str, vertical: bool = False, encoder: str = "auto", progress=None,
               should_stop=None) -> str:
    """Monta el vídeo: cada jugada con su puesto (#10 … #1) arriba durante los primeros segundos."""
    ffmpeg = core.find_tool("ffmpeg")
    if not items:
        raise RuntimeError("No hay jugadas para el top.")
    W, H = (1080, 1920) if vertical else (1920, 1080)
    key = "vertical" if vertical else "file"
    srcs = [e[key] for e in items]
    font = core._font_for_drawtext()
    durs = [max(0.5, core.probe_vod(p).get("duration") or 0) for p in srcs]
    core.QUALITY["bitrate_kbps"] = core.source_video_kbps(srcs[0])
    tmp = tempfile.mkdtemp(prefix="vortexsclips_top_")
    try:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for p in srcs:
            cmd += ["-i", p]
        chains, labels = [], []
        n = len(items)
        for i, e in enumerate(items):
            rank = n - i
            v = (f"[{i}:v:0]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,fps=60,setsar=1,format=yuv420p")
            if font:
                f1 = os.path.join(tmp, f"n{i}.txt")
                f2 = os.path.join(tmp, f"l{i}.txt")
                with open(f1, "w", encoding="utf-8") as f:
                    f.write(f"#{rank}")
                with open(f2, "w", encoding="utf-8") as f:
                    f.write(_label_text(e).upper())
                fe = core._esc_filter_path(font)
                show = "enable='lt(t,3.2)'"
                # la etiqueta va justo debajo del número (th sería la altura de la propia etiqueta)
                if vertical:
                    y1, y2, x = "h*0.68", "h*0.68+h/9*1.12", "(w-tw)/2"
                    s1, s2 = "h/9", "h/30"
                else:
                    y1, y2, x = "h*0.05", "h*0.05+h/6*1.12", "w*0.04"
                    s1, s2 = "h/6", "h/20"
                v += (f",drawtext=fontfile='{fe}':textfile='{core._esc_filter_path(f1)}':fontsize={s1}"
                      f":fontcolor=0xFFD21F:borderw=8:bordercolor=black:x={x}:y={y1}:{show}"
                      f",drawtext=fontfile='{fe}':textfile='{core._esc_filter_path(f2)}':fontsize={s2}"
                      f":fontcolor=0xC7A6FF:borderw=5:bordercolor=black:x={x}:y={y2}:{show}")
            chains.append(v + f"[v{i}]")
            chains.append(f"[{i}:a:0]aresample=48000:async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                          f"apad,atrim=0:{durs[i]:.3f}[a{i}]")
            labels.append(f"[v{i}][a{i}]")
        chains.append("".join(labels) + f"concat=n={n}:v=1:a=1[v][a]")
        cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
                *core._venc(encoder), "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart",
                out + ".tmp.mp4"]
        core._run_ffmpeg(cmd, sum(durs), progress, should_stop)
        os.replace(out + ".tmp.mp4", out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def texts(items: list[dict], monday: datetime, sunday: datetime, channel: str = "") -> str:
    n = len(items)
    title = f"TOP {n} jugadas de la semana ({monday:%d/%m} – {sunday:%d/%m}) | VALORANT"
    lines = [title, "", f"Las {n} mejores jugadas de mis directos de esta semana, de la #{n} a la #1.", "",
             "Capítulos:"]
    t = 0.0
    for i, e in enumerate(items):
        mm, ss = divmod(int(t), 60)
        lines.append(f"{mm}:{ss:02d} #{n - i} {_label_text(e)}")
        t += max(0.5, core.probe_vod(e["file"]).get("duration") or 0)
    lines.append("")
    if channel:
        lines.append(f"🔴 Directos en twitch.tv/{channel}")
    lines.append("📸 Instagram y TikTok: @vortexs_mc")
    lines.append("")
    lines.append("#valorant #valorantclips #top10 #valorantespaña #gaming")
    return "\n".join(lines)


def make_week(out_dir: str, monday: datetime, sunday: datetime, vertical: bool = True, encoder: str = "auto",
              progress=None, should_stop=None, channel: str = "", n: int = TOP_N) -> list[str]:
    """Genera el top de esa semana en «out_dir/Top semana dd-mm al dd-mm/». Devuelve los archivos."""
    items = pick(monday, sunday, n)
    if len(items) < 3:
        raise RuntimeError(f"Esa semana solo hay {len(items)} jugada(s) guardadas: hacen falta al menos 3.")
    folder = os.path.join(out_dir, f"Top semana {monday:%d-%m} al {sunday:%d-%m-%Y}")
    os.makedirs(folder, exist_ok=True)
    made = []
    h = os.path.join(folder, f"Top {len(items)} semana {monday:%d-%m} al {sunday:%d-%m}.mp4")
    render_top(items, h, False, encoder,
               (lambda f, t: progress(f * (0.5 if vertical else 1), "Top de la semana (horizontal)…"))
               if progress else None, should_stop)
    made.append(h)
    vitems = pick(monday, sunday, n, vertical=True) if vertical else []
    if vertical and len(vitems) >= 3:
        v = os.path.join(folder, f"Top {len(vitems)} semana {monday:%d-%m} al {sunday:%d-%m} (vertical).mp4")
        render_top(vitems, v, True, encoder,
                   (lambda f, t: progress(0.5 + f * 0.5, "Top de la semana (vertical)…")) if progress else None,
                   should_stop)
        made.append(v)
    with open(os.path.join(folder, "publicar.txt"), "w", encoding="utf-8") as f:
        f.write(texts(items, monday, sunday, channel) + "\n")
    return made
