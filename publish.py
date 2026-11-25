"""
Textos para publicar: título, descripción y hashtags de cada clip y del vídeo completo.
Se guardan junto a los vídeos:
  • «<clip>.txt» al lado de cada clip suelto (para copiar y pegar)
  • «publicar.txt» con todos, en el orden del ranking (el mejor primero)
  • «publicar.json» con lo mismo para programas de subida (Vortexs Post)
"""
import hashlib
import json
import os
import re

INSTAGRAM = "@vortexs_mc"
BASE_TAGS = ["#valorant", "#valorantclips"]
KIND_TAGS = {"3K": ["#3k"], "4K": ["#4k"], "ACE": ["#ace", "#valorantace"]}

TITLES = {
    "ACE": ["ACE con {agent} en {map}", "{agent} ACE en {map} 🔥", "No dejé a nadie vivo ({map})",
            "ACE limpio con {agent}", "5 de 5 en {map} con {agent}"],
    "4K": ["4K con {agent} en {map}", "Casi ACE con {agent} 😤", "4 kills seguidas en {map}",
           "{agent} 4K en {map}"],
    "3K": ["3K con {agent} en {map}", "Triple con {agent} en {map}", "{agent} se lleva a 3 en {map}",
           "3 de golpe en {map}"],
    "CLUTCH": ["Clutch 1v{n} con {agent} en {map}", "1v{n} y ronda para casa ({map})",
               "Me quedé solo contra {n} ({agent})"],
}


def _pick(options: list[str], seed: str) -> str:
    h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16)
    return options[h % len(options)]


def _tag(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-ZáéíóúñÁÉÍÓÚÑ]", "", s or "").lower()
    return "#" + s if s else ""


def best_phrase(words: list[dict] | None, max_words: int = 7) -> str:
    """La frase dicha en el clip con más «hype» (exclamaciones, palabras de jugada…), para el gancho."""
    if not words:
        return ""
    import subtitles
    chunks, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > 0.7 or len(cur) >= max_words):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    best, score = "", 0.0
    for ch in chunks:
        txt = " ".join(re.sub(r"\s+", " ", w["text"]).strip() for w in ch).strip()
        if len(txt) < 6:
            continue
        sc = sum(1.0 for w in ch if subtitles.auto_highlight(w["text"])) + (1.5 if "!" in txt else 0)
        sc += min(len(ch), 5) * 0.1
        if sc > score:
            best, score = txt, sc
    return best if score >= 1.0 else ""


def kind_key(c) -> tuple[str, int]:
    r = c.round
    if r.my_kills >= 5:
        return "ACE", 0
    if r.my_kills == 4:
        return "4K", 0
    if r.my_kills >= 3:
        return "3K", 0
    return "CLUTCH", r.clutch_vs


def clip_texts(c, words: list[dict] | None = None, why: list[str] | None = None, channel: str = "") -> dict:
    r, m = c.round, c.match
    key, n = kind_key(c)
    seed = f"{m.match_id}-{r.index}"
    agent, mp = m.agent or "", m.map_name or ""
    title = _pick(TITLES[key], seed).format(agent=agent, map=mp, n=n)
    if key != "CLUTCH" and r.won and r.clutch_vs >= 2:
        title += f" (clutch 1v{r.clutch_vs})"
    phrase = best_phrase(words)
    tags = list(BASE_TAGS)
    tags += KIND_TAGS.get(key, ["#clutch"])
    if key != "CLUTCH" and r.won and r.clutch_vs >= 1:
        tags.append("#clutch")
    tags += [t for t in (_tag(agent), _tag(mp)) if t]
    tags += ["#valorantespaña", "#fps", "#gaming"]
    seen, uniq = set(), []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    lines = []
    if phrase:
        lines.append(f"«{phrase}»")
    lines.append(f"{c.kind} con {agent} en {mp} · Ronda {r.index + 1}" + (f" · {m.score}" if m.score else ""))
    if channel:
        lines.append(f"🔴 En directo en twitch.tv/{channel}")
    lines.append(f"📸 {INSTAGRAM}")
    desc = "\n".join(lines)
    # TikTok: solo título y 5 hashtags (ahí la descripción es el propio título)
    tk = ["#valorant", (KIND_TAGS.get(key) or ["#clutch"])[0], _tag(agent) or "#valorantclips", "#valorantclips",
          "#parati"]
    tk_uniq = []
    for t in tk + ["#gaming", "#fps"]:
        if t and t not in tk_uniq:
            tk_uniq.append(t)
    return {
        "title": title[:95],
        "description": desc,
        "hashtags": uniq[:10],
        "tiktok": {"title": title[:95], "hashtags": tk_uniq[:5]},
        "why": why or [],
    }


