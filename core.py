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
    result: str = ""                    # cómo acabó: eliminación, desactivada, explotó, tiempo…
    plant_round_ms: int | None = None
    defuse_round_ms: int | None = None
    end_match_ms: int | None = None     # momento EXACTO del final (para el cartel WON/LOST)
    clutch_vs: int = 0                  # te quedaste solo contra X enemigos vivos (0 = no)
    clutch_from_ms: int | None = None   # momento (ms de partida) en que te quedaste solo


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
    allies: set = field(default_factory=set)     # puuids de tu equipo (contigo)
    enemies: set = field(default_factory=set)

    def first_my_kill_ms(self) -> int | None:
        mine = [k.t_match for k in self.kills if k.killer == self.my_puuid]
        return min(mine) if mine else None

    def highlight_rounds(self, min_kills: int, clutch_min: int = 0) -> list[Round]:
        """Rondas con min_kills o más kills tuyas, o clutches ganados de 1 contra clutch_min o más."""
        return [r for r in self.rounds if r.my_kills >= min_kills or is_clutch(r, clutch_min)]


def is_clutch(r: "Round", clutch_min: int) -> bool:
    return bool(clutch_min) and bool(r.won) and r.clutch_vs >= clutch_min


def compute_clutches(m: "Match") -> None:
    """
    Para cada ronda: si en algún momento eras el único vivo de tu equipo, cuántos enemigos
    quedaban (1 contra X). Solo cuenta si la ronda la ganó tu equipo (lo comprueba is_clutch).
    """
    if not m.allies or not m.enemies or m.my_puuid not in m.allies:
        return
    by_round: dict[int, list[Kill]] = {}
    for k in m.kills:
        by_round.setdefault(k.round, []).append(k)
    for r in m.rounds:
        r.clutch_vs, r.clutch_from_ms = 0, None
        alive_a, alive_e = set(m.allies), set(m.enemies)
        for k in sorted(by_round.get(r.index, []), key=lambda x: x.t_match):
            if k.victim in alive_a:
                alive_a.discard(k.victim)
            elif k.victim in alive_e:
                alive_e.discard(k.victim)
            if alive_a == {m.my_puuid} and alive_e:
                r.clutch_vs, r.clutch_from_ms = len(alive_e), k.t_match
                break


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
    def kind(self) -> str:
        """'3K', '4K', 'ACE', '1v3', '4K 1v3'…"""
        r = self.round
        k = r.my_kills
        kname = {3: "3K", 4: "4K", 5: "ACE"}.get(k, f"{k}K")
        clutch = f"1v{r.clutch_vs}" if (r.won and r.clutch_vs >= 1) else ""
        if clutch and k < 3:
            return clutch
        return f"{kname} {clutch}".strip()

    @property
    def label(self) -> str:
        return f"{self.kind} · {self.match.map_name} · Ronda {self.round.index + 1}"


# ---------------------------------------------------------------- API
class ApiError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code          # None = sin conexión


# Las respuestas de la API se guardan en disco: una partida no cambia nunca, así que se lee
# de ahí sin gastar peticiones; y si la API se cae o limita, se usa lo último guardado.
API_STATUS = {"from_cache": False}
API_CACHE_MAX = 400


