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
    part: int = 0      # en modo "solo kills", trozo dentro de la ronda
    parts: int = 1

    @property
    def group(self) -> tuple:
        return (self.match.match_id, self.round.index)

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
                r.start_match_ms = prev.start_match_ms + 130_000 * (i - prev.index)

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
    mode: str = "round"           # "round" = ronda completa · "kills" = solo las kills
    kill_pre_s: float = 2.5       # modo kills: segundos antes de cada kill
    kill_post_s: float = 1.5      # modo kills: segundos después de cada kill
    residual_s: float = 0.0       # con registro de delay de Vortexs Live: corrección fija aprendida


def delay_offset(m: "Match", settings: CutSettings, delay_fn=None) -> float:
    """Lo que hay que sumar a la hora para un partido sin sincronizar."""
    if delay_fn is not None:
        d = delay_fn(m.started_at)
        if d is not None:
            return d + settings.residual_s
    return settings.global_adjust_s


def match_offset_s(match: Match, vod_start_utc: datetime, calib: dict) -> float:
    """
    Segundo del VOD en el que está el instante 0 de la partida.
    calib[match_id] puede contener 'first_kill_vod_s' (momento del VOD de tu
    primera kill de esa partida). Si existe, manda sobre la hora.
    """
    c = calib.get(match.match_id) or {}
    if c.get("offset_s") is not None:            # sincronización automática
        return float(c["offset_s"])
    fk = c.get("first_kill_vod_s")
    first = match.first_my_kill_ms()
    if fk is not None and first is not None:
        return float(fk) - first / 1000.0
    return (match.started_at - vod_start_utc).total_seconds()


def is_synced(calib: dict, match_id: str) -> bool:
    c = calib.get(match_id) or {}
    return c.get("offset_s") is not None or c.get("first_kill_vod_s") is not None


def _kill_segments(m: Match, r: Round, base: float, settings: CutSettings,
                   vod_duration_s: float) -> list[tuple[float, float]]:
    """Modo solo kills: un trozo alrededor de cada kill tuya; las kills seguidas se juntan."""
    ts = sorted(base + k.t_match / 1000 for k in m.kills
                if k.round == r.index and k.killer == m.my_puuid)
    segs: list[list[float]] = []
    for t in ts:
        a, b = t - settings.kill_pre_s, t + settings.kill_post_s
        if segs and a <= segs[-1][1] + 0.05:
            segs[-1][1] = max(segs[-1][1], b)
        else:
            segs.append([a, b])
    out = []
    for a, b in segs:
        a = max(0.0, a)
        if vod_duration_s:
            b = min(vod_duration_s, b)
        if b - a >= 0.5:
            out.append((a, b))
    return out


def build_clips(matches: list[Match], vod_start_utc: datetime, vod_duration_s: float,
                settings: CutSettings, calib: dict | None = None, delay_fn=None) -> list[Clip]:
    calib = calib or {}
    clips: list[Clip] = []
    for m in matches:
        c = calib.get(m.match_id) or {}
        uses_calib = is_synced(calib, m.match_id)
        base = match_offset_s(m, vod_start_utc, calib)
        if not uses_calib:
            base += delay_offset(m, settings, delay_fn)
        base += float(c.get("extra_s", 0.0))
        for r in m.highlight_rounds(settings.min_kills):
            if r.start_match_ms is None:
                continue
            if settings.mode == "kills":
                segs = _kill_segments(m, r, base, settings, vod_duration_s)
                if segs and (segs[0][0] < (vod_duration_s or 1e12)) and segs[-1][1] > 0:
                    for i, (a, b) in enumerate(segs):
                        clips.append(Clip(m, r, a, b, i, len(segs)))
                continue
            start_round_ms = r.start_match_ms
            # El reloj de ronda de Riot empieza cuando bajan las barreras (tras la compra).
            if settings.skip_buy_phase:
                start_ms = start_round_ms - settings.pre_roll_s * 1000
            else:
                start_ms = start_round_ms - BUY_PHASE_MS - settings.pre_roll_s * 1000
            end_ms = start_round_ms + r.last_event_round_ms + settings.post_roll_s * 1000
            # no comerse la ronda siguiente
            nxt = next((x for x in m.rounds if x.index == r.index + 1 and x.start_match_ms), None)
            if nxt is not None:
                end_ms = min(end_ms, nxt.start_match_ms - BUY_PHASE_MS)
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
                 settings: CutSettings, calib: dict | None = None, delay_fn=None) -> bool:
    calib = calib or {}
    base = match_offset_s(m, vod_start_utc, calib)
    if not is_synced(calib, m.match_id):
        base += delay_offset(m, settings, delay_fn)
    end = base + (m.length_ms / 1000 if m.length_ms else 3000)
    return end > 0 and base < vod_duration_s


