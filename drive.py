"""
Subir la carpeta de un directo a Google Drive, con la misma estructura que en el PC:
  <carpeta compartida>/<subcarpeta, p. ej. «Vortexs Clips»>/<dd-mm-aaaa>/<todo lo de la carpeta del directo>

Usa rclone (programa libre para subir a Drive). Se descarga solo la primera vez, y para conectar
con tu cuenta se abre el navegador para que inicies sesión en Google: el programa nunca ve tu contraseña,
solo guarda el permiso (token) en tu PC, en %LOCALAPPDATA%\\VortexsClips\\drive.conf.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile

import core

RCLONE_ZIP = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
REMOTE = "vcdrive"
DEFAULT_LINK = ""
# el enlace de ejemplo que se puso al principio es una carpeta de UN día (24-11-2025), no la de
# «Vortexs Clips»: si es el que hay guardado, se pide el enlace bueno
EXAMPLE_DAY_ID = "1RoEymSQfnisgH6HiiMfK5AeAjw-q2dbZ"
DEFAULT_SUB = ""
# lo que nunca se sube: trozos a medio hacer y temporales
EXCLUDE = [".vod", "* (partes en curso)/**", "* (vertical en curso)/**", "*.tmp.mp4", "*.part", "*.pkl", "desktop.ini",
           "Thumbs.db"]


def conf_path() -> str:
    return os.path.join(core.data_dir(), "drive.conf")


def rclone_path() -> str | None:
    import shutil
    d = os.path.join(core.data_dir(), "rclone")
    for root, _dirs, files in os.walk(d) if os.path.isdir(d) else []:
        for n in ("rclone.exe", "rclone"):
            if n in files:
                return os.path.join(root, n)
    return shutil.which("rclone")


def ensure_rclone(progress=None) -> str:
    p = rclone_path()
    if p:
        return p
    import subtitles
    d = os.path.join(core.data_dir(), "rclone")
    os.makedirs(d, exist_ok=True)
    z = os.path.join(d, "rclone.zip")
    subtitles._download(RCLONE_ZIP, z, progress, "Descargando el programa de subida…")
    with zipfile.ZipFile(z) as f:
        f.extractall(d)
    os.remove(z)
    p = rclone_path()
    if not p:
        raise RuntimeError("No se pudo preparar el programa de subida (rclone).")
    return p


def folder_id(link: str) -> str:
    """El identificador de la carpeta a partir del enlace de Drive (o el propio id)."""
    link = (link or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", link) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", link)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", link):
        return link
    raise ValueError("Ese enlace no parece de una carpeta de Google Drive.")


def is_connected() -> bool:
    try:
        with open(conf_path(), encoding="utf-8") as f:
            t = f.read()
        return f"[{REMOTE}]" in t and "access_token" in t
    except OSError:
        return False


def connect(timeout: int = 600) -> None:
    """Abre el navegador para iniciar sesión en Google y guarda el permiso. Tarda lo que tardes en aceptar."""
    exe = ensure_rclone()
    r = subprocess.run([exe, "authorize", "drive"], capture_output=True, text=True, timeout=timeout,
                       **core._no_window())
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    token = _parse_token(out)
    if not token:
        raise RuntimeError("No se completó el inicio de sesión en Google.\n\n" + out.strip()[-400:])
    with open(conf_path(), "w", encoding="utf-8") as f:
        f.write(f"[{REMOTE}]\ntype = drive\nscope = drive\ntoken = {json.dumps(token)}\n")


def _parse_token(out: str) -> dict | None:
    """El permiso que imprime «rclone authorize»: JSON tal cual (versiones antiguas) o en base64."""
    import base64
    m = re.search(r"--->\s*(.+?)\s*<---", out, re.S)
    blob = m.group(1).strip() if m else ""
    cands = [blob]
    m2 = re.search(r"(\{\"access_token\".*?\})", out, re.S)
    if m2:
        cands.append(m2.group(1))
    for c in cands:
        if not c:
            continue
        for txt in (c, None):
            if txt is None:
                try:
                    txt = base64.b64decode(c + "=" * (-len(c) % 4)).decode("utf-8")
                except Exception:
                    continue
            try:
                d = json.loads(txt)
            except ValueError:
                continue
            if isinstance(d, dict) and "token" in d:          # {"token": "<json del permiso>"}
                d = d["token"]
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except ValueError:
                        continue
            if isinstance(d, dict) and "access_token" in d:
                return d
    return None


def disconnect() -> None:
    try:
        os.remove(conf_path())
    except OSError:
        pass


def _base_args(exe: str, root_id: str) -> list[str]:
    return [exe, "--config", conf_path(), "--drive-root-folder-id", root_id]


def dest_path(sub: str, date_txt: str) -> str:
    sub = (sub or "").strip().strip("/")
    return f"{REMOTE}:" + "/".join(x for x in (sub, date_txt) if x)


def upload(folder: str, date_txt: str, link: str, sub: str = DEFAULT_SUB, progress=None, should_stop=None) -> str:
    """Sube el contenido de `folder` a <link>/<sub>/<date_txt>/ (crea las carpetas que falten; lo que ya
    está subido igual no se vuelve a subir). Devuelve la ruta en Drive."""
    if not is_connected():
        raise RuntimeError("Primero conecta tu cuenta de Google Drive (pestaña Automático).")
    if not os.path.isdir(folder):
        raise RuntimeError(f"No encuentro la carpeta:\n{folder}")
    exe = ensure_rclone()
    rid = folder_id(link)
    dst = dest_path(sub, date_txt)
    cmd = _base_args(exe, rid) + ["copy", folder, dst, "--stats", "1s", "--stats-one-line", "-v",
                                  "--transfers", "4", "--drive-chunk-size", "64M", "--retries", "5"]
    for e in EXCLUDE:
        cmd += ["--exclude", e]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", **core._no_window())
    tail = []
    for line in p.stdout:
        tail = (tail + [line.rstrip()])[-15:]
        if should_stop and should_stop():
            p.kill()
            raise RuntimeError("Cancelado.")
        m = re.search(r"([\d.]+\s*\w*B)\s*/\s*([\d.]+\s*\w*B),\s*(\d+)%", line)
        if m and progress:
            progress(int(m.group(3)) / 100.0, f"Subiendo a Drive… {m.group(1)} de {m.group(2)}")
    p.wait()
    if p.returncode != 0:
        msg = "\n".join(tail)
        if "notFound" in msg or "File not found" in msg:
            raise RuntimeError("No encuentro la carpeta de Drive del enlace (o tu cuenta no tiene permiso para "
                               "escribir en ella).")
        raise RuntimeError("No se pudo subir a Drive:\n\n" + msg[-600:])
    return dst.split(":", 1)[1]
