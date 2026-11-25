"""
Subtítulos automáticos estilo TikTok de Vortexs.

- Transcribe con whisper.cpp en tu PC (sin internet después de la primera vez):
  el programa y el modelo se descargan solos la primera vez (~200 MB).
- Palabras en grande, blancas con borde negro, en minúsculas, de 1 a 3 palabras
  a la vez, centradas justo en el borde inferior de la cámara (como en tus TikToks).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile

import core

# versión fija que sí publica los programas para Windows (las últimas solo traen el código)
WHISPER_ZIP = "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.2/whisper-bin-x64.zip"
# versión para tarjetas NVIDIA (trae sus librerías CUDA: ~436 MB, se baja una vez y solo si hay NVIDIA)
WHISPER_GPU_ZIP = "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.2/whisper-cublas-12.4.0-bin-x64.zip"
MODEL_NAME = "ggml-small-q5_1.bin"
MODEL_URLS = [
    # copia en GitHub (funciona en redes donde HuggingFace está bloqueado)
    f"https://github.com/manuelferjo/VortexsClips/releases/download/modelo-voz/{MODEL_NAME}",
    f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}",
]
# frases que el transcriptor se inventa en silencios (se buscan como FRASE, no palabra suelta:
# antes se tiraba cualquier «subtítulos» aunque lo dijeras de verdad)
HALLUCINATIONS = (r"subt[ií]tulos (realizados|hechos|creados|por)( por)?( la comunidad)?( de)?( amara\.org)?",
                  r"(la comunidad de )?amara\.org", r"suscr[ií]bete al canal", r"gracias por ver(lo| el v[ií]deo)?")


def drop_hallucinations(words: list[dict]) -> list[dict]:
    spans, pos, parts = [], 0, []
    for w in words:
        t = re.sub(r"[¡!¿?,;:\"]", "", w["text"]).strip().lower()
        spans.append((pos, pos + len(t)))
        parts.append(t)
        pos += len(t) + 1
    joined = " ".join(parts)
    bad = set()
    for pat in HALLUCINATIONS:
        for mt in re.finditer(pat, joined):
            for i, (a, b) in enumerate(spans):
                if a < mt.end() and b > mt.start():
                    bad.add(i)
    return [w for i, w in enumerate(words) if i not in bad]


def whisper_dir() -> str:
    d = os.path.join(core.data_dir(), "whisper")
    os.makedirs(d, exist_ok=True)
    return d


def _find_cli() -> str | None:
    # 1) el que viene dentro del instalador; 2) el descargado
    for base in (os.path.join(core.app_dir(), "whisper"), whisper_dir()):
        for root, _d, files in os.walk(base):
            for name in ("whisper-cli.exe", "main.exe"):
                if name in files:
                    return os.path.join(root, name)
    for root, _d, files in os.walk(whisper_dir()):
        for name in ("whisper-cli.exe", "main.exe", "whisper-cli", "main"):
            if name in files:
                return os.path.join(root, name)
    return shutil.which("whisper-cli")


def _download(url: str, dst: str, progress=None, label=""):
    req = urllib.request.Request(url, headers={"User-Agent": "VortexsClips"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress and total:
                progress(done / total, f"{label} {done / 2**20:.0f} de {total / 2**20:.0f} MB")
    os.replace(dst + ".part", dst)


# ---------------------------------------------------------------- transcribir con la gráfica (NVIDIA)
_NVIDIA = {}


def _quiet_errors():
    """Que Windows no saque ventanas de «falta tal DLL» si la versión para NVIDIA no puede arrancar
    (el programa la detecta y sigue con el procesador)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except Exception:
            pass


_quiet_errors()


def has_nvidia() -> bool:
    if "v" in _NVIDIA:
        return _NVIDIA["v"]
    ok = False
    if os.name == "nt":
        smi = shutil.which("nvidia-smi") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                                         "System32", "nvidia-smi.exe")
        if os.path.isfile(smi):
            try:
                r = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=15, **core._no_window())
                ok = r.returncode == 0 and "GPU" in r.stdout
            except Exception:
                ok = False
    _NVIDIA["v"] = ok
    return ok


def gpu_dir() -> str:
    return os.path.join(core.data_dir(), "whisper-gpu")


def _gpu_broken_flag() -> str:
    return os.path.join(gpu_dir(), "no_funciona.txt")