def video_texts(clips, date_txt: str = "", channel: str = "") -> dict:
    """Para el vídeo horizontal con todas las rondas (YouTube)."""
    groups, seen = [], set()
    for c in clips:
        if c.group not in seen:
            seen.add(c.group)
            groups.append(c)
    cnt = {}
    for c in groups:
        k, _ = kind_key(c)
        cnt[k] = cnt.get(k, 0) + 1
    parts = [f"{cnt[k]} {k if k != 'CLUTCH' else 'clutch'}{'s' if k in ('ACE', 'CLUTCH') and cnt[k] > 1 else ''}"
             for k in ("ACE", "4K", "3K", "CLUTCH") if cnt.get(k)]
    agents = sorted({c.match.agent for c in groups if c.match.agent})
    maps = sorted({c.match.map_name for c in groups if c.match.map_name})
    title = "Mis mejores rondas" + (f" del directo del {date_txt}" if date_txt else "") + \
            (f" | {', '.join(parts)}" if parts else "")
    lines = [f"{len(groups)} rondas del directo: {', '.join(parts)}." if parts else ""]
    lines.append(f"Agentes: {', '.join(agents)} · Mapas: {', '.join(maps)}")
    lines.append("")
    t = 0.0
    lines.append("Capítulos:")
    for c in groups:
        d = sum(x.vod_end - x.vod_start for x in clips if x.group == c.group)
        mm, ss = divmod(int(t), 60)
        hh, mm = divmod(mm, 60)
        stamp = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
        lines.append(f"{stamp} {c.kind} · {c.match.map_name} · Ronda {c.round.index + 1}")
        t += d
    lines.append("")
    if channel:
        lines.append(f"🔴 Directos en twitch.tv/{channel}")
    lines.append(f"📸 Instagram y TikTok: {INSTAGRAM}")
    tags = list(BASE_TAGS) + [_tag(a) for a in agents[:3]] + ["#valorantespaña", "#gaming"]
    return {"title": title[:95], "description": "\n".join(x for x in lines if x is not None).strip(),
            "hashtags": [t for t in tags if t][:10]}


def words_in(words: list[dict] | None, t: float, d: float) -> list[dict]:
    if not words:
        return []
    return [{**w, "start": w["start"] - t, "end": w["end"] - t} for w in words
            if w["start"] >= t - 0.2 and w["start"] < t + d]


def tiktok_line(tx: dict) -> str:
    tk = tx.get("tiktok")
    return f"{tk['title']} {' '.join(tk['hashtags'])}" if tk else ""


def _block(name: str, tx: dict) -> str:
    tk = (f"TIKTOK (copia y pega tal cual):\n{tiktok_line(tx)}\n\n" if tx.get("tiktok") else "")
    return (f"=== {name} ===\n{tk}TÍTULO: {tx['title']}\n\nDESCRIPCIÓN (YouTube / Instagram):\n"
            f"{tx['description']}\n\nHASHTAGS: {' '.join(tx['hashtags'])}\n")


def write_clip_txt(video_path: str, tx: dict) -> str:
    p = os.path.splitext(video_path)[0] + ".txt"
    with open(p, "w", encoding="utf-8") as f:
        if tx.get("tiktok"):
            f.write(f"TIKTOK:\n{tiktok_line(tx)}\n\n---\n\n")
        f.write(f"{tx['title']}\n\n{tx['description']}\n\n{' '.join(tx['hashtags'])}\n")
    return p


def write_all(folder: str, video: dict | None, items: list[dict]) -> str:
    """items: [{'file', 'vertical', 'label', 'score', 'texts'}] ya ordenados (mejor primero)."""
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "publicar.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("TEXTOS PARA PUBLICAR (copia y pega). El mejor clip va primero.\n\n")
        if video:
            f.write(_block("VÍDEO COMPLETO (YouTube)", video) + "\n")
        for i, it in enumerate(items):
            f.write(_block(f"{i + 1}. {it['label']}  ·  {os.path.basename(it.get('vertical') or it['file'])}",
                           it["texts"]) + "\n")
    data = {"video": video, "clips": [{**it, "file": os.path.relpath(it["file"], folder),
                                       "vertical": os.path.relpath(it["vertical"], folder) if it.get("vertical")
                                       else None} for it in items]}
    with open(os.path.join(folder, "publicar.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return out