# ---------------------------------------------------------------- rutas
def app_dir() -> str:
    """Carpeta del programa (junto al .exe si está instalado, o junto a los .py)."""
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Carpeta con permisos de escritura para descargas (yt-dlp, transcriptor…)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VortexsClips")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- vídeo
def find_tool(name: str) -> str | None:
    here = app_dir()
    for cand in (os.path.join(here, "ffmpeg", "bin", name + ".exe"),
                 os.path.join(data_dir(), name + ".exe"),
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


def _has_audio(vod_path: str) -> bool:
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return True
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", vod_path],
                       capture_output=True, text=True, **_no_window())
    return bool(r.stdout.strip())


_ENC_CACHE: dict = {}
ENC_ORDER = ["nvenc", "amf", "qsv", "x264"]
ENC_NAMES = {"nvenc": "NVIDIA (NVENC)", "amf": "AMD (AMF)", "qsv": "Intel (QuickSync)", "x264": "CPU (x264)"}
# calidad objetivo; se limita al bitrate del original (x1,25) para no inflar el archivo
QUALITY = {"bitrate_kbps": None}


def encoder_works(enc: str) -> bool:
    if enc == "x264":
        return True
    if enc in _ENC_CACHE:
        return _ENC_CACHE[enc]
    ffmpeg = find_tool("ffmpeg")
    codec = {"nvenc": "h264_nvenc", "amf": "h264_amf", "qsv": "h264_qsv"}[enc]
    ok = False
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=black:s=1280x720:d=0.5", "-c:v", codec, "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60, **_no_window())
        ok = r.returncode == 0
    _ENC_CACHE[enc] = ok
    return ok


def best_encoder() -> str:
    """La gráfica primero (NVIDIA, AMD, Intel); si ninguna funciona, la CPU."""
    for e in ENC_ORDER:
        if encoder_works(e):
            return e
    return "x264"


def resolve_encoder(enc: str) -> str:
    if enc in (None, "", "auto"):
        return best_encoder()
    return enc if encoder_works(enc) else "x264"


