"""
Vortexs Clips - núcleo
Descarga tus partidas de competitivo, encuentra las rondas con N kills o más
y las recorta del VOD en un único vídeo.

Datos: HenrikDev API (los mismos datos de Riot que usa tracker.gg).
Vídeo: ffmpeg / ffprobe.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

API_BASE = "https://api.henrikdev.xyz"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# Duración aproximada de la fase de compra en competitivo (ms)
BUY_PHASE_MS = 30_000
# Tiempo de "fin de ronda" que hay entre que acaba una ronda y empieza la siguiente (ms)
ROUND_END_GAP_MS = 7_000


# ---------------------------------------------------------------- modelos
@dataclass
class Kill:
    round: int
    t_match: int          # ms desde el inicio de la partida
    t_round: int          # ms desde el inicio de la ronda
    killer: str           # puuid
    victim: str
    weapon: str = ""


@dataclass
class Round:
    index: int                     # 0-based
    my_kills: int = 0
    start_match_ms: int | None = None   # inicio de la ronda dentro de la partida
    last_event_round_ms: int = 0        # último suceso (kill/plant/defuse) en ms de ronda
    won: bool | None = None


@dataclass
class Match:
    match_id: str
    map_name: str
    started_at: datetime            # UTC
    length_ms: int
    agent: str
    my_puuid: str
    score: str
    rounds: list[Round] = field(default_factory=list)
    kills: list[Kill] = field(default_factory=list)

    def first_my_kill_ms(self) -> int | None:
        mine = [k.t_match for k in self.kills if k.killer == self.my_puuid]
        return min(mine) if mine else None

    def highlight_rounds(self, min_kills: int) -> list[Round]:
        return [r for r in self.rounds if r.my_kills >= min_kills]


@dataclass
class Clip:
    match: Match
    round: Round
    vod_start: float   # segundos en el VOD
    vod_end: float

    @property
    def label(self) -> str:
        k = self.round.my_kills
        name = {3: "3K", 4: "4K", 5: "ACE"}.get(k, f"{k}K")
        return f"{name} · {self.match.map_name} · Ronda {self.round.index + 1}"


# ---------------------------------------------------------------- API
class ApiError(Exception):
    pass


def _get(path: str, api_key: str, params: dict | None = None) -> dict:
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": api_key.strip(),
        "User-Agent": "VortexsClips/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        msg = {
            401: "La clave de la API no es válida.",
            403: "La API ha rechazado la petición (¿clave correcta?).",
            404: "No se ha encontrado la cuenta o la partida.",
            429: "Demasiadas peticiones seguidas. Espera un minuto y vuelve a probar.",
        }.get(e.code, f"Error {e.code} de la API.")
        raise ApiError(f"{msg}\n{body[:300]}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"No se pudo conectar con la API: {e.reason}") from None


def split_riot_id(riot_id: str) -> tuple[str, str]:
    riot_id = urllib.parse.unquote(riot_id.strip())
    if "#" not in riot_id:
        raise ValueError("El Riot ID debe ser Nombre#TAG")
    name, tag = riot_id.rsplit("#", 1)
    return name.strip(), tag.strip()


def fetch_recent_matches(riot_id: str, api_key: str, region: str = "eu",
                         platform: str = "pc", size: int = 10, start: int = 0,
                         mode: str = "competitive") -> list[dict]:
    """Devuelve la lista de partidas en bruto (formato v4)."""
    name, tag = split_riot_id(riot_id)
    path = (f"/valorant/v4/matches/{region}/{platform}/"
            f"{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
    data = _get(path, api_key, {"mode": mode, "size": size, "start": start})
    return data.get("data") or []


def fetch_match(match_id_or_url: str, api_key: str, region: str = "eu") -> dict:
    """Acepta un id de partida o un enlace de tracker.gg con el id dentro."""
    m = UUID_RE.search(match_id_or_url)
    if not m:
        raise ValueError("No encuentro el id de la partida en ese texto/enlace.")
    data = _get(f"/valorant/v4/match/{region}/{m.group(0)}", api_key)
    return data.get("data") or {}


# ---------------------------------------------------------------- parseo
def _parse_dt(v) -> datetime:
    if isinstance(v, (int, float)):
        if v > 10**11:   # ms
            v = v / 1000
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_match(raw: dict, riot_id: str) -> Match:
    """Convierte una partida (v4, con compatibilidad v2/v3) al modelo interno."""
    name, tag = split_riot_id(riot_id)
    md = raw.get("metadata", {})

    # --- jugadores
    players = raw.get("players")
    if isinstance(players, dict):           # v2/v3
        players = players.get("all_players", [])
    players = players or []
    me = None
    for p in players:
        if p.get("name", "").lower() == name.lower() and p.get("tag", "").lower() == tag.lower():
            me = p
            break
    if me is None:
        raise ValueError(f"{name}#{tag} no aparece en la partida {md.get('match_id') or md.get('matchid')}")
    my_puuid = me["puuid"]
    my_team = me.get("team_id") or me.get("team")
    agent = (me.get("agent") or {}).get("name") if isinstance(me.get("agent"), dict) else me.get("character", "")

    map_name = md.get("map")
    if isinstance(map_name, dict):
        map_name = map_name.get("name", "")
    started = md.get("started_at") or md.get("game_start")
    length = md.get("game_length_in_ms") or md.get("game_length") or 0

    # --- marcador
    score = ""
    teams = raw.get("teams")
    if isinstance(teams, list):
        for t in teams:
            if t.get("team_id") == my_team:
                r = t.get("rounds", {})
                score = f"{r.get('won', '?')}-{r.get('lost', '?')}"
    elif isinstance(teams, dict) and my_team:
        t = teams.get(my_team.lower(), {})
        score = f"{t.get('rounds_won', '?')}-{t.get('rounds_lost', '?')}"

    # --- kills
    kills: list[Kill] = []
    for k in raw.get("kills") or []:
        if "time_in_match_in_ms" in k:   # v4
            kills.append(Kill(
                round=int(k.get("round", 0)),
                t_match=int(k["time_in_match_in_ms"]),
                t_round=int(k.get("time_in_round_in_ms", 0)),
                killer=(k.get("killer") or {}).get("puuid", ""),
                victim=(k.get("victim") or {}).get("puuid", ""),
                weapon=(k.get("weapon") or {}).get("name", "") or "",
            ))
        else:                            # v2/v3
            kills.append(Kill(
                round=int(k.get("round", 0)),
                t_match=int(k.get("kill_time_in_match", 0)),
                t_round=int(k.get("kill_time_in_round", 0)),
                killer=k.get("killer_puuid", ""),
                victim=k.get("victim_puuid", ""),
                weapon=k.get("damage_weapon_name", "") or "",
            ))

    # --- rondas
    raw_rounds = raw.get("rounds") or []
    n_rounds = max(len(raw_rounds), (max((k.round for k in kills), default=-1) + 1))
    rounds = [Round(index=i) for i in range(n_rounds)]

    for i, rr in enumerate(raw_rounds):
        rnd = rounds[i]
        win = rr.get("winning_team")
        if win and my_team:
            rnd.won = (str(win).lower() == str(my_team).lower())
        for ev_key, t_key in (("plant", "round_time_in_ms"), ("defuse", "round_time_in_ms")):
            ev = rr.get(ev_key)
            if isinstance(ev, dict) and ev.get(t_key):
                rnd.last_event_round_ms = max(rnd.last_event_round_ms, int(ev[t_key]))
        for ev_key, t_key in (("plant_events", "plant_time_in_round"), ("defuse_events", "defuse_time_in_round")):
            ev = rr.get(ev_key)
            if isinstance(ev, dict) and ev.get(t_key):
                rnd.last_event_round_ms = max(rnd.last_event_round_ms, int(ev[t_key]))

    # kills por ronda e inicio de ronda
    starts: dict[int, list[int]] = {}
    for k in kills:
        if k.round >= len(rounds):
            continue
        r = rounds[k.round]
        if k.killer == my_puuid:
            r.my_kills += 1
        r.last_event_round_ms = max(r.last_event_round_ms, k.t_round)
        starts.setdefault(k.round, []).append(k.t_match - k.t_round)
    for i, vals in starts.items():
        rounds[i].start_match_ms = int(statistics.median(vals))

    # rondas sin kills: estimar a partir de las vecinas
    for i, r in enumerate(rounds):
        if r.start_match_ms is None:
            prev = next((rounds[j] for j in range(i - 1, -1, -1) if rounds[j].start_match_ms is not None), None)
            if prev is not None:
                r.start_match_ms = prev.start_match_ms + 100_000 * (i - prev.index) + 30_000

    # si existe stats por ronda (v4) úsalo como fuente fiable del número de kills
    for i, rr in enumerate(raw_rounds):
        for st in rr.get("stats") or []:
            if (st.get("player") or {}).get("puuid") == my_puuid:
                kk = (st.get("stats") or {}).get("kills")
                if isinstance(kk, int):
                    rounds[i].my_kills = kk
        for st in rr.get("player_stats") or []:
            if st.get("player_puuid") == my_puuid and isinstance(st.get("kills"), int):
                rounds[i].my_kills = st["kills"]

    return Match(
        match_id=md.get("match_id") or md.get("matchid") or "",
        map_name=map_name or "?",
        started_at=_parse_dt(started),
        length_ms=int(length) if length else 0,
        agent=agent or "",
        my_puuid=my_puuid,
        score=score,
        rounds=rounds,
        kills=kills,
    )


# ---------------------------------------------------------------- tiempos
@dataclass
class CutSettings:
    min_kills: int = 3
    skip_buy_phase: bool = True
    pre_roll_s: float = 3.0     # segundos antes de que bajen las barreras
    post_roll_s: float = 4.0    # segundos tras el último suceso de la ronda
    global_adjust_s: float = 0.0  # delay del directo u otro desfase


def match_offset_s(match: Match, vod_start_utc: datetime, calib: dict) -> float:
    """
    Segundo del VOD en el que está el instante 0 de la partida.
    calib[match_id] puede contener 'first_kill_vod_s' (momento del VOD de tu
    primera kill de esa partida). Si existe, manda sobre la hora.
    """
    c = calib.get(match.match_id) or {}
    fk = c.get("first_kill_vod_s")
    first = match.first_my_kill_ms()
    if fk is not None and first is not None:
        return float(fk) - first / 1000.0
    return (match.started_at - vod_start_utc).total_seconds()


def build_clips(matches: list[Match], vod_start_utc: datetime, vod_duration_s: float,
                settings: CutSettings, calib: dict | None = None) -> list[Clip]:
    calib = calib or {}
    clips: list[Clip] = []
    for m in matches:
        c = calib.get(m.match_id) or {}
        uses_calib = c.get("first_kill_vod_s") is not None
        base = match_offset_s(m, vod_start_utc, calib)
        if not uses_calib:
            base += settings.global_adjust_s
        base += float(c.get("extra_s", 0.0))
        for r in m.highlight_rounds(settings.min_kills):
            if r.start_match_ms is None:
                continue
            start_round_ms = r.start_match_ms
            if settings.skip_buy_phase:
                start_ms = start_round_ms + BUY_PHASE_MS - settings.pre_roll_s * 1000
            else:
                start_ms = start_round_ms - settings.pre_roll_s * 1000
            end_ms = start_round_ms + r.last_event_round_ms + settings.post_roll_s * 1000
            # no comerse la ronda siguiente
            nxt = next((x for x in m.rounds if x.index == r.index + 1 and x.start_match_ms), None)
            if nxt is not None:
                end_ms = min(end_ms, nxt.start_match_ms - 500)
            s = base + start_ms / 1000
            e = base + end_ms / 1000
            if e <= 0 or (vod_duration_s and s >= vod_duration_s):
                continue
            s = max(0.0, s)
            if vod_duration_s:
                e = min(vod_duration_s, e)
            if e - s >= 2:
                clips.append(Clip(m, r, s, e))
    clips.sort(key=lambda c: c.vod_start)
    return clips


def match_in_vod(m: Match, vod_start_utc: datetime, vod_duration_s: float,
                 settings: CutSettings, calib: dict | None = None) -> bool:
    calib = calib or {}
    base = match_offset_s(m, vod_start_utc, calib)
    if (calib.get(m.match_id) or {}).get("first_kill_vod_s") is None:
        base += settings.global_adjust_s
    end = base + (m.length_ms / 1000 if m.length_ms else 3000)
    return end > 0 and base < vod_duration_s


# ---------------------------------------------------------------- vídeo
def find_tool(name: str) -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "ffmpeg", "bin", name + ".exe"),
                 os.path.join(here, "ffmpeg", name + ".exe"),
                 os.path.join(here, name + ".exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which(name)


def _no_window():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def probe_vod(path: str) -> dict:
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        raise RuntimeError("No encuentro ffprobe. Instala ffmpeg (ver README).")
    out = subprocess.run([ffprobe, "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", path],
                         capture_output=True, text=True, **_no_window())
    if out.returncode != 0:
        raise RuntimeError("No se pudo leer el vídeo:\n" + out.stderr[-500:])
    info = json.loads(out.stdout)
    fmt = info.get("format", {})
    dur = float(fmt.get("duration") or 0)
    created = (fmt.get("tags") or {}).get("creation_time")
    start_guess = None
    if created:
        try:
            start_guess = _parse_dt(created)
        except Exception:
            start_guess = None
    if start_guess is None:
        # fecha de modificación del archivo - duración ≈ inicio
        mtime = os.path.getmtime(path)
        start_guess = datetime.fromtimestamp(mtime - dur, tz=timezone.utc)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration": dur, "start_guess": start_guess,
            "width": v.get("width"), "height": v.get("height"),
            "fps": v.get("avg_frame_rate", "60/1")}


def fmt_ts(sec: float) -> str:
    sec = max(0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}" if h else f"{m:02d}:{s:05.2f}"


def parse_ts(text: str) -> float:
    """'1:23:45', '83:45', '5025' -> segundos"""
    text = text.strip().replace(",", ".")
    if not text:
        raise ValueError("vacío")
    parts = [float(p) for p in text.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _font_for_drawtext() -> str | None:
    cands = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _esc_filter_path(p: str) -> str:
    p = p.replace("\\", "/")
    return p.replace(":", r"\:").replace("'", r"\'")


def _esc_text(t: str) -> str:
    return (t.replace("\\", "\\\\").replace(":", r"\:").replace("'", "\u2019")
             .replace("%", r"\%").replace(",", r"\,"))


def render(vod_path: str, clips: list[Clip], out_path: str, *, encoder: str = "x264",
           show_label: bool = True, progress=None, should_stop=None) -> str:
    """Recorta cada ronda y las une en un único vídeo."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("No encuentro ffmpeg. Instala ffmpeg (ver README).")
    if not clips:
        raise RuntimeError("No hay rondas que cortar.")

    if encoder == "nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    elif encoder == "qsv":
        venc = ["-c:v", "h264_qsv", "-global_quality", "20"]
    elif encoder == "amf":
        venc = ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"]
    else:
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]

    font = _font_for_drawtext() if show_label else None
    tmp = tempfile.mkdtemp(prefix="vortexsclips_")
    parts = []
    total = len(clips)
    try:
        for i, c in enumerate(clips):
            if should_stop and should_stop():
                raise RuntimeError("Cancelado.")
            if progress:
                progress(i, total, f"Cortando {i + 1}/{total}: {c.label}")
            part = os.path.join(tmp, f"part_{i:03d}.mp4")
            vf = ["format=yuv420p"]
            if font:
                vf.insert(0,
                    f"drawtext=fontfile='{_esc_filter_path(font)}':text='{_esc_text(c.label)}'"
                    ":fontcolor=0xFFD21F:fontsize=h/22:borderw=3:bordercolor=0x1a1a1a"
                    ":x=w/40:y=h/18:enable='lt(t,4)'")
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", f"{c.vod_start:.3f}", "-i", vod_path,
                   "-t", f"{c.vod_end - c.vod_start:.3f}",
                   "-map", "0:v:0", "-map", "0:a:0?",
                   "-vf", ",".join(vf), *venc,
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                   "-video_track_timescale", "90000", "-avoid_negative_ts", "make_zero",
                   part]
            r = subprocess.run(cmd, capture_output=True, text=True, **_no_window())
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg falló en {c.label}:\n{r.stderr[-800:]}")
            parts.append(part)

        if progress:
            progress(total, total, "Uniendo todo en un solo vídeo…")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write("file '" + p.replace("\\", "/").replace("'", r"'\''") + "'\n")
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", "-movflags", "+faststart", out_path],
                           capture_output=True, text=True, **_no_window())
        if r.returncode != 0:
            raise RuntimeError("ffmpeg falló al unir:\n" + r.stderr[-800:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    write_markers(clips, os.path.splitext(out_path)[0] + "_marcas.txt")
    return out_path


def write_markers(clips: list[Clip], path: str) -> None:
    """Lista de rondas con su minuto en el VOD original y en el vídeo final (para el editor)."""
    t = 0.0
    with open(path, "w", encoding="utf-8") as f:
        f.write("VIDEO FINAL | VOD ORIGINAL | RONDA\n")
        for c in clips:
            f.write(f"{fmt_ts(t)} | {fmt_ts(c.vod_start)} - {fmt_ts(c.vod_end)} | "
                    f"{c.label} ({c.match.agent}, {c.match.score})\n")
            t += c.vod_end - c.vod_start