def find_gpu_cli() -> str | None:
    d = gpu_dir()
    if not os.path.isdir(d) or os.path.isfile(_gpu_broken_flag()):
        return None
    for root, _d, files in os.walk(d):
        if "whisper-cli.exe" in files and ".completo" in files:
            return os.path.join(root, "whisper-cli.exe")
    return None


_GPU_LOCK = threading.Lock()


def ensure_gpu_whisper(progress=None) -> str | None:
    """Baja (una vez) la versión para NVIDIA. None si no hay NVIDIA o no se puede."""
    if not has_nvidia() or os.path.isfile(_gpu_broken_flag()):
        return None
    cli = find_gpu_cli()
    if cli:
        return cli
    if not _GPU_LOCK.acquire(blocking=False):
        return None                      # ya se está bajando en segundo plano
    try:
        os.makedirs(gpu_dir(), exist_ok=True)
        z = os.path.join(gpu_dir(), "gpu.zip")
        _download(WHISPER_GPU_ZIP, z, progress, "Descargando el transcriptor para tu gráfica NVIDIA (una vez):")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(gpu_dir())
        os.remove(z)
        for root, _d, files in os.walk(gpu_dir()):
            if "whisper-cli.exe" in files:
                open(os.path.join(root, ".completo"), "w").close()
                return os.path.join(root, "whisper-cli.exe")
        return None
    except Exception:
        return None
    finally:
        _GPU_LOCK.release()


def prefetch_gpu_async():
    """Al abrir el programa en un PC con NVIDIA: bajar la versión rápida en segundo plano."""
    if os.name != "nt" or find_gpu_cli() or os.path.isfile(_gpu_broken_flag()):
        return
    threading.Thread(target=lambda: has_nvidia() and ensure_gpu_whisper(), daemon=True).start()


def ensure_whisper(progress=None) -> tuple[str, str]:
    """Devuelve (programa, modelo); los descarga la primera vez."""
    cli = _find_cli()
    if cli is None:
        if os.name != "nt":
            raise RuntimeError("Falta whisper-cli.")
        z = os.path.join(whisper_dir(), "whisper.zip")
        _download(WHISPER_ZIP, z, progress, "Descargando transcriptor (solo la primera vez):")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(whisper_dir())
        os.remove(z)
        cli = _find_cli()
        if not cli:
            raise RuntimeError("No se pudo preparar el transcriptor.")
    model = os.path.join(whisper_dir(), MODEL_NAME)
    if not os.path.isfile(model):
        errors = []
        for url in MODEL_URLS:
            try:
                _download(url, model, progress, "Descargando modelo de voz (solo la primera vez):")
                break
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e}")
        else:
            raise RuntimeError("No se pudo descargar el modelo de voz (" + " · ".join(errors) + ")")
    return cli, model