def _api_cache_path(url: str) -> str:
    import hashlib
    d = os.path.join(data_dir(), "api_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".json")


def _api_cache_read(url: str):
    try:
        with open(_api_cache_path(url), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _api_cache_write(url: str, data) -> None:
    try:
        p = _api_cache_path(url)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, p)
        d = os.path.dirname(p)
        files = sorted((os.path.join(d, x) for x in os.listdir(d) if x.endswith(".json")), key=os.path.getmtime)
        for old in files[:-API_CACHE_MAX]:
            os.remove(old)
    except OSError:
        pass


def _get(path: str, api_key: str, params: dict | None = None, cache: str = "fallback") -> dict:
    """cache: 'first' (lo guardado primero: partidas, que no cambian) o 'fallback' (solo si falla la API)."""
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    if cache == "first":
        hit = _api_cache_read(url)
        if hit is not None:
            return hit
    import time as _time
    data, err = None, None
    for attempt in range(3):
        try:
            data, err = _get_net(url, api_key), None
            break
        except ApiError as e:
            err = e
            # límite de peticiones, fallo del servidor o sin conexión: esperar y reintentar
            if (e.code == 429 or e.code is None or e.code >= 500) and attempt < 2:
                _time.sleep(3 * (attempt + 1))
                continue
            break
    if err is not None:
        # clave mala / no existe: error de verdad. Si no, usar lo último guardado
        if err.code not in (401, 403, 404):
            hit = _api_cache_read(url)
            if hit is not None:
                API_STATUS["from_cache"] = True
                return hit
        raise err
    _api_cache_write(url, data)
    return data


def _get_net(url: str, api_key: str) -> dict:
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
        raise ApiError(f"{msg}\n{body[:300]}", e.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ApiError(f"No se pudo conectar con la API: {getattr(e, 'reason', e)}", None) from None


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


def fetch_stored_ids(riot_id: str, api_key: str, region: str = "eu", page: int = 1, size: int = 20,
                     mode: str = "competitive") -> list[tuple[str, datetime]]:
    """Ids y fecha de partidas más antiguas (el historial guardado de HenrikDev, que sí pagina)."""
    name, tag = split_riot_id(riot_id)
    path = f"/valorant/v1/stored-matches/{region}/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}"
    data = _get(path, api_key, {"mode": mode, "page": page, "size": size})
    out = []
    for e in data.get("data") or []:
        meta = e.get("meta") or {}
        mid = meta.get("id") or e.get("match_id")
        if mid:
            out.append((mid, _parse_dt(meta.get("started_at") or meta.get("game_start"))))
    return out


def fetch_match(match_id_or_url: str, api_key: str, region: str = "eu") -> dict:
    """Acepta un id de partida o un enlace de tracker.gg con el id dentro."""
    m = UUID_RE.search(match_id_or_url)
    if not m:
        raise ValueError("No encuentro el id de la partida en ese texto/enlace.")
    data = _get(f"/valorant/v4/match/{region}/{m.group(0)}", api_key, cache="first")
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
    allies, enemies = set(), set()
    for p in players:
        t_ = p.get("team_id") or p.get("team")
        if not p.get("puuid") or t_ is None or my_team is None:
            continue
        (allies if str(t_).lower() == str(my_team).lower() else enemies).add(p["puuid"])
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
        rnd.result = str(rr.get("result") or rr.get("end_type") or "")
        for key, t_key, attr in (("plant", "round_time_in_ms", "plant_round_ms"),
                                 ("defuse", "round_time_in_ms", "defuse_round_ms"),
                                 ("plant_events", "plant_time_in_round", "plant_round_ms"),
                                 ("defuse_events", "defuse_time_in_round", "defuse_round_ms")):
            ev = rr.get(key)
            if isinstance(ev, dict) and ev.get(t_key):
                setattr(rnd, attr, int(ev[t_key]))
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

    # final exacto de cada ronda (lo que dispara el cartel WON/LOST)
    last_kill: dict[int, int] = {}
    for k in kills:
        last_kill[k.round] = max(last_kill.get(k.round, 0), k.t_match)
    for r in rounds:
        if r.start_match_ms is None:
            continue
        res = r.result.lower()
        if r.defuse_round_ms:
            r.end_match_ms = r.start_match_ms + r.defuse_round_ms
        elif "deton" in res and r.plant_round_ms:
            r.end_match_ms = r.start_match_ms + r.plant_round_ms + 45000
        elif "elimin" in res and r.index in last_kill:
            r.end_match_ms = last_kill[r.index]
        elif not res and r.index in last_kill and not r.plant_round_ms:
            r.end_match_ms = last_kill[r.index]   # sin dato de resultado: sin spike, acaba en la última muerte
        # tiempo agotado / rendición: sin momento exacto -> no se usa

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

    m_ = Match(
        match_id=md.get("match_id") or md.get("matchid") or "",
        map_name=map_name or "?",
        started_at=_parse_dt(started),
        length_ms=int(length) if length else 0,
        agent=agent or "",
        my_puuid=my_puuid,
        score=score,
        rounds=rounds,
        kills=kills,
        allies=allies,
        enemies=enemies,
    )
    compute_clutches(m_)
    return m_


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
    merge_gap_s: float = 4.0      # modo kills: si entre dos kills hay menos de esto, no se corta
    clutch_min: int = 2           # también clutches ganados de 1 contra X o más (0 = no)
    clutch_pre_s: float = 3.0     # clutch en modo kills: segundos antes de quedarte solo
    residual_s: float = 0.0       # con registro de delay de Vortexs Live: corrección fija aprendida
    cut_shift_s: float = 0.0      # ajuste fino de TODOS los cortes (− = antes, + = después)
    unsynced_extra_pre_s: float = 6.0   # modo kills: margen extra si la partida NO está sincronizada
    unsynced_extra_post_s: float = 4.0


def delay_offset(m: "Match", settings: CutSettings, delay_fn=None) -> float:
    """
    Lo que hay que sumar a la hora de Riot para una partida sin sincronizar:
    desfase base (selección de agente + carga, ~115 s) + el delay del directo si Vortexs Live
    lo dejó registrado. Sin registro, el campo «Desfase» debe incluir también el delay.
    """
    if delay_fn is not None:
        d = delay_fn(m.started_at)
        if d is not None:
            return settings.global_adjust_s + d
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
                   vod_duration_s: float, synced: bool = True) -> list[tuple[float, float]]:
    """Modo solo kills: un trozo alrededor de cada kill tuya; las kills seguidas se juntan.
    Si la partida no está sincronizada al segundo, se deja margen extra para no perder la jugada."""
    ts = sorted(base + k.t_match / 1000 for k in m.kills
                if k.round == r.index and k.killer == m.my_puuid)
    pre = settings.kill_pre_s + (0 if synced else settings.unsynced_extra_pre_s)
    post = settings.kill_post_s + (0 if synced else settings.unsynced_extra_post_s)
    segs: list[list[float]] = []
    last = None
    for t in ts:
        a, b = t - pre, t + post
        if segs and (a <= segs[-1][1] + 0.05 or (last is not None and t - last <= settings.merge_gap_s)):
            segs[-1][1] = max(segs[-1][1], b)
        else:
            segs.append([a, b])
        last = t
    out = []
    for a, b in segs:
        a = max(0.0, a)
        if vod_duration_s:
            b = min(vod_duration_s, b)
        if b - a >= 0.5:
            out.append((a, b))
    return out


def match_base_s(m: Match, vod_start_utc: datetime, settings: CutSettings, calib: dict | None = None,
                 delay_fn=None) -> float:
    """Segundo del VOD del instante 0 de la partida, tal y como se usa para cortar."""
    calib = calib or {}
    c = calib.get(m.match_id) or {}
    base = match_offset_s(m, vod_start_utc, calib)
    if not is_synced(calib, m.match_id):
        base += delay_offset(m, settings, delay_fn)
    return base + float(c.get("extra_s", 0.0)) + float(getattr(settings, "cut_shift_s", 0.0) or 0.0)


def _with_clutch(m: Match, r: Round, base: float, settings: CutSettings, vod_duration_s: float,
                 segs: list, synced: bool = True) -> list[tuple[float, float]]:
    """Clutch en modo kills: desde que te quedas solo hasta que acaba la ronda, sin cortes
    (los trozos de kills que caen dentro se funden con él)."""
    pre = settings.clutch_pre_s + (0 if synced else settings.unsynced_extra_pre_s)
    post = settings.kill_post_s + (0 if synced else settings.unsynced_extra_post_s)
    a = base + r.clutch_from_ms / 1000 - pre
    if r.end_match_ms:
        end_ms = r.end_match_ms
    else:
        end_ms = r.start_match_ms + r.last_event_round_ms
    b = base + end_ms / 1000 + post
    out = []
    for s_, e_ in sorted(list(segs) + [(a, b)]):
        if out and s_ <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], e_))
        else:
            out.append((s_, e_))
    res = []
    for s_, e_ in out:
        s_ = max(0.0, s_)
        if vod_duration_s:
            e_ = min(vod_duration_s, e_)
        if e_ - s_ >= 0.5:
            res.append((s_, e_))
    return res


