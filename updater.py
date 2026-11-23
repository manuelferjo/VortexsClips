"""
Actualizador de Vortexs Clips.
Mira la última versión publicada en GitHub Releases y, si es más nueva,
descarga el zip y sustituye los archivos del programa.
Tu configuración (clave, sincronizaciones…) está en %APPDATA%\\VortexsClips y no se toca.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from version import REPO, VERSION

APP_DIR = os.path.dirname(os.path.abspath(__file__))
API = f"https://api.github.com/repos/{REPO}/releases/latest"
KEEP = {"ffmpeg"}   # carpetas locales que nunca se borran ni se pisan


def _ver(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    out = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def check() -> dict | None:
    """Devuelve {'version', 'url', 'notes'} si hay una versión más nueva; si no, None."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "VortexsClips-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        rel = json.loads(r.read().decode("utf-8"))
    tag = rel.get("tag_name", "")
    if not tag or _ver(tag) <= _ver(VERSION):
        return None
    asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".zip")), None)
    url = asset["browser_download_url"] if asset else rel.get("zipball_url")
    return {"version": tag.lstrip("vV"), "url": url, "notes": rel.get("body") or ""}


def apply(url: str, progress=None) -> None:
    """Descarga el zip de la versión nueva y copia sus archivos encima del programa."""
    tmp = tempfile.mkdtemp(prefix="vortexsclips_upd_")
    try:
        zpath = os.path.join(tmp, "update.zip")
        req = urllib.request.Request(url, headers={"User-Agent": "VortexsClips-Updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        ex = os.path.join(tmp, "x")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(ex)
        # localizar la carpeta que contiene app.py dentro del zip
        src = None
        for root, _dirs, files in os.walk(ex):
            if "app.py" in files and "core.py" in files:
                src = root
                break
        if not src:
            raise RuntimeError("El paquete descargado no parece una versión de Vortexs Clips.")
        for name in os.listdir(src):
            if name in KEEP or name.startswith("."):
                continue
            s = os.path.join(src, name)
            d = os.path.join(APP_DIR, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        shutil.rmtree(os.path.join(APP_DIR, "__pycache__"), ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart() -> None:
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.isfile(pyw):
            exe = pyw
    subprocess.Popen([exe, os.path.join(APP_DIR, "app.py")], cwd=APP_DIR)
    os._exit(0)