def transcribe(video: str, progress=None, should_stop=None, threads: int | None = None) -> list[dict]:
    """Lista de palabras [{'start','end','text'}] en segundos, en español."""
    cli, model = ensure_whisper(progress)
    tmp = tempfile.mkdtemp(prefix="vortexsclips_sub_")
    try:
        wav = os.path.join(tmp, "audio.wav")
        ffmpeg = core.find_tool("ffmpeg")
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-vn",
                            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
                           capture_output=True, text=True, **core._no_window())
        if r.returncode != 0:
            raise RuntimeError("No se pudo sacar el audio: " + r.stderr[-300:])
        if progress:
            progress(None, "Transcribiendo lo que dices (subtítulos)…")
        threads = str(threads or max(2, (os.cpu_count() or 4) - 1))
        base = os.path.join(tmp, "out")
        jpath = base + ".json"
        # 1º con alineación precisa por palabra (DTW) y sin arrastrar contexto entre trozos
        #    (en audios largos el contexto hace que los tiempos se vayan desplazando);
        # si esta versión del transcriptor no lo admite, como antes
        out_tail = []
        # (la alineación DTW no funciona con «flash attention», que viene activada por defecto: -nfa)
        attempts = []
        gcli = find_gpu_cli() if has_nvidia() else None
        if gcli:                     # 1º con la gráfica NVIDIA (varias veces más rápido)
            attempts.append((gcli, ["-mc", "0", "-nfa", "-dtw", DTW_PRESET, "-ojf"]))
        for extra in (["-mc", "0", "-nfa", "-dtw", DTW_PRESET, "-ojf"], ["-mc", "0", "-dtw", DTW_PRESET, "-ojf"],
                      ["-mc", "0", "-oj"], ["-oj"]):
            attempts.append((cli, extra))
        for exe, extra in attempts:
            try:
                os.remove(jpath)
            except OSError:
                pass
            common = [exe, "-m", model, "-f", wav, "-l", "es", "-t", threads, "-ml", "1", "-sow", "-np",
                      "-of", base]
            if exe == gcli and progress:
                progress(None, "Transcribiendo con tu gráfica NVIDIA (subtítulos)…")
            try:
                p = subprocess.Popen(common + extra, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", cwd=os.path.dirname(exe) or None,
                                     **core._no_window())
            except OSError as e:
                out_tail = [str(e)]
                if exe == gcli:
                    try:
                        with open(_gpu_broken_flag(), "w", encoding="utf-8") as f_:
                            f_.write(str(e))
                    except OSError:
                        pass
                continue
            out_tail = []
            for line in p.stdout:
                out_tail = (out_tail + [line])[-10:]
                if should_stop and should_stop():
                    p.kill()
                    raise RuntimeError("Cancelado.")
            p.wait()
            if p.returncode == 0 and os.path.isfile(jpath):
                global LAST_MODE
                LAST_MODE = ("GPU " if exe == gcli else "") + " ".join(extra)
                break
            if exe == gcli:
                # la versión para NVIDIA no funciona en este PC (driver antiguo…): no se vuelve a intentar
                try:
                    with open(_gpu_broken_flag(), "w", encoding="utf-8") as f_:
                        f_.write("".join(out_tail)[-2000:])
                except OSError:
                    pass
        if not os.path.isfile(jpath):
            raise RuntimeError("El transcriptor falló:\n" + "".join(out_tail)[-400:])
        with open(jpath, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        global LAST_RAW
        LAST_RAW = data
        return parse_whisper_json(data)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


LAST_MODE = ""
LAST_RAW: dict = {}
DTW_PRESET = "small"          # el modelo es «small» (cuantizado q5_1)
MAX_WORD_S = 0.9              # una palabra hablada casi nunca dura más
MIN_WORD_S = 0.12


def _seg_dtw_start(seg: dict) -> float | None:
    """Inicio de la palabra según la alineación DTW (si el transcriptor la calculó)."""
    for tk in seg.get("tokens") or []:
        txt = (tk.get("text") or "").strip()
        if not txt or txt.startswith("[_") or txt.startswith("<|"):
            continue
        t = tk.get("t_dtw")
        if isinstance(t, (int, float)) and t >= 0:
            return t / 100.0           # en centésimas
        return None
    return None


def parse_whisper_json(data: dict) -> list[dict]:
    words = []
    for seg in data.get("transcription", []):
        text = (seg.get("text") or "").strip()
        off = seg.get("offsets") or {}
        if not text or text.startswith("[") or text.startswith("("):
            continue
        a, b = off.get("from"), off.get("to")
        if a is None or b is None:
            continue
        w = {"start": a / 1000.0, "end": max(b, a + 120) / 1000.0, "text": text, "raw": text}
        d = _seg_dtw_start(seg)
        if d is not None and abs(d - w["start"]) < 6.0:
            w["start"] = d
            w["_dtw"] = True
        words.append(w)
    return fix_timing(drop_hallucinations(words))


# ---------------------------------------------------------------- diccionario y ajuste de tiempo
def norm(t: str) -> str:
    """Forma para comparar: minúsculas y sin signos."""
    return re.sub(r"\s+", " ", re.sub(r"[.,;:¡!¿?…\"'()]", "", t or "").lower()).strip()


def apply_dict(words: list[dict], d: dict | None) -> list[dict]:
    """
    Corrige con tu diccionario (lo que has corregido otras veces): {"vortex": "Vortexs", "tres kills": "3K"}.
    Admite expresiones de hasta 4 palabras (se juntan en una). Conserva el signo final de la original.
    """
    if not words or not d:
        return words
    dd = {norm(k): v for k, v in d.items() if norm(k)}
    maxn = min(4, max((len(k.split()) for k in dd), default=1))
    out, i = [], 0
    while i < len(words):
        hit = None
        for n in range(min(maxn, len(words) - i), 0, -1):
            key = " ".join(norm(w["text"]) for w in words[i:i + n])
            if key in dd:
                hit = (n, dd[key])
                break
        if not hit:
            out.append(words[i])
            i += 1
            continue
        n, rep = hit
        grp = words[i:i + n]
        tail = re.search(r"[.,;:!?…]+$", grp[-1]["text"].strip())
        txt = rep + (tail.group(0) if tail and not re.search(r"[.,;:!?…]$", rep) else "")
        nw = {**grp[0], "text": txt, "end": grp[-1]["end"]}
        if any("hl" in g for g in grp):
            nw["hl"] = next(g["hl"] for g in grp if "hl" in g)
        out.append(nw)
        i += n
    return out


def shift(words: list[dict], s: float) -> list[dict]:
    """Mueve todos los subtítulos s segundos (positivo = salen más tarde)."""
    if not words or not s:
        return words
    return [{**w, "start": max(0.0, w["start"] + s), "end": max(0.05, w["end"] + s)} for w in words]


def fix_timing(words: list[dict]) -> list[dict]:
    """
    Corrige los desfases típicos del transcriptor:
    - con alineación (DTW) el INICIO es fiable pero el final no: cada palabra dura hasta la siguiente,
      con tope (así no se queda una palabra suelta 0,1 s en pantalla ni se mete en un silencio);
    - sin alineación, tras una pausa la palabra «empieza» al final de la pausa anterior (el texto
      salía antes de decirlo): si dura más de lo normal, se recorta por el principio;
    - tiempos que se solapan o van hacia atrás.
    """
    ws = sorted((dict(w) for w in words), key=lambda x: x["start"])
    for i, w in enumerate(ws):
        dtw = w.pop("_dtw", False)
        nxt = ws[i + 1]["start"] if i + 1 < len(ws) else None
        if dtw:
            w["end"] = min(nxt, w["start"] + MAX_WORD_S) if nxt is not None else w["start"] + 0.6
        elif w["end"] - w["start"] > MAX_WORD_S:
            w["start"] = w["end"] - MAX_WORD_S
    out = []
    for w in ws:
        if out and w["start"] < out[-1]["start"] + 0.02:
            w["start"] = out[-1]["start"] + 0.02
        if out and out[-1]["end"] > w["start"]:
            out[-1]["end"] = max(out[-1]["start"] + MIN_WORD_S, w["start"])
        w["end"] = max(w["end"], w["start"] + MIN_WORD_S)
        out.append(w)
    return out


# ------------------------------------------------------------------ ASS
# Colores del canal en formato ASS (&HBBGGRR&)
ASS_YELLOW = "&H1FD2FF&"     # #FFD21F
ASS_PURPLE = "&HFFA6C7&"     # #C7A6FF
ASS_WHITE = "&HFFFFFF&"
# palabras que suelen merecer color en un clip de VALORANT
HYPE = {
    "ace", "kill", "kills", "headshot", "head", "clutch", "one", "tap", "onetap", "spray", "flick", "wallbang",
    "brutal", "bestia", "increíble", "increible", "locura", "madre", "mía", "mia", "dios", "vamos", "venga",
    "toma", "tomaa", "ahí", "ahi", "boom", "pum", "gg", "ez", "easy", "fácil", "facil", "limpio", "limpia",
    "uno", "dos", "tres", "cuatro", "cinco", "último", "ultimo", "operator", "op", "sheriff", "vandal",
    "phantom", "jett", "reyna", "phoenix", "sova", "chamber", "hush", "raze", "neon", "yoru", "omen",
    "victoria", "ganamos", "ganado", "perdón", "perdon", "qué", "flipa", "flipando", "bro", "tío", "tio",
}


def auto_highlight(raw: str) -> bool:
    """¿Esta palabra merece ir en color? (exclamaciones, números, 3K/4K, palabras de jugada…)"""
    t = re.sub(r"[.,;:¡!¿?…\"'()]", "", raw).strip().lower()
    if not t:
        return False
    if "!" in raw or "¡" in raw:
        return True
    if re.fullmatch(r"\d+k?|[345]k", t):
        return True
    if t in HYPE:
        return True
    letters = re.sub(r"[^a-záéíóúñü]", "", t)
    return len(letters) >= 9


WORDS_PER_LINE = 1          # en pantalla: palabra a palabra (estilo TikTok)


def chunk_words_rich(words: list[dict], max_words: int | None = None, max_chars: int = 16, max_gap: float = 0.6,
                     highlight: bool = True):
    """
    Como chunk_words pero conservando cada palabra y su color: [(inicio, fin, [(texto, color|None)])].
    Cada palabra puede traer 'hl': 'y' (amarillo), 'p' (morado), 'none' (sin color) o nada (automático).
    Como mucho una palabra con color por frase, alternando amarillo y morado.
    """
    if max_words is None:
        max_words = WORDS_PER_LINE
    if max_words <= 1:
        max_chars = 10 ** 6            # una palabra (o lo que juntaste en una) por pantalla
    chunks, cur = [], []
    for w in words:
        t = re.sub(r"\s+", " ", w["text"]).strip()
        if not t:
            continue
        if cur:
            joined = " ".join(x["text"] for x in cur) + " " + t
            gap = w["start"] - cur[-1]["end"]
            if len(cur) >= max_words or len(joined) > max_chars or gap > max_gap:
                chunks.append(cur)
                cur = []
        cur.append({**w, "text": t})
        if re.search(r"[.!?…]$", t):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out, alt = [], 0
    for c in chunks:
        a, b = c[0]["start"], max(c[-1]["end"], c[0]["start"] + 0.35)
        items, used = [], False
        for x in c:
            clean = re.sub(r"[.,;:¡!¿?…\"]", "", x["text"].lower()).strip()
            if not clean:
                continue
            hl = x.get("hl")
            col = None
            if hl == "y":
                col = ASS_YELLOW
            elif hl == "p":
                col = ASS_PURPLE
            elif hl in (None, "", "auto") and highlight and not used and auto_highlight(x["text"]):
                col = ASS_YELLOW if alt % 2 == 0 else ASS_PURPLE
                alt += 1
            if col:
                used = True
            items.append((clean, col))
        if items:
            out.append((a, b, items))
    fixed = []
    for i, (a, b, it) in enumerate(out):
        if i + 1 < len(out):
            b = min(b, out[i + 1][0])
        if b - a > 0.05:
            fixed.append((a, b, it))
    return fixed


def chunk_words(words: list[dict], max_words: int = 3, max_chars: int = 16, max_gap: float = 0.6):
    """Agrupa palabras en frases cortas de 1 a 3 palabras (ritmo TikTok)."""
    chunks, cur = [], []
    for w in words:
        t = re.sub(r"\s+", " ", w["text"]).strip()
        if not t:
            continue
        if cur:
            joined = " ".join(x["text"] for x in cur) + " " + t
            gap = w["start"] - cur[-1]["end"]
            if len(cur) >= max_words or len(joined) > max_chars or gap > max_gap:
                chunks.append(cur)
                cur = []
        cur.append({**w, "text": t})
        if re.search(r"[.!?…]$", t):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        a, b = c[0]["start"], max(c[-1]["end"], c[0]["start"] + 0.35)
        txt = " ".join(x["text"] for x in c).lower()
        txt = re.sub(r"[.,;:¡!¿?…\"]", "", txt).strip()
        if txt:
            out.append((a, b, txt))
    # que no se pisen
    fixed = []
    for i, (a, b, t) in enumerate(out):
        if i + 1 < len(out):
            b = min(b, out[i + 1][0])
        if b - a > 0.05:
            fixed.append((a, b, t))
    return fixed


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words: list[dict], y_center: int, path: str, offset: float = 0.0, duration: float | None = None,
              highlight: bool = True):
    """Escribe el .ass para un vídeo vertical 1080x1920. offset/duration para recortar a un clip."""
    sel = []
    for w in words:
        a, b = w["start"] - offset, w["end"] - offset
        if b <= 0 or (duration is not None and a >= duration):
            continue
        sel.append({**w, "start": max(0.0, a), "end": b if duration is None else min(duration, b)})
    lines = chunk_words_rich(sel, highlight=highlight)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,Arial Black,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for a, b, items in lines:
            parts = []
            for word, col in items:
                word = word.replace("{", "(").replace("}", ")")
                parts.append(f"{{\\c{col}}}{word}{{\\c{ASS_WHITE}}}" if col else word)
            t = " ".join(parts)
            # pequeño "pop" al aparecer, como en los TikToks
            f.write(f"Dialogue: 0,{_ts(a)},{_ts(b)},TikTok,,0,0,0,,"
                    f"{{\\pos(540,{y_center})\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}}{t}\n")
    return len(lines)