def source_video_kbps(path: str) -> int | None:
    """Bitrate de vídeo del original (para no generar más grande ni peor que el directo)."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True, **_no_window())
    try:
        info = json.loads(r.stdout)
    except Exception:
        return None
    v = next((x for x in info.get("streams", []) if x.get("codec_type") == "video"), {})
    a = next((x for x in info.get("streams", []) if x.get("codec_type") == "audio"), {})
    br = v.get("bit_rate")
    if br and str(br).isdigit():
        return int(br) // 1000
    total = (info.get("format") or {}).get("bit_rate")
    if total and str(total).isdigit():
        ab = int(a.get("bit_rate") or 160000) if str(a.get("bit_rate") or "").isdigit() else 160000
        return max(1000, (int(total) - ab) // 1000)
    return None


def _venc(encoder: str) -> list[str]:
    encoder = resolve_encoder(encoder)
    src = QUALITY.get("bitrate_kbps")
    cap = []
    if src:
        mx = max(6000, int(src * 1.25))
        cap = ["-maxrate", f"{mx}k", "-bufsize", f"{mx * 2}k"]
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "19",
                "-b:v", "0", *cap, "-profile:v", "high"]
    if encoder == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "slower", "-global_quality", "19", *cap]
    if encoder == "amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "qvbr", "-qvbr_quality_level", "20", *cap]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *cap, "-profile:v", "high"]


BATCH = 20   # rondas por pasada de ffmpeg


def _render_batch(ffmpeg, vod_path, clips, out, venc, font, audio, done_before, total_s,
                  progress, should_stop):
    """
    Una sola pasada de ffmpeg: cada ronda es una entrada con búsqueda precisa (-ss/-t)
    y el filtro concat las une. concat alinea audio y vídeo de cada ronda (rellena
    con silencio si al audio le falta algún milisegundo), así que no hay desfase
    acumulado por muchas rondas que se junten.
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-nostats", "-progress", "pipe:1"]
    for c in clips:
        cmd += ["-ss", f"{c.vod_start:.3f}", "-t", f"{c.vod_end - c.vod_start:.3f}", "-i", vod_path]
    chains, labels = [], []
    for i, c in enumerate(clips):
        v = f"[{i}:v:0]setpts=PTS-STARTPTS"
        if font:
            v += (f",drawtext=fontfile='{_esc_filter_path(font)}':text='{_esc_text(c.label)}'"
                  ":fontcolor=0xFFD21F:fontsize=h/22:borderw=3:bordercolor=0x1a1a1a"
                  ":x=w/40:y=h/18:enable='lt(t,4)'")
        v += f",format=yuv420p[v{i}]"
        chains.append(v)
        labels.append(f"[v{i}]")
        if audio:
            dur = c.vod_end - c.vod_start
            chains.append(f"[{i}:a:0]asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,"
                          f"apad,atrim=0:{dur:.3f}[a{i}]")
            labels.append(f"[a{i}]")
    n = len(clips)
    chains.append("".join(labels) + f"concat=n={n}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]"]
    if audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += [*venc, "-movflags", "+faststart", out]

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **_no_window())
    err_lines = []

    def read_err():
        for line in p.stderr:
            err_lines.append(line)
    import threading
    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    for line in p.stdout:
        if should_stop and should_stop():
            p.kill()
            raise RuntimeError("Cancelado.")
        if line.startswith("out_time_us=") and progress and total_s:
            try:
                sec = int(line.split("=")[1]) / 1e6
            except ValueError:
                continue
            progress(min(1.0, (done_before + sec) / total_s))
    p.wait()
    t.join(timeout=2)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg falló:\n" + "".join(err_lines)[-800:])