def build_clips(matches: list[Match], vod_start_utc: datetime, vod_duration_s: float,
                settings: CutSettings, calib: dict | None = None, delay_fn=None) -> list[Clip]:
    calib = calib or {}
    clips: list[Clip] = []
    for m in matches:
        uses_calib = is_synced(calib, m.match_id)
        base = match_base_s(m, vod_start_utc, settings, calib, delay_fn)
        for r in m.highlight_rounds(settings.min_kills, settings.clutch_min):
            if r.start_match_ms is None:
                continue
            if settings.mode == "kills":
                c_ = calib.get(m.match_id) or {}
                precise = uses_calib and not c_.get("approx")
                segs = _kill_segments(m, r, base, settings, vod_duration_s, synced=precise) \
                    if r.my_kills >= settings.min_kills else []
                if is_clutch(r, settings.clutch_min) and r.clutch_from_ms is not None:
                    segs = _with_clutch(m, r, base, settings, vod_duration_s, segs, precise)
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


# ---------------------------------------------------------------- VOD por tramos
PARTS_SUFFIX = ".partes.json"


def is_parts(path: str) -> bool:
    return bool(path) and path.lower().endswith(PARTS_SUFFIX)


_PARTS_CACHE: dict = {}


def load_parts(path: str) -> dict:
    """
    Manifiesto de un VOD descargado por tramos (solo las partidas):
    {"duration": duración del VOD entero, "parts": [{"file", "start", "dur"}], ...}
    Todos los tiempos del programa siguen siendo los del VOD completo.
    """
    st = os.path.getmtime(path)
    hit = _PARTS_CACHE.get(path)
    if hit and hit[0] == st:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for p in m["parts"]:
        if not os.path.isabs(p["file"]):
            p["file"] = os.path.join(base, p["file"])
    m["parts"].sort(key=lambda p: p["start"])
    _PARTS_CACHE[path] = (st, m)
    return m


def vod_resolve(vod: str, t: float) -> tuple[str, float]:
    """(archivo, segundo dentro de ese archivo) para el segundo t del VOD."""
    if not is_parts(vod):
        return vod, max(0.0, t)
    parts = load_parts(vod)["parts"]
    best = None
    for p in parts:
        if p["start"] - 0.05 <= t < p["start"] + p["dur"]:
            return p["file"], max(0.0, t - p["start"])
        if p["start"] > t and best is None:
            best = p
    if best is not None:                 # en un hueco: lo siguiente que hay
        return best["file"], 0.0
    last = parts[-1]
    return last["file"], max(0.0, last["dur"] - 0.5)


def vod_piece(vod: str, start: float, dur: float) -> tuple[str, float, float, float]:
    """
    Para leer [start, start+dur] del VOD: (archivo, inicio dentro del archivo, duración, relleno),
    donde «relleno» son los segundos del principio que no están descargados (se rellenan en negro
    para que los tiempos sigan cuadrando).
    """
    if not is_parts(vod):
        return vod, max(0.0, start), dur, 0.0
    a, b = start, start + dur
    best, ov = None, -1.0
    for p in load_parts(vod)["parts"]:
        o = min(b, p["start"] + p["dur"]) - max(a, p["start"])
        if o > ov:
            best, ov = p, o
    s0 = max(a, best["start"])
    e0 = min(b, best["start"] + best["dur"])
    if e0 <= s0:
        return best["file"], 0.0, 0.0, 0.0
    return best["file"], s0 - best["start"], e0 - s0, s0 - a