# ---------------------------------------------------------------- subtítulos con la hora del VOD
# Se transcribe por tramos del VOD (p. ej. las jugadas de cada partida en cuanto se sincroniza, mientras
# se sincronizan las demás) y se guarda con la hora del VOD. Al generar se reaprovecha lo ya hecho y solo
# se transcribe lo que falte.
SUBS_CACHE_VER = 1
_CACHE_LOCK = threading.Lock()


def _subs_cache_path(vod: str) -> str:
    import hashlib
    d = os.path.join(core.data_dir(), "subtitulos_cache")
    os.makedirs(d, exist_ok=True)
    h = hashlib.sha1(f"{os.path.abspath(vod).lower()}|{MODEL_NAME}|v{SUBS_CACHE_VER}".encode("utf-8"))
    return os.path.join(d, h.hexdigest()[:16] + ".json")


def cache_get(vod: str) -> list[dict]:
    with _CACHE_LOCK:
        try:
            with open(_subs_cache_path(vod), encoding="utf-8") as f:
                return json.load(f).get("ranges", [])
        except Exception:
            return []


def cache_put(vod: str, entries: list[dict]) -> None:
    with _CACHE_LOCK:
        p = _subs_cache_path(vod)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"vod": vod, "ranges": []}
        data["ranges"].extend(entries)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)