def render(vod_path: str, clips: list[Clip], out_path: str, *, encoder: str = "x264",
           show_label: bool = False, progress=None, should_stop=None) -> str:
    """Recorta cada ronda y las une en un único vídeo con audio y vídeo sincronizados."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("No encuentro ffmpeg. Instala ffmpeg (ver README).")
    if not clips:
        raise RuntimeError("No hay rondas que cortar.")
    QUALITY["bitrate_kbps"] = source_video_kbps(vod_path)
    venc = _venc(encoder)
    font = _font_for_drawtext() if show_label else None
    audio = _has_audio(vod_path)
    total_s = sum(c.vod_end - c.vod_start for c in clips)
    batches = [clips[i:i + BATCH] for i in range(0, len(clips), BATCH)]

    def prog(frac):
        if progress:
            progress(frac, f"Generando vídeo… {frac * 100:.0f}%  ({len(clips)} rondas)")

    if len(batches) == 1:
        _render_batch(ffmpeg, vod_path, clips, out_path, venc, font, audio, 0, total_s, prog, should_stop)
    else:
        tmp = tempfile.mkdtemp(prefix="vortexsclips_")
        try:
            parts, done = [], 0.0
            for bi, b in enumerate(batches):
                part = os.path.join(tmp, f"part_{bi:03d}.mp4")
                _render_batch(ffmpeg, vod_path, b, part, venc, font, audio, done, total_s, prog, should_stop)
                done += sum(c.vod_end - c.vod_start for c in b)
                parts.append(part)
            if progress:
                progress(1.0, "Uniendo todo en un solo vídeo…")
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
            part = f" [trozo {c.part + 1}/{c.parts}]" if c.parts > 1 else ""
            f.write(f"{fmt_ts(t)} | {fmt_ts(c.vod_start)} - {fmt_ts(c.vod_end)} | "
                    f"{c.label}{part} ({c.match.agent}, {c.match.score})\n")
            t += c.vod_end - c.vod_start


# ---------------------------------------------------------------- vertical y clips sueltos
def cam_bottom_y(cam: dict | None, src_w: int = 1920, src_h: int = 1080) -> int:
    """Dónde acaba la cámara en el vídeo vertical (para poner ahí los subtítulos)."""
    if not cam:
        return 1180
    pw, ph = cam["w"] * src_w, cam["h"] * src_h
    out_h = int(round(520 * ph / max(pw, 1) / 2)) * 2 + 8
    return 140 + out_h


def vertical_filter(cam: dict | None, ass: str | None = None) -> str:
    """
    Estilo TikTok de Vortexs: juego recortado al centro a toda la altura (1080x1920)
    y la cámara encima, centrada arriba, con un borde oscuro fino.
    cam = {'x','y','w','h'} en fracciones (0..1) de la imagen original.
    """
    bg = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920:flags=lanczos,setsar=1"
    sub = f",ass={ass}" if ass else ""
    if not cam:
        return f"[0:v]{bg}{sub},format=yuv420p[v]"
    cx, cy, cw, ch = (float(cam[k]) for k in ("x", "y", "w", "h"))
    return (f"[0:v]split[a][b];[a]{bg}[bg];"
            f"[b]crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{cx:.4f}:ih*{cy:.4f},"
            f"scale=520:-2:flags=lanczos,pad=iw+8:ih+8:4:4:color=0x111111,setsar=1[cam];"
            f"[bg][cam]overlay=(W-w)/2:140{sub},format=yuv420p[v]")


def _run_ffmpeg(cmd: list[str], total_s: float, progress=None, should_stop=None, label="", cwd=None):
    p = subprocess.Popen(cmd + ["-nostats", "-progress", "pipe:1"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, cwd=cwd, **_no_window())
    err = []
    import threading
    th = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
    th.start()
    for line in p.stdout:
        if should_stop and should_stop():
            p.kill()
            raise RuntimeError("Cancelado.")
        if line.startswith("out_time_us=") and progress and total_s:
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / total_s), label)
            except ValueError:
                pass
    p.wait()
    th.join(timeout=2)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg falló:\n" + "".join(err)[-800:])


def make_vertical(src: str, out: str, cam: dict | None, encoder: str = "x264",
                  progress=None, should_stop=None, start: float | None = None,
                  dur: float | None = None, words: list | None = None) -> str:
    """words: palabras de la transcripción (tiempos del vídeo src) para los subtítulos."""
    ffmpeg = find_tool("ffmpeg")
    info = probe_vod(src)
    total = dur or float(info["duration"])
    tmp = tempfile.mkdtemp(prefix="vortexsclips_v_")
    try:
        ass = None
        if words:
            import subtitles
            y = cam_bottom_y(cam, info.get("width") or 1920, info.get("height") or 1080)
            if subtitles.build_ass(words, y, os.path.join(tmp, "subs.ass"),
                                   offset=start or 0.0, duration=total):
                ass = "subs.ass"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if start is not None:
            cmd += ["-ss", f"{start:.3f}"]
        if dur is not None:
            cmd += ["-t", f"{dur:.3f}"]
        cmd += ["-i", os.path.abspath(src), "-filter_complex", vertical_filter(cam, ass), "-map", "[v]",
                "-map", "0:a?", *_venc(encoder), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                os.path.abspath(out)]
        _run_ffmpeg(cmd, total, progress, should_stop, "Versión vertical…", cwd=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _safe(name: str) -> str:
    return "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip()


def export_singles(src: str, clips: list[Clip], folder: str, *, vertical_cam=None, vertical=False,
                   encoder: str = "x264", progress=None, should_stop=None, words=None) -> list[str]:
    """Un archivo por ronda, sacado del vídeo ya montado (rápido y con el mismo audio/vídeo)."""
    ffmpeg = find_tool("ffmpeg")
    os.makedirs(folder, exist_ok=True)
    # agrupar los trozos de una misma ronda (modo solo kills) en un único clip
    groups: list[tuple[Clip, float]] = []
    for c in clips:
        d = c.vod_end - c.vod_start
        if groups and groups[-1][0].group == c.group:
            groups[-1] = (groups[-1][0], groups[-1][1] + d)
        else:
            groups.append((c, d))
    outs, t = [], 0.0
    n = len(groups)
    for i, (c, d) in enumerate(groups):
        name = _safe(f"{i + 1:02d} - {c.label.replace(' · ', ' ')}")
        h = os.path.join(folder, name + ".mp4")
        if progress:
            progress(i / n, f"Clip suelto {i + 1} de {n}: {c.label}")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-t", f"{d:.3f}",
               "-i", src, "-map", "0:v:0", "-map", "0:a?", *_venc(encoder),
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", h]
        _run_ffmpeg(cmd, d, None, should_stop)
        outs.append(h)
        if vertical:
            v = os.path.join(folder, name + " (vertical).mp4")
            make_vertical(src, v, vertical_cam, encoder, None, should_stop, start=t, dur=d, words=words)
            outs.append(v)
        t += d
    if progress:
        progress(1.0, "Clips sueltos listos")
    return outs


def grab_frame(vod: str, at: float, out_png: str, width: int = 960) -> str:
    ffmpeg = find_tool("ffmpeg")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", vod,
                        "-frames:v", "1", "-vf", f"scale={width}:-2", out_png],
                       capture_output=True, text=True, **_no_window())
    if r.returncode != 0 or not os.path.isfile(out_png):
        raise RuntimeError("No se pudo sacar un fotograma del VOD:\n" + r.stderr[-300:])
    return out_png


# ---------------------------------------------------------------- delay de Vortexs Live
def delay_log_candidates() -> list[str]:
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, "VortexsLive", "delay_log.csv"))
            out.append(os.path.join(base, "VortexsLive", "delay_log.old.csv"))
    return out


def load_delay_log(path: str | None = None) -> list[tuple[float, float]]:
    """
    Registro que escribe Vortexs Live: una línea por cada cambio de delay
    'epoch_ms;delay_segundos' (también al empezar el directo). Devuelve [(epoch_s, delay_s)].
    """
    paths = [path] if path else delay_log_candidates()
    rows = []
    for p in paths:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    sep = ";" if ";" in line else ("\t" if "\t" in line else ",")
                    parts = line.split(sep)
                    if len(parts) < 2:
                        continue
                    try:
                        t = float(parts[0])
                        d = float(parts[1].replace(",", "."))
                    except ValueError:
                        continue
                    rows.append((t / 1000 if t > 1e11 else t, d))
    rows.sort()
    return rows


def make_delay_fn(rows: list[tuple[float, float]]):
    """Función fecha -> delay (s) que había en ese momento, o None si no hay registro cerca."""
    if not rows:
        return None
    import bisect
    times = [r[0] for r in rows]

    def at(t):
        i = bisect.bisect_right(times, t) - 1
        if i < 0 or t - times[i] > 14 * 3600:   # sin registro de ese directo
            return None
        return rows[i][1]

    def fn(dt: datetime):
        # Lo jugado en T se emite en W = T + delay(W): se busca ese W (2-3 vueltas bastan)
        t = dt.timestamp()
        d = at(t)
        if d is None:
            return None
        for _ in range(3):
            d2 = at(t + d)
            if d2 is None or abs(d2 - d) < 0.05:
                break
            d = d2
        return d
    return fn