def vod_pieces(vod: str, start: float, dur: float) -> list[tuple[str, float, float, float]]:
    """Todos los trozos descargados que tocan [start, start+dur]: [(archivo, inicio en el archivo,
    duración, segundo del VOD donde empieza)], en orden. Lo que no esté, son huecos."""
    if not is_parts(vod):
        return [(vod, max(0.0, start), dur, max(0.0, start))]
    a, b = start, start + dur
    out = []
    for p in load_parts(vod)["parts"]:
        s0, e0 = max(a, p["start"]), min(b, p["start"] + p["dur"])
        if e0 > s0:
            out.append((p["file"], s0 - p["start"], e0 - s0, s0))
    return out


def vod_part_start(vod: str, file: str) -> float:
    if not is_parts(vod):
        return 0.0
    for p in load_parts(vod)["parts"]:
        if os.path.normcase(os.path.abspath(p["file"])) == os.path.normcase(os.path.abspath(file)):
            return float(p["start"])
    return 0.0


def vod_display_base(vod: str) -> str:
    """Nombre base del VOD para los archivos que se generan (sin «.partes.json»)."""
    b = os.path.basename(vod)
    if is_parts(b):
        return b[: -len(PARTS_SUFFIX)]
    return os.path.splitext(b)[0]


def vod_first_file(vod: str) -> str:
    return load_parts(vod)["parts"][0]["file"] if is_parts(vod) else vod


def vod_covers(vod: str, a: float, b: float) -> bool:
    """¿Está ese tramo [a, b] dentro de lo descargado?"""
    if not is_parts(vod):
        return True
    return any(p["start"] - 0.5 <= a and b <= p["start"] + p["dur"] + 0.5 for p in load_parts(vod)["parts"])


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
    if is_parts(path):
        m = load_parts(path)
        info = _probe_file(m["parts"][0]["file"])
        info["duration"] = float(m["duration"])
        if m.get("start_utc"):
            info["start_guess"] = _parse_dt(m["start_utc"])
        info["parts"] = len(m["parts"])
        return info
    return _probe_file(path)


def _probe_file(path: str) -> dict:
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
    vod_path = vod_first_file(vod_path)
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
    path = vod_first_file(path)
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


BATCH = 8    # rondas por tanda de ffmpeg (cada tanda terminada se guarda: si se corta, se sigue)


def _dur_ok(path: str, expected: float, tol: float = 1.0) -> bool:
    """¿Existe ese vídeo y dura lo que debe? (para reaprovechar lo ya generado)"""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
        return abs(probe_vod(path)["duration"] - expected) <= tol
    except Exception:
        return False


def _sig_file(path: str) -> str:
    import hashlib
    d = os.path.join(data_dir(), "firmas")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(os.path.abspath(path).lower().encode("utf-8")).hexdigest()[:20] + ".txt")