def _covering(entries, a, b):
    for e in entries:
        if e["a"] <= a + 0.05 and e["b"] >= b - 0.05:
            return e
    return None


def missing_ranges(vod: str, clips) -> list[tuple[float, float]]:
    entries = cache_get(vod)
    out = []
    for c in clips:
        if not _covering(entries, c.vod_start, c.vod_end):
            out.append((c.vod_start, c.vod_end))
    return out


def transcribe_ranges(vod: str, ranges: list[tuple[float, float]], progress=None, should_stop=None,
                      threads: int | None = None) -> int:
    """Transcribe esos tramos del VOD (juntos, en una pasada) y los guarda con la hora del VOD."""
    from types import SimpleNamespace as _NS
    rs = []
    for a, b in sorted(ranges):
        a = max(0.0, a)
        if b <= a:
            continue
        if rs and a <= rs[-1][1] + 1.0:
            rs[-1][1] = max(rs[-1][1], b)
        else:
            rs.append([a, b])
    if not rs:
        return 0
    tmp = tempfile.mkdtemp(prefix="vortexsclips_rng_")
    try:
        wav = os.path.join(tmp, "a.wav")
        core.render_audio(vod, [_NS(vod_start=a, vod_end=b) for a, b in rs], wav, should_stop)
        words = transcribe(wav, progress, should_stop, threads)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    cum, t = [], 0.0
    for a, b in rs:
        cum.append(t)
        t += b - a
    per = [[] for _ in rs]
    j = 0
    for w in words:
        while j + 1 < len(rs) and w["start"] >= cum[j + 1]:
            j += 1
        a, b = rs[j]
        st = a + (w["start"] - cum[j])
        en = min(b, a + (w["end"] - cum[j]))
        per[j].append({**w, "start": round(st, 3), "end": round(max(en, st + 0.05), 3)})
    cache_put(vod, [{"a": a, "b": b, "words": ws} for (a, b), ws in zip(rs, per)])
    return len(words)


def words_for_clips(vod: str, clips) -> list[dict] | None:
    """Palabras ya en el tiempo del vídeo montado (clips seguidos). None si falta algún tramo."""
    entries = cache_get(vod)
    out, t = [], 0.0
    for c in clips:
        e = _covering(entries, c.vod_start, c.vod_end)
        if e is None:
            return None
        for w in e["words"]:
            if c.vod_start <= w["start"] < c.vod_end:
                out.append({**w, "start": round(w["start"] - c.vod_start + t, 3),
                            "end": round(min(w["end"], c.vod_end) - c.vod_start + t, 3)})
        t += c.vod_end - c.vod_start
    return out