def _sig_get(path: str) -> str:
    try:
        with open(_sig_file(path), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _sig_set(path: str, sig: str) -> None:
    try:
        with open(_sig_file(path), "w", encoding="utf-8") as f:
            f.write(sig)
    except OSError:
        pass


def _hash(*parts) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def estimate_bytes(vod_path: str, seconds: float) -> int:
    """Tamaño aproximado de un vídeo de esa duración con la calidad del directo."""
    kbps = source_video_kbps(vod_path) or 8000
    return int((min(kbps * 1.25, max(kbps, 6000)) + 192) * 1000 / 8 * seconds)


def _group_starts(clips) -> list[float]:
    """Segundo (dentro de la tanda) en que empieza cada jugada: ahí se fuerza un fotograma clave
    para poder cortar los clips sueltos después sin volver a codificar."""
    out, t, prev = [], 0.0, None
    for c in clips:
        if c.group != prev:
            out.append(t)
            prev = c.group
        t += c.vod_end - c.vod_start
    return out


def _kf_args(times: list[float], venc: list[str]) -> list[str]:
    ts = ",".join(f"{x:.3f}" for x in times if x > 0.01)
    if not ts:
        return []
    args = ["-force_key_frames", ts]
    if "h264_nvenc" in venc:
        args += ["-forced-idr", "1"]
    return args


def _render_batch(ffmpeg, vod_path, clips, out, venc, font, audio, done_before, total_s,
                  progress, should_stop, vout=None, cam=None, ass_path=None, vonly=False):
    """
    Una sola pasada de ffmpeg: cada ronda es una entrada con búsqueda precisa (-ss/-t)
    y el filtro concat las une. concat alinea audio y vídeo de cada ronda (rellena
    con silencio si al audio le falta algún milisegundo), así que no hay desfase
    acumulado por muchas rondas que se junten.
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-nostats", "-progress", "pipe:1"]
    for c in clips:
        f_, t_ = vod_resolve(vod_path, c.vod_start)
        cmd += ["-ss", f"{t_:.3f}", "-t", f"{c.vod_end - c.vod_start:.3f}", "-i", f_]
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
    kf = _kf_args(_group_starts(clips), venc)
    if vonly:
        # solo la vertical (p. ej. al corregir los subtítulos): una codificación desde el original
        chains.append("".join(labels) + f"concat=n={n}:v=1:a={1 if audio else 0}[vc]" + ("[a]" if audio else ""))
        vf = vertical_filter(cam, os.path.basename(ass_path) if ass_path else None).replace("[0:v]", "[vc]", 1)
        chains.append(vf)
        cmd += ["-filter_complex", ";".join(chains), "-map", "[v]"]
        if audio:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += [*venc, *kf, "-movflags", "+faststart", os.path.abspath(out)]
    elif vout:
        # una sola lectura del VOD: la misma imagen va a la horizontal y a la vertical,
        # cada una codificada UNA vez desde el original (sin pérdida extra)
        chains.append("".join(labels) + f"concat=n={n}:v=1:a={1 if audio else 0}[vc]" + ("[ac]" if audio else ""))
        chains.append("[vc]split[vh][vv0]")
        if audio:
            chains.append("[ac]asplit[a][av]")
        vf = vertical_filter(cam, os.path.basename(ass_path) if ass_path else None).replace("[0:v]", "[vv0]", 1)
        chains.append(vf.replace("[v]", "[vv]"))
        cmd += ["-filter_complex", ";".join(chains), "-map", "[vh]"]
        if audio:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += [*venc, *kf, "-movflags", "+faststart", os.path.abspath(out), "-map", "[vv]"]
        if audio:
            cmd += ["-map", "[av]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += [*venc, *kf, "-movflags", "+faststart", os.path.abspath(vout)]
    else:
        chains.append("".join(labels) + f"concat=n={n}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
        cmd += ["-filter_complex", ";".join(chains), "-map", "[v]"]
        if audio:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += [*venc, *kf, "-movflags", "+faststart", out]

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         cwd=os.path.dirname(ass_path) if ass_path and os.path.isabs(ass_path) else None,
                         **_no_window())
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
           show_label: bool = False, progress=None, should_stop=None,
           vout_path: str | None = None, cam: dict | None = None, words: list | None = None,
           highlight: bool = True, vertical_only: bool = False) -> str:
    """
    Recorta cada ronda y las une en un único vídeo con audio y vídeo sincronizados.
    Con vout_path, en la MISMA lectura del VOD genera también la vertical 9:16 (con subtítulos),
    codificada una sola vez desde el original. En las dos se fuerza un fotograma clave al
    principio de cada jugada, para sacar luego los clips sueltos sin volver a codificar.
    """
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("No encuentro ffmpeg. Instala ffmpeg (ver README).")
    if not clips:
        raise RuntimeError("No hay rondas que cortar.")
    if is_parts(vod_path):
        fuera = [c for c in clips if not vod_covers(vod_path, c.vod_start, c.vod_end)]
        if fuera:
            raise RuntimeError(f"{len(fuera)} ronda(s) (p. ej. {fuera[0].label}) caen fuera de los trozos que se "
                               "descargaron. Desmarca «Bajar solo los trozos con partidas» y vuelve a descargar "
                               "el VOD entero.")
    QUALITY["bitrate_kbps"] = source_video_kbps(vod_path)
    venc = _venc(encoder)
    font = _font_for_drawtext() if show_label else None
    audio = _has_audio(vod_path)
    total_s = sum(c.vod_end - c.vod_start for c in clips)
    batches = [clips[i:i + BATCH] for i in range(0, len(clips), BATCH)]
    vinfo = probe_vod(vod_path) if (vout_path or vertical_only) else {}

    def prog(frac):
        if progress:
            progress(frac, f"Generando vídeo{'s' if vout_path else ''}… {frac * 100:.0f}%  ({len(clips)} rondas)")

    vkey = (vout_path is not None, cam, words, highlight, vertical_only)
    sig = _hash(os.path.abspath(vod_path), [(round(c.vod_start, 3), round(c.vod_end, 3)) for c in clips],
                venc, bool(font), audio, vkey, 2)
    outs_ok = _sig_get(out_path) == sig and _dur_ok(out_path, total_s, 1.5)
    if vout_path:
        outs_ok = outs_ok and _sig_get(vout_path) == sig and _dur_ok(vout_path, total_s, 1.5)
    if outs_ok:
        if progress:
            progress(1.0, "Los vídeos ya estaban generados: los reaprovecho")
        if vertical_only:
            return out_path
        write_markers(clips, os.path.splitext(out_path)[0] + "_marcas.txt")
        return out_path
    _sig_set(out_path, "")
    if vout_path:
        _sig_set(vout_path, "")

    def batch_ass(tmpdir, b, offset, bdur, tag):
        if not ((vout_path or vertical_only) and words):
            return None
        import subtitles
        y = cam_bottom_y(cam, vinfo.get("width") or 1920, vinfo.get("height") or 1080)
        p = os.path.join(tmpdir, f"subs_{tag}.ass")
        n = subtitles.build_ass(words, y, p, offset=offset, duration=bdur, highlight=highlight)
        return p if n else None

    import hashlib
    # carpeta fija junto al vídeo: las tandas ya hechas se reaprovechan si se corta
    tmp = os.path.splitext(out_path)[0] + (" (vertical en curso)" if vertical_only else " (partes en curso)")
    os.makedirs(tmp, exist_ok=True)
    ok_ = False
    try:
        parts, vparts, done = [], [], 0.0
        for bi, b in enumerate(batches):
            key = ("|".join(f"{c.vod_start:.3f}-{c.vod_end:.3f}" for c in b) +
                   f"|{venc}|{bool(font)}|{audio}|{_hash(vkey, done)}")
            h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
            part = os.path.join(tmp, f"part_{bi:03d}_{h}.mp4")
            vpart = os.path.join(tmp, f"vert_{bi:03d}_{h}.mp4") if vout_path else None
            bdur = sum(c.vod_end - c.vod_start for c in b)
            if _dur_ok(part, bdur) and (not vpart or _dur_ok(vpart, bdur)):
                if progress:
                    progress((done + bdur) / total_s, f"Tanda {bi + 1} de {len(batches)} ya estaba hecha")
            else:
                tpart = part[:-4] + ".tmp.mp4"
                tvpart = vpart[:-4] + ".tmp.mp4" if vpart else None
                ass = batch_ass(tmp, b, done, bdur, f"{bi:03d}")
                _render_batch(ffmpeg, vod_path, b, tpart, venc, font, audio, done, total_s, prog, should_stop,
                              vout=tvpart, cam=cam, ass_path=ass, vonly=vertical_only)
                os.replace(tpart, part)
                if vpart:
                    os.replace(tvpart, vpart)
            done += bdur
            parts.append(part)
            if vpart:
                vparts.append(vpart)
        if progress:
            progress(1.0, "Uniendo todo…")
        for lst_parts, dst in ((parts, out_path), (vparts, vout_path)):
            if not dst:
                continue
            if len(lst_parts) == 1:
                shutil.copy2(lst_parts[0], dst + ".tmp.mp4")
                os.replace(dst + ".tmp.mp4", dst)
                continue
            lst = os.path.join(tmp, "list_" + ("v" if dst == vout_path else "h") + ".txt")
            with open(lst, "w", encoding="utf-8") as f:
                for p in lst_parts:
                    f.write("file '" + p.replace("\\", "/").replace("'", r"'\''") + "'\n")
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                                "-f", "concat", "-safe", "0", "-i", lst,
                                "-c", "copy", "-movflags", "+faststart", dst],
                               capture_output=True, text=True, **_no_window())
            if r.returncode != 0:
                raise RuntimeError("ffmpeg falló al unir:\n" + r.stderr[-800:])
        ok_ = True
    finally:
        if ok_:
            shutil.rmtree(tmp, ignore_errors=True)

    _sig_set(out_path, sig)
    if vout_path:
        _sig_set(vout_path, sig)
    if not vertical_only:
        write_markers(clips, os.path.splitext(out_path)[0] + "_marcas.txt")
    return out_path


def render_audio(vod_path: str, clips: list[Clip], out_audio: str, should_stop=None) -> str:
    """Solo el audio de las jugadas, unidas (para transcribir los subtítulos antes de generar)."""
    ffmpeg = find_tool("ffmpeg")
    if not _has_audio(vod_path):
        raise RuntimeError("El VOD no tiene audio.")
    tmp = tempfile.mkdtemp(prefix="vortexsclips_a_")
    try:
        pieces = []
        for bi in range(0, len(clips), BATCH):
            b = clips[bi:bi + BATCH]
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
            for c in b:
                f_, t_ = vod_resolve(vod_path, c.vod_start)
                cmd += ["-ss", f"{t_:.3f}", "-t", f"{c.vod_end - c.vod_start:.3f}", "-vn", "-i", f_]
            chains, labels = [], []
            for i, c in enumerate(b):
                dur = c.vod_end - c.vod_start
                chains.append(f"[{i}:a:0]asetpts=PTS-STARTPTS,aresample=16000:async=1:first_pts=0,"
                              f"apad,atrim=0:{dur:.3f}[a{i}]")
                labels.append(f"[a{i}]")
            chains.append("".join(labels) + f"concat=n={len(b)}:v=0:a=1[a]")
            piece = os.path.join(tmp, f"a_{bi:04d}.wav")
            cmd += ["-filter_complex", ";".join(chains), "-map", "[a]", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", piece]
            if should_stop and should_stop():
                raise RuntimeError("Cancelado.")
            r = subprocess.run(cmd, capture_output=True, text=True, **_no_window())
            if r.returncode != 0:
                raise RuntimeError("No se pudo sacar el audio:\n" + r.stderr[-500:])
            pieces.append(piece)
        if len(pieces) == 1:
            shutil.move(pieces[0], out_audio)
        else:
            lst = os.path.join(tmp, "l.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for p in pieces:
                    f.write("file '" + p.replace("\\", "/") + "'\n")
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                                "-i", lst, "-c", "copy", out_audio], capture_output=True, text=True, **_no_window())
            if r.returncode != 0:
                raise RuntimeError("No se pudo unir el audio:\n" + r.stderr[-500:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_audio


def write_markers(clips: list[Clip], path: str) -> None:
    """Lista de rondas con su minuto en el VOD original y en el vídeo final (para el editor)."""
    t = 0.0
    with open(path, "w", encoding="utf-8") as f:
        f.write("VIDEO FINAL | VOD ORIGINAL | RONDA | NOTA\n")
        for c in clips:
            part = f" [trozo {c.part + 1}/{c.parts}]" if c.parts > 1 else ""
            sc, why = clip_score(c)
            f.write(f"{fmt_ts(t)} | {fmt_ts(c.vod_start)} - {fmt_ts(c.vod_end)} | "
                    f"{c.label}{part} ({c.match.agent}, {c.match.score}) | {sc:g} ({', '.join(why)})\n")
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
    return (f"[0:v]split[vta][vtb];[vta]{bg}[vtbg];"
            f"[vtb]crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{cx:.4f}:ih*{cy:.4f},"
            f"scale=520:-2:flags=lanczos,pad=iw+8:ih+8:4:4:color=0x111111,setsar=1[vtcam];"
            f"[vtbg][vtcam]overlay=(W-w)/2:140{sub},format=yuv420p[v]")


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
                  dur: float | None = None, words: list | None = None, highlight: bool = True) -> str:
    """words: palabras de la transcripción (tiempos del vídeo src) para los subtítulos.
    highlight: algunas palabras en los colores del canal."""
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
                                   offset=start or 0.0, duration=total, highlight=highlight):
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


def kills_folder(kills: int) -> str:
    """Carpeta según las kills de la ronda: 3K, 4K o ACE (5 o más)."""
    if kills >= 5:
        return "ACE"
    return f"{kills}K"


def reaction_lu(src: str, start: float, dur: float) -> float | None:
    """
    Cuánto sube el volumen en el momento más fuerte del clip respecto a lo normal del clip
    (en LU/dB): tus gritos, risas o el «¡vamos!». Es el audio del directo mezclado, así que
    también cuentan los disparos: es una pista aproximada, no un detector de voz.
    """
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg or dur < 3:
        return None
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}",
                        "-t", f"{dur:.3f}", "-i", src, "-vn", "-af",
                        "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-", "-f", "null", "-"],
                       capture_output=True, text=True, **_no_window())
    vals = sorted(v for v in (float(x) for x in re.findall(r"lavfi\.r128\.M=(-?[\d.]+)", r.stdout or ""))
                  if v > -70)
    if len(vals) < 20:
        return None
    return vals[int(len(vals) * 0.97)] - vals[len(vals) // 2]


def clip_score(c: "Clip", react: float | None = None) -> tuple[float, list[str]]:
    """Nota de una jugada para saber cuál subir primero, con los motivos."""
    r, m = c.round, c.match
    pts, why = 0.0, []
    k = r.my_kills
    kp = {3: 3.0, 4: 6.0}.get(k, 10.0 if k >= 5 else 0.0)
    if kp:
        pts += kp
        why.append({3: "3K", 4: "4K"}.get(k, "ACE"))
    if r.won and r.clutch_vs >= 1:
        pts += 2.5 * r.clutch_vs
        why.append(f"clutch 1v{r.clutch_vs}")
    mine = sorted(x.t_match for x in m.kills if x.round == r.index and x.killer == m.my_puuid)
    if len(mine) >= 3:
        span = (mine[-1] - mine[0]) / 1000
        b = max(0.0, 4.0 - span / 3)
        if b >= 0.5:
            pts += b
            why.append(f"{len(mine)} kills en {span:.0f} s")
    if react is not None:
        rb = max(0.0, min(3.0, (react - 8) / 3))
        if rb >= 0.3:
            pts += rb
            why.append(f"reacción (+{react:.0f} dB)")
    if r.won:
        pts += 0.5
    return round(pts, 1), why


def clip_folder(r: Round) -> str:
    """3K / 4K / ACE por kills; si la ronda está por un clutch sin 3 kills, a la carpeta Clutch."""
    if r.my_kills >= 3 or not (r.won and r.clutch_vs >= 1):
        return kills_folder(r.my_kills)
    return "Clutch"


def _copy_cut(ffmpeg, src: str, t: float, d: float, out: str, should_stop=None) -> None:
    """Corte SIN volver a codificar (copia directa). El vídeo tiene un fotograma clave forzado al
    principio de cada jugada, así que el corte cae justo ahí."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t + 0.02:.3f}", "-t", f"{max(0.5, d - 0.06):.3f}",
           "-i", src, "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero",
           "-movflags", "+faststart", out]
    if should_stop and should_stop():
        raise RuntimeError("Cancelado.")
    r = subprocess.run(cmd, capture_output=True, text=True, **_no_window())
    if r.returncode != 0:
        raise RuntimeError("ffmpeg falló al cortar un clip:\n" + r.stderr[-500:])


def export_singles(src: str, clips: list[Clip], folder: str, *, vertical_cam=None, vertical=False,
                   encoder: str = "x264", progress=None, should_stop=None, words=None,
                   highlight: bool = True, ranking: list | None = None, top_n: int = 3,
                   vsrc: str | None = None, channel: str = "", video_texts: dict | None = None,
                   srt: bool = False) -> list[str]:
    """
    Un archivo por ronda, sacado del vídeo ya montado. Si src (y vsrc, la vertical) se generaron
    con fotogramas clave en cada jugada, se cortan COPIANDO: al instante y sin ninguna pérdida.
    """
    copy_mode = bool(_sig_get(src)) and (not vertical or (vsrc and os.path.isfile(vsrc)))
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
    rank = []
    pub = []
    # huella de lo que se genera: si coincide con la de antes, los clips ya hechos se reaprovechan
    ssig = _hash(_sig_get(src), [(round(c.vod_start, 3), round(c.vod_end, 3)) for c in clips], vertical,
                 vertical_cam, words, highlight, _venc(encoder), bool(vsrc))
    same = bool(_sig_get(src)) and _sig_get(folder) == ssig
    _sig_set(folder, ssig)
    for i, (c, d) in enumerate(groups):
        name = _safe(f"{i + 1:02d} - {c.label.replace(' · ', ' ')}")
        sub = os.path.join(folder, clip_folder(c.round))
        os.makedirs(sub, exist_ok=True)
        h = os.path.join(sub, name + ".mp4")
        if progress:
            progress(i / n, f"Clip suelto {i + 1} de {n}: {c.label}")
        def reuse(p):
            return same and _dur_ok(p, d, 0.6)
        if not reuse(h):
            if copy_mode:
                _copy_cut(ffmpeg, src, t, d, h + ".tmp.mp4", should_stop)
            else:
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-t", f"{d:.3f}",
                       "-i", src, "-map", "0:v:0", "-map", "0:a?", *_venc(encoder),
                       "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", h + ".tmp.mp4"]
                _run_ffmpeg(cmd, d, None, should_stop)
            os.replace(h + ".tmp.mp4", h)
        outs.append(h)
        best = h
        if vertical:
            v = os.path.join(sub, name + " (vertical).mp4")
            if not reuse(v):
                if copy_mode and vsrc:
                    _copy_cut(ffmpeg, vsrc, t, d, v + ".tmp.mp4", should_stop)
                else:
                    make_vertical(src, v + ".tmp.mp4", vertical_cam, encoder, None, should_stop, start=t, dur=d,
                                  words=None if srt else words, highlight=highlight)
                os.replace(v + ".tmp.mp4", v)
            outs.append(v)
            best = v
            if srt and words:
                # para CapCut: la vertical va sin subtítulos y al lado su .srt para editarlos allí
                try:
                    import subtitles
                    subtitles.write_srt(words, os.path.splitext(v)[0], offset=t, duration=d, highlight=highlight)
                except OSError:
                    pass
        try:
            react = reaction_lu(src, t, d)
        except Exception:
            react = None
        sc, why = clip_score(c, react)
        rank.append((sc, c.label, best, why))
        try:
            import publish
            tx = publish.clip_texts(c, publish.words_in(words, t, d), why, channel)
            publish.write_clip_txt(best, tx)
            pub.append({"file": h, "vertical": v if vertical else None, "label": c.label, "score": sc,
                        "texts": tx})
        except Exception:
            pass
        t += d
    if rank:
        rank.sort(key=lambda x: -x[0])
        try:
            with open(os.path.join(folder, "ranking.txt"), "w", encoding="utf-8") as f:
                f.write("NOTA | JUGADA | POR QUÉ | ARCHIVO\n")
                for sc, lab, p, why in rank:
                    f.write(f"{sc:g} | {lab} | {', '.join(why)} | {os.path.relpath(p, folder)}\n")
            topd = os.path.join(folder, "TOP")
            os.makedirs(topd, exist_ok=True)
            for j, (sc, lab, p, why) in enumerate(rank[:top_n]):
                dst = os.path.join(topd, f"{j + 1} - {os.path.basename(p)}")
                if os.path.exists(dst):
                    os.remove(dst)
                try:
                    os.link(p, dst)          # mismo archivo, sin ocupar más disco
                except OSError:
                    shutil.copy2(p, dst)
        except OSError:
            pass
        if ranking is not None:
            ranking.extend(rank)
    if pub:
        try:
            import publish
            publish.write_all(folder, video_texts, sorted(pub, key=lambda x: -x["score"]))
        except OSError:
            pass
    if progress:
        progress(1.0, "Clips sueltos listos")
    return outs


def grab_crop(vod: str, at: float, out_png: str, crop: str, width: int = 300) -> str:
    """Un fotograma recortado (p.ej. el killfeed) para revisar la sincronización."""
    ffmpeg = find_tool("ffmpeg")
    vf = (crop + "," if crop else "") + f"scale={width}:-2"
    vod, at = vod_resolve(vod, at)
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", vod,
                        "-frames:v", "1", "-vf", vf, out_png],
                       capture_output=True, text=True, **_no_window())
    if r.returncode != 0 or not os.path.isfile(out_png):
        raise RuntimeError("No se pudo sacar un fotograma del VOD:\n" + r.stderr[-300:])
    return out_png


def grab_frame(vod: str, at: float, out_png: str, width: int = 960) -> str:
    ffmpeg = find_tool("ffmpeg")
    vod, at = vod_resolve(vod, at)
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
